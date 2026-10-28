"""Project-owned script/video standards shared by all creator tasks."""
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, Request
from pydantic import Field, ValidationError, field_validator

from app.core.contracts import ContractModel, Envelope, Platform
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.brief_script.script_service import authorize_task, require_task_writable
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from app.modules.video_model.evidence.secrets import cipher
from .prompts import catalog, prompt, recommendation_prompt
from .script_defaults import DEFAULT_SCRIPT_RULES, default_script_rules
from .video_components import ComponentPlan, freeze_plan

router=APIRouter(tags=['task-standards'])
from .submission_feedback import router as feedback_router
router.include_router(feedback_router)
RECOMMENDATION_READ_TIMEOUT=300
# 规则生成要读完整份 Brief 候选并要求逐条引用，思考型模型常超 5 分钟；单独放宽，不影响事实卡提取。
RULE_GENERATION_READ_TIMEOUT=600

# 事实卡注入：完整卡片时只发卡片（正文可省数万字符），否则正文与卡片一起发。
FACTS_WITH_BLOCKS_NOTE=('【Brief 事实卡】AI 从当前 Brief 解构且出处已校验的候选事实如下（仍需品牌确认）（含出处）。生成规则时产品全名、品牌名、必提/禁用词句、发布规则与数值阈值必须逐字采用事实卡的 value，不得改写、缩写或另造；结构安排、必做镜头、发布形式与禁止类要求见 publishRules 与 freeform。品牌关系见 brandEntities：primary 是主推品牌，related 是母品牌或关联品牌；同一实体的 name 与 aliases 是同一品牌的可用名称，不得把合法英文名当作品名错误或竞品，不要求中英文同时出现。不同实体不可混并。params 直接由事实卡填充；事实卡与 Brief 原文冲突时以原文为准并写入 warnings。')
FACTS_ONLY_NOTE=('【Brief 事实卡】本次只提供事实卡，不提供 Brief 原文。事实卡是逐字核对过出处的摘要：产品全名、品牌名、必提/禁用词句、发布规则与数值阈值必须逐字采用 value，不得改写、缩写或另造；结构安排、必做镜头、开场要求、发布形式与禁止类要求从 publishRules 与 freeform 取，不要因为分组名不含“要求”就忽略。source_block_id 与 source_quote 只能逐字复制事实卡条目里的 blockId 与 quote，不得改写引文、不得引用卡片以外的内容；卡片没覆盖的要求不要凭空补规则，写进 warnings 说明缺哪类要求、需品牌补充。品牌关系见 brandEntities：primary 是主推品牌，related 是母品牌或关联品牌；同一实体的 name 与 aliases 是同一品牌的可用名称，不得把合法英文名当作品名错误或竞品，不要求中英文同时出现。不同实体不可混并。params 直接由事实卡填充。')


class RuleThreshold(ContractModel):
    key: str = Field(min_length=1,max_length=50)
    value: float


class RuleParams(ContractModel):
    """规则携带的机器可读事实：生成时从 Brief 固化，确认时冻结，判断时由平台逐字核对。
    有了它，规则不必在判据里写「对照品牌资料」这类外部依赖。"""
    names: list[str] = Field(default_factory=list,max_length=30)
    terms: list[str] = Field(default_factory=list,max_length=50)
    thresholds: list[RuleThreshold] = Field(default_factory=list,max_length=20)

    @field_validator('names','terms')
    @classmethod
    def stripped(cls,value):
        return [item.strip() for item in value if item.strip()]


class RuleSourceReference(ContractModel):
    ref: str = Field(max_length=100)
    block_id: str = Field(min_length=1,max_length=100)
    quote: str = Field(min_length=1,max_length=3000)


class StandardRule(ContractModel):
    text: str = Field(min_length=1,max_length=2000)
    criterion: str = Field(min_length=1,max_length=20000)
    category: str = Field(default='内容',min_length=1,max_length=50)
    importance: Literal['must','suggested']='must'
    source_block_id: str = Field(default='',max_length=100)
    source_quote: str = Field(default='',max_length=3000)
    origin: Literal['template','ai','manual']='manual'
    source_references: list[RuleSourceReference] = Field(default_factory=list,max_length=20)
    citation_status: Literal['verified','pending'] | None = None
    citation_note: str | None = Field(default=None,max_length=2000)
    citation_confirmed: bool | None = None
    component_plan: ComponentPlan | None=None
    params: RuleParams | None=None
    layer: Literal['baseline','brief','precise'] | None=None
    enabled: bool=True
    baseline_key: Literal['competitor','product','forbidden','script_match','identity','information_accuracy','understandability'] | None=None
    preset_key: Literal['product_count','product_duration','forbidden_expression'] | None=None

    @field_validator('text','criterion','category')
    @classmethod
    def not_blank(cls,value):
        if not value.strip():
            raise ValueError('不能为空白')
        return value.strip()


# 判据里出现这些措辞，说明规则依赖判断时已不提供的 Brief 原文。
EXTERNAL_REFERENCE_MARKERS=('对照品牌提供','品牌提供信息','对照品牌资料','参考品牌资料','与品牌资料','与品牌提供',
                            '以Brief为准','以 Brief 为准','见Brief','见 Brief','参照Brief','参照 Brief','品牌确认资料')


def self_contained(rule):
    """规则脱离 Brief 原文是否仍可判：外部引用但未内联事实（criterion 或 params）→ 不自包含。"""
    if isinstance(rule,dict):
        text=str(rule.get('text') or '');criterion=str(rule.get('criterion') or '');params=rule.get('params') or {}
    else:
        text=rule.text;criterion=rule.criterion;params=(rule.params.model_dump() if rule.params else {})
    marker=next((item for item in EXTERNAL_REFERENCE_MARKERS if item in text or item in criterion),None)
    if not marker:
        return True,None
    if any(params.get(key) for key in ('names','terms','thresholds')):
        return True,None
    return False,f'判据引用了「{marker}」，但未把具体事实写进 criterion 或 params'


def self_containment_issues(rules):
    """确认环节自检：返回不可自判的规则清单，由接口返回给品牌，不静默通过。"""
    issues=[]
    for number,rule in enumerate(rules,1):
        ok,reason=self_contained(rule)
        if not ok:
            issues.append({'ruleNumber':number,'text':(rule.text if hasattr(rule,'text') else str(rule.get('text',''))),'reason':reason})
    return issues


class StandardsConfirmation(ContractModel):
    script_rules: list[StandardRule] = Field(min_length=1,max_length=60)
    video_rules: list[StandardRule] = Field(min_length=1,max_length=60)
    image_rules: list[StandardRule] | None = Field(default=None,min_length=1,max_length=60)
    script_confirmed: bool=False
    video_confirmed: bool=False
    confirmation_note: str = Field(min_length=1,max_length=2000)
    source_version_id: str | None=None
    video_review_level: Literal['basic','custom','strict'] | None=None


class PublishTaskInput(StandardsConfirmation):
    creator_name: str = Field(min_length=1,max_length=50)
    platform: Platform
    campaign_phase: Literal['warmup','seeding','general']='general'
    acknowledge_duplicate: bool=False

    @field_validator('creator_name')
    @classmethod
    def creator_not_blank(cls,value):
        if not value.strip():
            raise ValueError('请输入达人名称')
        return value.strip()


class ReconfirmInput(StandardsConfirmation):
    task_revision: int = Field(ge=1)


class ProjectStandardsInput(StandardsConfirmation):
    revision: int = Field(ge=0)


class ProjectCreatorInput(ContractModel):
    creator_name: str = Field(min_length=1,max_length=50)
    platform: Platform
    acknowledge_duplicate: bool=False

    @field_validator('creator_name')
    @classmethod
    def valid_name(cls,value):
        if not value.strip():raise ValueError('请输入达人名称')
        return value.strip()


class RecommendationInput(ContractModel):
    source_version_id: str
    scope: Literal['script','video','image']
    config_revision: int = Field(ge=1,description='独立 Brief 解构模型配置版本，不是内容审核模型版本')
    allow_real_call: bool=False
    output_format: Literal['structured','paragraph']='structured'
    strictness: Literal['lenient','standard','strict']='lenient'
    video_rule_mode: Literal['legacy','layered','template']='legacy'
    existing_video_rules: list[StandardRule] | None = Field(default=None,max_length=60)


class ParagraphRules(ContractModel):
    rules_text: str = Field(min_length=1,max_length=20000)
    warnings: list[str] = Field(default_factory=list,max_length=40)


class RecommendedStandards(ContractModel):
    rules: list[StandardRule] = Field(min_length=1,max_length=30)
    warnings: list[str] = Field(default_factory=list,max_length=40)


class RecommendedVideoStandards(RecommendedStandards):
    # A Brief may contain publishing/operations only; do not invent video rules.
    rules: list[StandardRule] = Field(min_length=0,max_length=30)


class RecommendedImageStandards(RecommendedStandards):
    rules: list[StandardRule] = Field(min_length=0,max_length=30)


class ScriptRuleCandidate(ContractModel):
    text: str = Field(min_length=1,max_length=2000)
    criterion: str = Field(min_length=1,max_length=20000)
    category: Literal['广告法合规','平台规则合规','品牌安全','内容准确性','知识产权','口碑风险','品牌调性匹配','合作要求达成','其他']
    importance: Literal['must','suggested']
    source_refs: list[str] = Field(default_factory=list,max_length=20)
    params: RuleParams | None = None


class ScriptRuleCandidates(ContractModel):
    rules: list[ScriptRuleCandidate] = Field(default_factory=list,max_length=30)
    warnings: list[str] = Field(default_factory=list,max_length=40)


class ScriptRecommendationReport(ContractModel):
    rules: list[StandardRule] = Field(min_length=8,max_length=60)
    warnings: list[str] = Field(default_factory=list,max_length=100)


class RecommendationProviderRejected(Exception):
    def __init__(self, status):
        self.status = int(status)
        super().__init__(f'Provider HTTP {self.status}')


def provider_http_message(status):
    reason = {
        401: '认证失败，请在“Brief 解构模型”核对 API Key 与服务地址是否匹配',
        403: '访问被拒绝，请核对账号、模型权限与服务地址',
        413: '请求体过大，请检查服务商传输限制',
        429: '请求限流或额度不足，请检查服务商控制台',
    }.get(status, '请求被拒绝，请查看服务商错误响应')
    return f'模型服务返回 HTTP {status}：{reason}。未取得规则内容，无法通过重新校验恢复；未自动重试'


def project_for(db,project_id):
    project=db.execute("SELECT * FROM projects WHERE id=? AND mode='demo'",(project_id,)).fetchone()
    if not project:
        raise ApiError(404,'RESOURCE_NOT_FOUND','项目不存在')
    if project['status']=='archived':
        raise ApiError(409,'PROJECT_ARCHIVED','归档项目不能发布或推荐标准')
    return project


def source_blocks(db,project_id,source_id):
    source=db.execute('SELECT s.* FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id WHERE s.id=? AND b.project_id=?',(source_id,project_id)).fetchone()
    if not source:
        raise ApiError(404,'RESOURCE_NOT_FOUND','Brief来源不属于当前项目')
    if source['extraction_status']!='ready':
        raise ApiError(409,'BRIEF_NOT_READY','Brief尚无可用文本，请先完成文件解析')
    rows=db.execute('SELECT * FROM brief_text_blocks WHERE source_version_id=? ORDER BY ordinal',(source_id,)).fetchall()
    blocks=[{'id':r['id'],'text':r['text'],'pageNo':r['page_no']} for r in rows]
    from .brief_vision import resolve_material
    blocks,uncovered,_=resolve_material(db,source_id,blocks,json.loads(source['uncovered_sources_json']))
    if not blocks or sum(len(b['text']) for b in blocks)>100_000:
        raise ApiError(400,'BRIEF_INPUT_LIMIT','Brief可读文本为空或超过10万字符，请拆分文档，不会静默截断')
    return blocks,uncovered


def citation_blocks(db,project_id,source_id,rules):
    """Validate saved citations against immutable runs of this same Brief only.

    Historical OCR is available for provenance, never merged into current AI input.
    """
    blocks,_=source_blocks(db,project_id,source_id)
    requested={r.source_block_id for r in rules} | {ref.block_id for r in rules for ref in r.source_references}
    runs={m.group(1) for value in requested if (m:=re.fullmatch(r'vision:([0-9a-f]{32}):[1-9][0-9]*:(text|visual)',value))}
    known={b['id'] for b in blocks}
    if runs:
        placeholders=','.join('?' for _ in runs)
        rows=db.execute(f"SELECT id,result_json FROM brief_visual_runs WHERE project_id=? AND source_version_id=? AND status='succeeded' AND id IN ({placeholders})",(project_id,source_id,*sorted(runs))).fetchall()
        for row in rows:
            for page in json.loads(row['result_json'])['report']['pages']:
                for kind,key in [('text','text'),('visual','visualDescription')]:
                    identity=f"vision:{row['id']}:{page['pageNo']}:{kind}"
                    if identity in requested and identity not in known:
                        blocks.append({'id':identity,'text':page[key],'pageNo':page['pageNo'],'origin':'ai-visual-extraction','visionRunId':row['id']})
                        known.add(identity)
    return blocks


def _page_index(blocks,page_no):
    """同页原文按块在输入中的顺序拼接，返回原文、去空白文本及两者的位置映射。"""
    page=[b for b in blocks if b.get('pageNo')==page_no]
    parts=[];owners=[]
    for position,block in enumerate(page):
        if position:
            parts.append('\n');owners.append(block['id'])
        parts.append(block['text']);owners.extend([block['id']]*len(block['text']))
    text=''.join(parts);norm=[];mapping=[]
    for index,char in enumerate(text):
        if char.isspace():continue
        norm.append(char);mapping.append(index)
    return text,''.join(norm),mapping,owners


def _span_quote(quote,block_id,blocks,require_start=True):
    """引文若是所引用块所在页上多个原文块按序拼接后的连续片段，返回（首个覆盖块, 该页连续原文）。

    require_start=True 用于已冻结规则的复核：引文必须从被引用块开始，否则不能证明出处。
    require_start=False 只在生成阶段修复模型输出时使用——模型常引用同页相邻块，允许重新定位。
    """
    source=next((b for b in blocks if b['id']==block_id),None)
    if not source or source.get('pageNo') is None:return None
    text,norm,mapping,owners=_page_index(blocks,source['pageNo'])
    target=''.join(char for char in quote if not char.isspace())
    if len(target)<4:return None
    cursor=0
    while True:
        position=norm.find(target,cursor)
        if position<0:return None
        start=mapping[position];end=mapping[position+len(target)-1]+1
        covered=[]
        for index in range(start,end):
            if text[index].isspace():continue
            if not covered or covered[-1]!=owners[index]:covered.append(owners[index])
        if covered and len(covered)>1 and (covered[0]==block_id or not require_start):
            return covered[0],text[start:end]
        cursor=position+1


def quote_located(quote,block_id,blocks,lookup=None):
    """引文可核对：落在所引用块内，或是同页多块按序拼接后的连续片段（含跨块引文）。"""
    if not quote or not block_id:return False
    if lookup is None:lookup={b['id']:b['text'] for b in blocks}
    if block_id not in lookup:return False
    if quote in lookup[block_id]:return True
    return _span_quote(quote,block_id,blocks) is not None


def check_quotes(rules,blocks,scope=None):
    lookup={b['id']:b['text'] for b in blocks}
    invalid=[]
    for number,rule in enumerate(rules,1):
        if rule.citation_status=='pending' and not rule.enabled:
            continue
        if rule.citation_status=='pending' and not rule.citation_confirmed:
            invalid.append(number)
            continue
        if any(not quote_located(ref.quote,ref.block_id,blocks,lookup) for ref in rule.source_references):
            invalid.append(number)
            continue
        if rule.source_block_id or rule.source_quote:
            if not quote_located(rule.source_quote,rule.source_block_id,blocks,lookup):
                invalid.append(number)
    if invalid:
        label={'script':'脚本','video':'视频','image':'图文'}.get(scope,'')
        fields={f'{scope}Rules.{n-1}.sourceQuote':'引用必须逐字来自所选Brief的原文（同一块内，或同一页多个块按顺序拼接后连续）' for n in invalid} if scope else None
        raise ApiError(400,'INVALID_STANDARD_SOURCE',f'{label}规则第{"、".join(map(str,invalid))}条引用无法核对：须逐字来自所选Brief的原文（同一块内，或同一页多个块按顺序拼接后连续，包括该Brief的历史图文解析）。请核对原文或修改引用。',fields=fields,details={'scope':scope,'ruleNumbers':invalid})


def validate_recommendation(value,blocks,scope=None,facts=None):
    """Repair only provable citation formatting; manual rule saves remain strict."""
    report=(RecommendedVideoStandards if scope=='video' else RecommendedImageStandards if scope=='image' else RecommendedStandards).model_validate(value);lookup={b['id']:b for b in blocks}
    if scope=='video':
        from .video_rule_scope import filter_candidates
        excluded=filter_candidates(report,facts=facts)
        if excluded:
            report.warnings.append(system_note(f'已略过{len(excluded)}条产品参考资料或非成片要求，不重复加入视频规则。'))
        if not report.rules:
            report.warnings.append(system_note('本次没有新增适用的视频成片要求，现有规则保留。'))
    if scope and scope!='video':
        # 旧模型或 JSON Object 模式下仍可能带回分层字段：脚本/图文规则不接受三层配置，直接丢弃并说明。
        stripped=False
        for rule in report.rules:
            if rule.layer or rule.baseline_key or rule.preset_key or rule.component_plan or not rule.enabled:
                stripped=True
            rule.layer=None;rule.baseline_key=None;rule.preset_key=None;rule.component_plan=None;rule.enabled=True
        if stripped:report.warnings.append(system_note('模型返回了视频分层字段（layer/组件/启用状态），脚本与图文规则不使用三层配置，已忽略。'))
    def fact_entries(value):
        if isinstance(value,dict):
            if isinstance(value.get('value'),str) and value.get('blockId') and value.get('quote'):
                yield value
            for child in value.values():yield from fact_entries(child)
        elif isinstance(value,list):
            for child in value:yield from fact_entries(child)
    verified_facts=[f for f in fact_entries(facts) if quote_located(f['quote'],f['blockId'],blocks)]
    for number,rule in enumerate(report.rules,1):
        if not rule.source_block_id and not rule.source_quote:continue
        if not quote_located(rule.source_quote,rule.source_block_id,blocks):
            # The model may copy the normalized fact value instead of its literal quote.
            # Only a unique, source-verified fact at the same block can supply the quote.
            matches={(f['blockId'],f['quote']) for f in verified_facts
                     if f['blockId']==rule.source_block_id and f['value']==rule.source_quote}
            if len(matches)==1:
                rule.source_block_id,rule.source_quote=next(iter(matches))
                report.warnings.append(system_note(f'第{number}条已自动沿用事实卡的原文引用，规则表述保留。'))
        source=lookup.get(rule.source_block_id)
        if not source or not rule.source_quote:continue
        if rule.source_quote in source['text']:continue
        if scope in ('video','image'):
            from .vision_facts import source_quote
            canonical=source_quote(rule.source_quote,source['text'])
            if canonical is not None:
                rule.source_quote=canonical
                report.warnings.append(system_note(f'第{number}条引文空白差异已按原文还原。'))
                continue
            # A list of real excerpts is not a contiguous quote. Preserve it as
            # separate references instead of inventing a combined source span.
            parts=[part.strip() for part in rule.source_quote.splitlines() if part.strip()]
            canonical_parts=[source_quote(part,source['text']) for part in parts]
            if 2<=len(parts)<=20 and all(len(''.join(part.split()))>=4 for part in parts) and all(canonical_parts):
                references={(ref.block_id,ref.quote):ref for ref in rule.source_references}
                for index,part in enumerate(canonical_parts,1):
                    references.setdefault((rule.source_block_id,part),RuleSourceReference(ref=f'part-{index}',block_id=rule.source_block_id,quote=part))
                if len(references)<=20:
                    rule.source_references=list(references.values())
                    rule.source_quote=canonical_parts[0]
                    report.warnings.append(system_note(f'第{number}条拼接引文已拆为独立原文出处，未将片段冒充连续原文。'))
                    continue
        # A fragmented native text block may cite the intact OCR block on the same page.
        matches=[b for b in blocks if source.get('pageNo') is not None and b.get('pageNo')==source['pageNo'] and rule.source_quote in b['text']]
        if len(matches)==1:
            rule.source_block_id=matches[0]['id']
            report.warnings.append(system_note(f'第{number}条引用已定位至同页完整原文块，请核对判定口径。'))
            continue
        # Replace ellipses with the actual intervening source, never fuzzy-match words.
        parts=re.split(r'\.{3,}|…+',rule.source_quote)
        if len(parts)>1 and all(len(p.strip())>=3 for p in parts):
            cursor=0;start=None;valid=True
            for part in parts:
                index=source['text'].find(part,cursor)
                if index<0:valid=False;break
                if start is None:start=index
                cursor=index+len(part)
            expanded=source['text'][start:cursor] if valid and start is not None else ''
            if expanded and len(expanded)<=3000:
                rule.source_quote=expanded
                report.warnings.append(system_note(f'第{number}条省略引文已还原为该块连续原文，请核对。'))
                continue
        # 页内被拆成多块时，模型常把相邻块拼成一句话引用：按该页连续原文合并定位。
        span=_span_quote(rule.source_quote,rule.source_block_id,blocks,require_start=False)
        if span:
            moved=span[0]!=rule.source_block_id
            rule.source_block_id,rule.source_quote=span
            report.warnings.append(system_note(f'第{number}条引文跨了同一页的多个原文块，已按该页连续原文合并定位{"，并把出处改到覆盖该引文的首个原文块" if moved else ""}，请核对。'))
    if scope in ('video','image'):
        from .fact_scope import filter_scoped_candidates
        scoped,rejected=filter_scoped_candidates({'rules':[rule.model_dump(by_alias=True) for rule in report.rules]},facts,scope)
        if rejected:
            report.rules=[StandardRule.model_validate(rule) for rule in scoped['rules']]
            report.warnings.append(system_note(f'已略过{len(rejected)}条引用拆分后确认不属于本次材料或阶段的规则。'))
    check_quotes(report.rules,blocks)
    for rule in report.rules:rule.origin='ai'
    return report


def project_sets_for(db,project_id):
    result={}
    for scope in ('script','video'):
        row=db.execute('SELECT * FROM project_standard_sets WHERE project_id=? AND scope=? ORDER BY revision DESC LIMIT 1',(project_id,scope)).fetchone()
        result[scope]=({**dict(row),'rules':json.loads(row['rules_json'])} if row else None)
        if result[scope]:
            result[scope].pop('rules_json',None)
            if scope == 'video':
                from .video_layers import review_level
                result[scope]['videoReviewLevel'] = review_level(result[scope]['rules'])
    return result


def sets_for(db,task_id):
    row=db.execute('SELECT project_id FROM creator_tasks WHERE id=?',(task_id,)).fetchone()
    return project_sets_for(db,row['project_id']) if row else {'script':None,'video':None}


def sets_ready(db,task):
    sets=sets_for(db,task['id'])
    project=db.execute('SELECT platforms_json FROM projects WHERE id=?',(task['project_id'],)).fetchone()
    from .video_layers import active_rules
    return bool(project and task['platform'] in json.loads(project['platforms_json']) and all(sets[s] and (bool(active_rules(sets[s]['rules'])) if s=='video' else any(r['importance']=='must' and r.get('enabled',True) for r in sets[s]['rules'])) for s in ('script','video')))


def freeze_sets(db,task,payload):
    if not payload.script_confirmed or not payload.video_confirmed or not payload.confirmation_note.strip():
        raise ApiError(400,'STANDARDS_CONFIRMATION_REQUIRED','品牌必须分别审核确认脚本标准和视频标准，并填写确认意见')
    blocks=citation_blocks(db,task['project_id'],payload.source_version_id,[*payload.script_rules,*payload.video_rules,*(payload.image_rules or [])]) if payload.source_version_id else []
    from .citation_migration import migrate_citations
    citation_migrations=migrate_citations(db,task['project_id'],payload.source_version_id,
        [*payload.script_rules,*payload.video_rules,*(payload.image_rules or [])],blocks) if payload.source_version_id else {}
    if len(payload.model_dump_json())>150_000:
        raise ApiError(400,'STANDARDS_TOO_LARGE','标准总内容过大，请精简')
    identities={}
    revision=db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM project_standard_sets WHERE project_id=?',(task['project_id'],)).fetchone()[0]
    groups=[('script',payload.script_rules),('video',payload.video_rules)]
    if payload.image_rules is not None:groups.append(('image',payload.image_rules))
    for scope,rules in groups:
        if scope=='video':
            from .video_layers import LAYERS, REVIEW_LEVELS
            def selected(r):
                return r.enabled and LAYERS.index(r.layer or ('precise' if r.component_plan else 'brief')) < REVIEW_LEVELS[payload.video_review_level or 'strict']
            if any(r.layer for r in rules):
                rules=sorted(rules,key=lambda r:LAYERS.index(r.layer or ('precise' if r.component_plan else 'brief')))
            if not any(selected(r) for r in rules):
                raise ApiError(400,'VIDEO_RULES_DISABLED','至少启用一条视频规则')
            preset_keys = [r.preset_key for r in rules if r.preset_key]
            if len(preset_keys) != len(set(preset_keys)):
                raise ApiError(400, 'VIDEO_PRESET_DUPLICATE', '同一种默认产品检查只能配置一条')
            for r in rules:
                if r.preset_key:
                    kind = {'product_count': 'exposure_count', 'product_duration': 'exposure_duration', 'forbidden_expression': 'forbidden_expression'}[r.preset_key]
                    if r.layer != 'precise' or (r.component_plan and (len(r.component_plan.nodes) != 1 or r.component_plan.nodes[0].kind != kind)):
                        raise ApiError(400, 'VIDEO_PRESET_INVALID', '默认详细规则必须使用对应的次数或时长组件')
                if r.layer=='baseline' and (not r.baseline_key or r.component_plan):
                    raise ApiError(400,'VIDEO_LAYER_INVALID','基础规则须指定基础类型且不使用组件')
                if r.layer=='brief' and r.component_plan:
                    raise ApiError(400,'VIDEO_LAYER_INVALID','第二层使用文字判据，不使用组件')
                if selected(r) and r.layer=='precise' and not r.component_plan:
                    raise ApiError(400,'VIDEO_COMPONENT_REQUIRED','第三层启用前请补齐并确认组件')
        elif any(r.layer or r.baseline_key or r.preset_key for r in rules):
            raise ApiError(400,'VIDEO_LAYER_SCOPE_INVALID','三层配置只适用于视频')
        if scope!='video' and any(r.component_plan for r in rules):
            raise ApiError(400,'COMPONENT_SCOPE_INVALID','视频检查组件只能用于视频规则')
        if scope=='video' and sum(len(r.component_plan.nodes) for r in rules if selected(r) and r.component_plan)>8:
            raise ApiError(400,'COMPONENT_BUDGET_LIMIT','首版每次视频审核最多8个检查组件，为取证保留调用预算')
        if scope=='video':
            refs={ref for r in rules if selected(r) and r.component_plan for n in r.component_plan.nodes for ref in n.parameters.reference_image_ids}
            if len(refs)>8:
                raise ApiError(400,'REFERENCE_BUDGET_LIMIT','每次审核最多8张参考图')
            sizes=[db.execute('SELECT byte_size FROM video_reference_images WHERE id=? AND project_id=?',(ref,task['project_id'])).fetchone() for ref in refs]
            if any(row is None for row in sizes):
                raise ApiError(400,'REFERENCE_PROJECT_MISMATCH','检查组件的参考图不属于当前项目')
            if sum(row['byte_size'] for row in sizes)>8*1024*1024:
                raise ApiError(400,'REFERENCE_BUDGET_LIMIT','每次审核参考图合计须在8MiB以内')
        check_quotes(rules,blocks,scope)
        if scope!='video' and not any(r.importance=='must' and r.enabled for r in rules):
            raise ApiError(400,'MUST_STANDARD_REQUIRED','每套标准都至少需要一条启用中的必须项；停用规则不生效')
        identity=uuid4().hex
        frozen=[]
        for number, rule in enumerate(rules, 1):
            value=rule.model_dump(by_alias=True)
            if scope == 'video' and payload.video_review_level is not None:
                value['videoReviewLevel'] = payload.video_review_level
            if rule.preset_key is None:
                value.pop('presetKey', None)
            if rule.layer is None:
                value.pop('layer',None);value.pop('baselineKey',None)
                if rule.enabled:value.pop('enabled',None)
            if value.get('params') is None:
                value.pop('params',None)
            if rule.component_plan:
                # Validation only uses project ownership here; bytes verified again before sending.
                reference_ids={ref for node in rule.component_plan.nodes for ref in node.parameters.reference_image_ids}
                for ref in reference_ids:
                    if not db.execute('SELECT 1 FROM video_reference_images WHERE id=? AND project_id=?',(ref,task['project_id'])).fetchone():
                        raise ApiError(400,'REFERENCE_PROJECT_MISMATCH','检查组件的参考图不属于当前项目')
                value['componentPlan']=freeze_plan(rule.component_plan)
            value.update({'id':scope+'-'+uuid4().hex,'ruleNumber':number,'reviewStages':[scope],
                          'source':{'sourceVersionId':payload.source_version_id,'blockId':rule.source_block_id,'quote':rule.source_quote,'origin':rule.origin},
                          'evidencePolicy':{'preferredTools':['cut_evidence_clip'] if any(w in rule.category+rule.text for w in ('口播','字幕','音乐','声音')) else ['capture_evidence_image','cut_evidence_clip'],
                                            'instruction':rule.criterion,'maxImagesPerIssue':4,'maxClipsPerIssue':3,'maxClipMs':30000}})
            if id(rule) in citation_migrations:value['source']['citationMigration']=citation_migrations[id(rule)]
            frozen.append(value)
        table='project_image_standards' if scope=='image' else 'project_standard_sets'
        db.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)',
                   (identity,task['project_id'],scope,json.dumps(frozen,ensure_ascii=False),payload.source_version_id,payload.confirmation_note.strip(),'local-reviewer',now(),revision))
        identities[scope]=identity
    return identities


def invalidate_project(db,project_id,identities,reason='项目统一审核规则更新，需重新验收'):
    stamp=now()
    for task in db.execute('SELECT id FROM creator_tasks WHERE project_id=? AND archived_at IS NULL',(project_id,)).fetchall():
        db.execute('INSERT INTO task_invalidation_events VALUES (?,?,?,?,?,?,?)',(uuid4().hex,task['id'],'standards_reconfirmed',reason,json.dumps(identities),'demo',stamp))
    db.execute("UPDATE creator_tasks SET stage='script',revision=revision+1,updated_at=? WHERE project_id=? AND archived_at IS NULL",(stamp,project_id))


def legacy_candidates(db,project_id):
    candidates=[]
    tasks=db.execute('SELECT id,creator_name FROM creator_tasks WHERE project_id=? ORDER BY created_at,id',(project_id,)).fetchall()
    for task in tasks:
        pair={scope:db.execute('SELECT * FROM task_standard_sets WHERE task_id=? AND scope=? ORDER BY rowid DESC LIMIT 1',(task['id'],scope)).fetchone() for scope in ('script','video')}
        if not all(pair.values()):continue
        candidates.append({'taskId':task['id'],'creatorName':task['creator_name'],
            'scriptRules':json.loads(pair['script']['rules_json']),'videoRules':json.loads(pair['video']['rules_json']),
            'sourceVersionId':pair['script']['source_version_id'],'videoSourceVersionId':pair['video']['source_version_id']})
    return candidates


def comparable_rules(rules):
    result=json.loads(json.dumps(rules))
    for rule in result:
        rule.pop('id',None);rule.pop('ruleNumber',None)
        for node in (rule.get('componentPlan') or {}).get('nodes',[]):node.pop('id',None)
    return result


def adopt_legacy_project_standards(db):
    """Promote only identical complete confirmed task standards; conflicting versions need brand choice."""
    for p in db.execute("SELECT id FROM projects WHERE mode='demo' AND status!='archived'").fetchall():
        if project_sets_for(db,p['id'])['script']:continue
        candidates=legacy_candidates(db,p['id'])
        active={t['id'] for t in db.execute('SELECT id FROM creator_tasks WHERE project_id=? AND archived_at IS NULL',(p['id'],))}
        selected=[c for c in candidates if c['taskId'] in active]
        if not selected or {c['taskId'] for c in selected}!=active:continue
        signatures=[json.dumps({'script':comparable_rules(c['scriptRules']),'video':comparable_rules(c['videoRules']),
                    'source':c['sourceVersionId'],'videoSource':c['videoSourceVersionId']},sort_keys=True) for c in selected]
        if len(set(signatures))!=1 or selected[0]['sourceVersionId']!=selected[0]['videoSourceVersionId']:continue
        identities={}
        for scope,key in [('script','scriptRules'),('video','videoRules')]:
            identity=uuid4().hex;identities[scope]=identity
            db.execute('INSERT INTO project_standard_sets VALUES (?,?,?,?,?,?,?,?,1)',(identity,p['id'],scope,json.dumps(selected[0][key],ensure_ascii=False),selected[0]['sourceVersionId'],'从一致的历史达人规则迁移为项目统一规则','migration',now()))
        invalidate_project(db,p['id'],identities)


def project_standards_view(db,project_id):
    p=db.execute("SELECT * FROM projects WHERE id=? AND mode='demo'",(project_id,)).fetchone()
    if not p:raise ApiError(404,'RESOURCE_NOT_FOUND','项目不存在')
    sets=project_sets_for(db,project_id)
    revision=max((s['revision'] for s in sets.values() if s),default=0)
    from .video_layers import active_rules
    ready=bool(all(sets[scope] and (bool(active_rules(sets[scope]['rules'])) if scope=='video' else any(r['importance']=='must' and r.get('enabled',True) for r in sets[scope]['rules'])) for scope in ('script','video')))
    candidates=legacy_candidates(db,project_id) if not ready else []
    image=db.execute('SELECT * FROM project_image_standards WHERE project_id=? ORDER BY revision DESC LIMIT 1',(project_id,)).fetchone()
    image={**dict(image),'rules':json.loads(image['rules_json'])} if image else None
    if image:image.pop('rules_json',None)
    if sets['video']:
        # 事实卡就绪后确定性填充 L1 基线参数（只读派生，不覆盖库中品牌已确认值）。
        from .brief_facts import latest_facts
        from .baseline_fill import baseline_fill
        from .project_brief import current_brief
        brief=current_brief(db,project_id,include_blocks=False)
        facts=latest_facts(db,project_id,brief['sourceVersionId']) if brief else None
        if facts:baseline_fill(sets['video']['rules'],facts)
    warnings={scope:self_containment_issues(sets[scope]['rules']) for scope in ('script','video') if sets[scope]}
    if image:
        warnings['image']=self_containment_issues(image['rules'])
    from .video_rule_scope import existing_exclusions
    return {'projectId':project_id,'revision':revision,'ready':ready,'script':sets['script'],'image':image,'video':sets['video'],
            'excludedVideoRuleTexts':existing_exclusions(sets['video']['rules'] if sets['video'] else []),
            'standardWarnings':warnings,
            'legacyCandidates':candidates,'migrationMessage':'历史达人规则存在差异，请由品牌选择并确认项目统一规则' if candidates else None}


def save_project_standards(settings,project_id,payload,key):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE');project_for(db,project_id)
        scope='project-standards:'+project_id;fp=fingerprint(payload)
        prior=replay(db,scope,key,fp)
        if prior:return prior
        view=project_standards_view(db,project_id)
        if view['revision']!=payload.revision:raise ApiError(409,'REVISION_CONFLICT','项目规则已更新，请刷新后重新确认')
        warnings={'script':self_containment_issues(payload.script_rules),'video':self_containment_issues(payload.video_rules)}
        if payload.image_rules is not None:
            warnings['image']=self_containment_issues(payload.image_rules)
        identities=freeze_sets(db,{'project_id':project_id},payload)
        invalidate_project(db,project_id,identities)
        value={'projectId':project_id,'revision':payload.revision+1,'standardSetIds':identities,'standardWarnings':warnings}
        record(db,scope,key,fp,value)
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',(uuid4().hex,'local-reviewer','project',project_id,'project_standards_confirmed','demo',json.dumps(value),now()))
        return value


def add_project_creator(settings,project_id,payload,key):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE');p=project_for(db,project_id)
        scope='project-creator:'+project_id;fp=fingerprint(payload)
        prior=replay(db,scope,key,fp)
        if prior:return prior
        if not project_standards_view(db,project_id)['ready']:raise ApiError(409,'PROJECT_STANDARDS_REQUIRED','请先确认项目统一脚本与视频规则，再添加达人')
        if payload.platform.value not in json.loads(p['platforms_json']):raise ApiError(400,'PLATFORM_NOT_ALLOWED','平台不属于项目')
        if not payload.acknowledge_duplicate and db.execute('SELECT 1 FROM creator_tasks WHERE project_id=? AND creator_name=? AND platform=? AND archived_at IS NULL',(project_id,payload.creator_name,payload.platform.value)).fetchone():
            raise ApiError(409,'DUPLICATE_SUSPECTED','项目内已有同名达人，请确认是否另建提交任务')
        identity=uuid4().hex;stamp=now()
        db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,campaign_phase,rule_version_id,stage,revision,created_at,updated_at) VALUES (?,?,?,?,'video','general',NULL,'script',1,?,?)",(identity,project_id,payload.creator_name,payload.platform.value,stamp,stamp))
        value={'id':identity,'projectId':project_id};record(db,scope,key,fp,value)
        return value


@router.get('/projects/{project_id}/standards',response_model=Envelope[dict])
def get_project_standards(project_id:str,request:Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:value=project_standards_view(db,project_id)
    return Envelope(data=value,request_id=request.state.request_id)


@router.post('/projects/{project_id}/standards/confirm',response_model=Envelope[dict])
def confirm_project_standards(project_id:str,payload:ProjectStandardsInput,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=save_project_standards(request.app.state.settings,project_id,payload,idempotency_key),request_id=request.state.request_id)


@router.post('/projects/{project_id}/creators',response_model=Envelope[dict],status_code=201)
def create_project_creator(project_id:str,payload:ProjectCreatorInput,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=add_project_creator(request.app.state.settings,project_id,payload,idempotency_key),request_id=request.state.request_id)


@router.get('/workflow/video-components',response_model=Envelope[list[dict]])
def video_components(request:Request):
    from .video_components import catalog as component_catalog
    local_only(request)
    return Envelope(data=component_catalog(),request_id=request.state.request_id)


def fingerprint(payload):
    return hashlib.sha256(payload.model_dump_json().encode()).hexdigest()


def replay(db,scope,key,fp):
    row=db.execute('SELECT * FROM idempotency_records WHERE scope=? AND key=?',(scope,key)).fetchone()
    if row:
        if row['request_hash']!=fp:
            raise ApiError(409,'IDEMPOTENCY_CONFLICT','幂等键对应的请求不同')
        return json.loads(row['response_json'])


def record(db,scope,key,fp,value):
    db.execute('INSERT INTO idempotency_records VALUES (?,?,?,?,?)',(scope,key,fp,json.dumps(value),now()))


def publish(settings,project_id,payload,key):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        project=project_for(db,project_id)
        scope='publish-task:'+project_id; fp=fingerprint(payload)
        prior=replay(db,scope,key,fp)
        if prior:
            return prior
        shared=project_standards_view(db,project_id)
        if shared['ready']:
            def canonical(rules):
                values=[]
                for r in rules:
                    value={k:v for k,v in r.items() if k in ('text','criterion','category','importance','sourceBlockId','sourceQuote','origin','componentPlan','params','layer','enabled','baselineKey','presetKey')}
                    if value.get('componentPlan'):
                        value['componentPlan']={**value['componentPlan'],'nodes':[{k:v for k,v in n.items() if k!='prompt'} for n in value['componentPlan']['nodes']]}
                    values.append(StandardRule.model_validate(value).model_dump(by_alias=True))
                return comparable_rules(values)
            if any(canonical(shared[s]['rules'])!=canonical([r.model_dump(by_alias=True) for r in (payload.script_rules if s=='script' else payload.video_rules)]) for s in ('script','video')) or shared['script']['source_version_id']!=payload.source_version_id:
                raise ApiError(409,'PROJECT_STANDARDS_SHARED','达人统一使用项目规则，请在项目中修改规则，不要为达人另设规则')
        elif shared['legacyCandidates']:
            raise ApiError(409,'PROJECT_STANDARDS_REQUIRED','请先整合历史规则并确认项目统一标准')
        if payload.platform.value not in json.loads(project['platforms_json']):
            raise ApiError(400,'PLATFORM_NOT_ALLOWED','平台不属于项目')
        if not payload.acknowledge_duplicate and db.execute('SELECT 1 FROM creator_tasks WHERE project_id=? AND platform=? AND creator_name=? AND archived_at IS NULL',(project_id,payload.platform.value,payload.creator_name)).fetchone():
            raise ApiError(409,'DUPLICATE_SUSPECTED','同项目同平台已有同名达人，确认后可发布独立任务')
        identity=uuid4().hex;stamp=now()
        db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,campaign_phase,rule_version_id,stage,revision,created_at,updated_at) VALUES (?,?,?,?,'video',?,NULL,'script',1,?,?)",(identity,project_id,payload.creator_name,payload.platform.value,payload.campaign_phase,stamp,stamp))
        task=db.execute('SELECT * FROM creator_tasks WHERE id=?',(identity,)).fetchone()
        if shared['ready']:
            standards={scope:shared[scope]['id'] for scope in ('script','video')}
        else:
            standards=freeze_sets(db,task,payload)
            invalidate_project(db,project_id,standards)
        value={'id':identity,'projectId':project_id,'standardSetIds':standards}
        record(db,scope,key,fp,value)
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',(uuid4().hex,'local-reviewer','creator_task',identity,'published_with_standards','demo',json.dumps(value),stamp))
    return value


def reconfirm(settings,task_id,payload,key):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        task=authorize_task(db,task_id);require_task_writable(task)
        scope='confirm-task-standards:'+task_id; fp=fingerprint(payload)
        prior=replay(db,scope,key,fp)
        if prior:
            return prior
        if task['revision']!=payload.task_revision:
            raise ApiError(409,'REVISION_CONFLICT','任务已变化，请刷新后重新审核标准')
        standards=freeze_sets(db,task,payload)
        invalidate_project(db,task['project_id'],standards)
        value={'id':task_id,'projectId':task['project_id'],'standardSetIds':standards,'scope':'project'};record(db,scope,key,fp,value)
    return value


def template_rules(domain):
    def rule(text,criterion,category):
        return {'text':text,'criterion':criterion,'category':category,'importance':'must','sourceBlockId':'','sourceQuote':'','origin':'template'}
    script=default_script_rules()
    video=[rule('实际品牌与产品信息正确','检查真实画面、口播和字幕里的品牌名、产品名，是否与视频基础资料中填写的完整名称逐字一致，并记录出现时间；基础资料为空时，标注待品牌确认，不要自行判定对错。','品牌信息'),
           rule('产品展示可辨认','检查实际露出是否能辨认产品，定位展示时间；若品牌要求次数或时长，应先补充具体阈值再判定。','画面'),
           rule('口播与字幕一致且卖点落实','对照视频基础资料中确认的必提词句，核查实际音轨和字幕；口播听不清时标注待确认，发现的问题要保留有声片段。','口播字幕'),
           rule('已通过脚本的执行一致性','对照品牌通过脚本核查内容承诺与关键镜头，指出偏离和影响，不把所有创意变化判错。','脚本一致性')]
    if domain=='food':
        script.append(rule('饮用体验与成分描述有依据','核对脚本里的口味、配料或营养描述，是否与品牌确认的成分和说法一致；主观体验与客观数据要分开表述。','食品饮品'))
        video.append(rule('包装与食用展示符合确认资料','检查成片展示的包装、饮用场景和实际口播，是否符合视频基础资料中确认的包装名称与说法，记录疑点。','食品饮品'))
    elif domain=='beauty':
        script.append(rule('使用体验与功效描述有依据','核对脚本里的使用方式、功效表述，是否与品牌确认的依据一致；缺少依据时标注待品牌确认。','美妆个护'))
        video.append(rule('使用演示与对比表达可核实','对照确认资料核查真实使用演示和效果对比，疑似误导时保留上下文片段交人工确认。','美妆个护'))
    return {'scriptRules':script,'videoRules':video}


def recommend(settings,project_id,payload,key):
    if not payload.allow_real_call:
        raise ApiError(400,'REAL_CALL_CONFIRMATION_REQUIRED','请确认真实AI推荐调用')
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        project=project_for(db,project_id);scope='standards-recommend:'+project_id;fp=fingerprint(payload)
        prior=replay(db,scope,key,fp)
        if prior:
            row=db.execute('SELECT * FROM standard_recommendations WHERE id=?',(prior['id'],)).fetchone()
            return recommendation_view(row)
        from .brief_processing import latest
        processing=latest(db,payload.source_version_id)
        if processing and processing['status']!='succeeded':raise ApiError(409,'BRIEF_PROCESSING_NOT_READY','Brief自动处理尚未完成，请先完成图文解析和事实卡提取再生成规则')
        blocks,uncovered=source_blocks(db,project_id,payload.source_version_id)
        config=db.execute('SELECT * FROM decomposition_model_settings WHERE id=1').fetchone()
        if not config or not config['encrypted_key']:
            raise ApiError(409,'DECOMPOSITION_MODEL_KEY_MISSING','请先配置独立的 Brief 解构模型和 Key')
        if config['revision']!=payload.config_revision:
            raise ApiError(409,'REVISION_CONFLICT','Brief 解构模型配置已变化，请重新确认')
        if db.execute("SELECT 1 FROM standard_recommendations WHERE status='processing'").fetchone():
            raise ApiError(409,'RECOMMENDATION_BUSY','已有标准推荐正在处理')
        template=recommendation_prompt('brief-script-rules-text' if payload.scope=='script' and payload.output_format=='paragraph' else 'brief-'+payload.scope+'-standards', payload.strictness)
        if payload.scope=='video' and payload.video_rule_mode=='layered':
            template=prompt('brief-video-layered')
        if payload.scope=='video' and payload.video_rule_mode=='template':
            from .video_layers import defaults
            template=prompt('brief-video-template')
            template={**template,'content':template['content']+'\n\n【待补全的通用模板】'+json.dumps(defaults(),ensure_ascii=False)}
        from .brief_facts import latest_facts_run
        facts_run=latest_facts_run(db,project_id,payload.source_version_id)
        facts=facts_run['facts'] if facts_run else None
        from .fact_scope import scoped_facts, filter_scoped_candidates
        generation_facts,scope_exclusions=scoped_facts(facts,payload.scope)
        # 事实卡完整且无待核对项时只发事实卡：Brief 正文动辄数万字，卡片是已核对出处的摘要。
        facts_only=bool(facts_run and facts_run['complete'])
        if facts:
            template={**template,'content':template['content']+'\n\n'+(FACTS_ONLY_NOTE if facts_only else FACTS_WITH_BLOCKS_NOTE)+json.dumps(generation_facts,ensure_ascii=False)}
        script_refs=payload.scope=='script' and payload.output_format=='structured'
        references=[]
        generation_references=[]
        if script_refs:
            from .script_citations import CONTRACT, source_catalog
            references=source_catalog(None if facts_only else blocks,facts,available_blocks=blocks)
            catalog_rules={'rules':[{'text':entry['ref'],'sourceRefs':[entry['ref']]} for entry in references]}
            allowed,_=filter_scoped_candidates(catalog_rules,facts,payload.scope,references)
            allowed_refs={entry['text'] for entry in allowed['rules']}
            generation_references=[entry for entry in references if entry['ref'] in allowed_refs]
            template={**template,'content':template['content']+'\n\n'+CONTRACT}
            template['version']=hashlib.sha256(template['content'].encode()).hexdigest()[:12]
        from .platform_rule_skills import apply_platform_skills
        template,platform_skills=apply_platform_skills(template,json.loads(project['platforms_json']),payload.scope)
        from .video_rule_scope import existing_exclusions
        existing_video=project_sets_for(db,project_id)['video'] if payload.scope=='video' else None
        existing_rules = ([r.model_dump(by_alias=True) for r in payload.existing_video_rules]
                          if payload.existing_video_rules is not None else (existing_video['rules'] if existing_video else [])) if payload.scope=='video' else []
        snapshot={'modelRole':'decomposition','prompt':template,'strictness':payload.strictness,'videoRuleMode':payload.video_rule_mode,'configRevision':config['revision'],'endpoint':config['endpoint'],'model':config['model'],
                  'excludedExistingVideoRuleTexts':existing_exclusions(existing_video['rules'] if existing_video else []),
                  'existingVideoRules':existing_rules,
                  'sourceVersionId':payload.source_version_id,'uncoveredSources':uncovered,'factsUsed':bool(facts),
                  'inputMode':'facts' if facts_only else 'blocks','factsPendingCount':facts_run['pendingCount'] if facts_run else 0,
                  'briefFacts':facts,
                  'factScopeVersion':'material_stage_v2_keep_unknown','excludedFacts':scope_exclusions,
                  'recommendationVersion':'script_refs_v1' if script_refs else 'legacy',
                  'sourceReferences':references,
                  'generationSourceReferences':generation_references,
                  'platformSkills':platform_skills,
                  'blocks':[] if facts_only else blocks,
                  'requestOptions':{'reasoningEffort':config['reasoning_effort'],'temperature':config['temperature'],
                                    'outputMode':'json_schema' if re.match(r'^qwen3\.[78]-(?:plus|max|flash)(?:$|-)',config['model']) else 'json_object',
                                    'readTimeoutSeconds':RULE_GENERATION_READ_TIMEOUT,'maxAttempts':1}}
        identity=uuid4().hex;stamp=now()
        db.execute('INSERT INTO standard_recommendations VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,project_id,payload.source_version_id,payload.scope,'processing',json.dumps(snapshot,ensure_ascii=False),None,None,stamp,stamp))
        record(db,scope,key,fp,{'id':identity});config=dict(config)
    result={};status='failed';error=None
    try:
        secret=cipher(settings.data_dir).decrypt(config['encrypted_key'].encode()).decode()
        import inspect
        # Inspect legacy adapters before calling; a provider TypeError must never trigger another paid call.
        sent_blocks=None if facts_only else blocks
        extra = {'existing_rules':existing_rules} if existing_rules and 'existing_rules' in inspect.signature(call_recommendation).parameters else {}
        if script_refs and 'source_refs' in inspect.signature(call_recommendation).parameters:
            result=call_recommendation(config,secret,template,sent_blocks,uncovered,facts=generation_facts,source_refs=generation_references)
        elif 'facts' in inspect.signature(call_recommendation).parameters:
            result=call_recommendation(config,secret,template,sent_blocks,uncovered,facts=generation_facts,**extra)
        else:
            result=call_recommendation(config,secret,template,sent_blocks,uncovered)
        if result.get('httpStatus') is not None and result['httpStatus'] != 200:
            raise RecommendationProviderRejected(result['httpStatus'])
        from .platform_rule_skills import filter_stage_candidates
        model_value,excluded=filter_stage_candidates(result['report'],payload.scope,platform_skills)
        model_value,scope_rejected=filter_scoped_candidates(model_value,facts,payload.scope,references)
        excluded.extend(scope_rejected)
        if excluded:result['excludedCandidates']=excluded
        if template['key']=='brief-script-rules-text':
            report=ParagraphRules.model_validate(model_value)
            if not report.rules_text.strip():
                raise ValueError('empty rules')
        elif script_refs:
            from .script_citations import prepare_report
            report=prepare_report(model_value,blocks,facts,references,legacy=True)
        else:
            report=validate_recommendation(model_value,blocks,payload.scope,facts=facts)
            if payload.scope=='video' and payload.video_rule_mode=='layered':
                from .video_layers import normalize_candidates
                report=normalize_candidates(report)
            if payload.scope=='video' and payload.video_rule_mode=='template':
                from .video_layers import normalize_template
                report=normalize_template(report,facts)
        if uncovered:
            report.warnings.append(system_note(f'Brief有{len(uncovered)}项未解析或未覆盖资料，需品牌核对原件补充标准。'))
        if scope_exclusions or scope_rejected:
            report.warnings.append(system_note(f'已按原页适用范围略过{len(scope_exclusions)}条事实及{len(scope_rejected)}条跨范围候选，保留通用模板；详情见调用诊断。'))
        result['report']=report.model_dump(by_alias=True)
        status='succeeded'
    except RecommendationProviderRejected as exc:
        result.pop('report',None)
        result.update({'errorCode':f'PROVIDER_HTTP_{exc.status}','errorType':'ProviderHTTPError'})
        error=provider_http_message(exc.status)
    except httpx.ReadTimeout as exc:
        result.pop('report',None)
        result.update({'errorCode':'PROVIDER_READ_TIMEOUT','errorType':type(exc).__name__,'readTimeoutSeconds':RULE_GENERATION_READ_TIMEOUT})
        error=f'等待模型返回超过{RULE_GENERATION_READ_TIMEOUT}秒；供应商是否已处理或计费未知，未自动重试。请先核对供应商用量，再决定是否重新生成'
    except httpx.HTTPError as exc:
        result.pop('report',None)
        result.update({'errorCode':'PROVIDER_CONNECTION_INTERRUPTED','errorType':type(exc).__name__})
        error='模型连接中断；是否计费未知，未自动重试。请先核对供应商用量，再决定是否重新生成'
    except Exception as exc:
        result.pop('report',None)
        source_invalid=isinstance(exc,ApiError) and exc.code=='INVALID_STANDARD_SOURCE'
        result.update({'errorCode':'INVALID_STANDARD_SOURCE' if isinstance(exc,ApiError) else 'RECOMMENDATION_REPORT_INVALID','errorType':type(exc).__name__})
        if isinstance(exc,ApiError) and exc.details:result['invalidRules']=exc.details.get('ruleNumbers')
        if source_invalid:
            error='模型规则引文未能在Brief原文或已核对事实卡中定位；已保留返回内容，可先本地重新校验，无需调用AI。'
        else:
            error='AI推荐结果格式或引用校验失败；已保留返回内容，可先重新校验上次结果，无需调用AI；未自动重试'
    finally:
        secret=''
    with connection(settings.database_path) as db:
        from app.core.timing import stamp_duration
        stamp_duration(db, 'standard_recommendations', identity, result)
        db.execute('UPDATE standard_recommendations SET status=?,result_json=?,error_message=?,updated_at=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),error,now(),identity))
        return recommendation_view(db.execute('SELECT * FROM standard_recommendations WHERE id=?',(identity,)).fetchone())


# 平台自己追加的提示（引文定位、重新校验、识读完整性）与模型写的 Brief 疑点分开，前端分成两组展示。
SYSTEM_NOTE_PREFIX='【系统提示】'


def system_note(text):
    return SYSTEM_NOTE_PREFIX+text


SCRIPT_PROMPT_KEYS=('brief-script-standards','brief-script-rules-text','brief-image-standards')
VIDEO_ONLY_FIELDS=('layer','enabled','baselineKey','presetKey','componentPlan')


def prune_video_fields(schema):
    """从规则对象 schema 中删除视频分层字段，并清掉因此不再被引用的 $defs。"""
    def walk(node):
        if isinstance(node,dict):
            properties=node.get('properties')
            if isinstance(properties,dict) and 'criterion' in properties:
                for key in VIDEO_ONLY_FIELDS:properties.pop(key,None)
                if isinstance(node.get('required'),list):
                    node['required']=[key for key in node['required'] if key in properties]
            for value in node.values():walk(value)
        elif isinstance(node,list):
            for value in node:walk(value)
    walk(schema)
    defs=schema.get('$defs')
    if not isinstance(defs,dict):return
    def references(node):
        found=set()
        if isinstance(node,dict):
            ref=node.get('$ref')
            if isinstance(ref,str) and ref.startswith('#/$defs/'):found.add(ref.split('/')[-1])
            for value in node.values():found|=references(value)
        elif isinstance(node,list):
            for value in node:found|=references(value)
        return found
    reachable=set();queue=[{key:value for key,value in schema.items() if key!='$defs'}]
    while queue:
        for name in references(queue.pop()):
            if name not in reachable:
                reachable.add(name);queue.append(defs.get(name,{}))
    for name in list(defs):
        if name not in reachable:defs.pop(name,None)


def json_completion(config,secret,template,user_content,schema_model,read_timeout=None):
    """Shared structured-JSON completion for Brief decomposition calls."""
    schema=schema_model.model_json_schema()
    def server_fields(node):
        if isinstance(node,dict):
            props=node.get('properties',{})
            if 'criterion' in props:
                for name in ('sourceReferences','citationStatus','citationNote','citationConfirmed'):
                    props.pop(name,None)
            for child in node.values():server_fields(child)
        elif isinstance(node,list):
            for child in node:server_fields(child)
    server_fields(schema)
    if schema_model is ScriptRuleCandidates:
        refs=[r['ref'] for r in json.loads(user_content).get('sourceReferences',[])]
        if refs:
            schema['$defs']['ScriptRuleCandidate']['properties']['sourceRefs']['items']['enum']=refs
    # Qwen3.8-Flash supports strict schema output. Keep compatibility services
    # on JSON Object for unrecognized models; never probe by paid resubmission.
    strict=bool(re.match(r'^qwen3\.[78]-(?:plus|max|flash)(?:$|-)',config['model']))
    if strict:
        def require_fields(node):
            if isinstance(node,dict):
                node.pop('default',None)
                if node.get('type')=='object':node.update(additionalProperties=False,required=list(node.get('properties',{})))
                for value in node.values():require_fields(value)
            elif isinstance(node,list):
                for value in node:require_fields(value)
        require_fields(schema)
    # 脚本规则没有三层配置：schema 里留着 layer/enabled/baselineKey 等字段，strict 模式会强制模型填，
    # 模型就会编出 layer=brief、baselineKey=product，保存时被“三层配置只适用于视频”拒绝。
    if template.get('key') in SCRIPT_PROMPT_KEYS:prune_video_fields(schema)
    response_format={'type':'json_schema','json_schema':{'name':'brief_rule_candidates','strict':True,'schema':schema}} if strict else {'type':'json_object'}
    with httpx.Client(timeout=httpx.Timeout(read_timeout or RECOMMENDATION_READ_TIMEOUT,connect=15,write=60),follow_redirects=False) as client:
        response=client.post(config['endpoint'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+secret},json={'model':config['model'],'stream':False,'reasoning_effort':config.get('reasoning_effort','none'),'temperature':config.get('temperature',0.1),'response_format':response_format,'messages':[
            # strict 模式 schema 已随 response_format 下发，system 里再拼一遍纯属浪费 token。
            {'role':'system','content':template['content'] if strict else template['content']+'\nJSON Schema:'+json.dumps(schema,ensure_ascii=False)},
            {'role':'user','content':user_content}]})
        if response.status_code!=200:
            safe=response.text.replace(secret,'[REDACTED]') if secret else response.text
            safe=re.sub(r'Bearer\s+[^\s"<>]+','Bearer [REDACTED]',safe,flags=re.I)
            request_id=next((response.headers[key] for key in ('x-request-id','x-dashscope-request-id','request-id') if response.headers.get(key)),None)
            return {'httpStatus':response.status_code,'report':None,'providerResponse':safe[:12000],
                    'providerRequestIds':[request_id] if request_id else [],
                    'errorCode':f'PROVIDER_HTTP_{response.status_code}','errorType':'ProviderHTTPError'}
        data=response.json()
    content=data['choices'][0]['message'].get('content') or ''
    result={'providerRequestIds':[data.get('id')],'usage':[data.get('usage')],'modelResponse':content.replace(secret,'[REDACTED]')[:200000]}
    value=content.strip()
    if value.startswith('```') and value.endswith('```'):
        value=value.split('\n',1)[-1].rsplit('```',1)[0].strip()
    try:
        result['report']=json.loads(value) if len(value)<=200000 else None
    except ValueError:
        result['report']=None
    return result


def call_recommendation(config,secret,template,blocks,uncovered,facts=None,source_refs=None,existing_rules=None):
    model=ParagraphRules if template['key']=='brief-script-rules-text' else RecommendedVideoStandards if template['key'] in ('brief-video-standards','brief-video-layered','brief-video-template') else RecommendedStandards
    if template['key']=='brief-image-standards':model=RecommendedImageStandards
    # blocks 为 None 表示这次只发事实卡，正文留在服务端做引文校验。
    content={'blocks':blocks,'uncoveredSources':uncovered,'briefFacts':facts} if blocks is not None else {'uncoveredSources':uncovered,'briefFacts':facts}
    if source_refs is not None:
        model=ScriptRuleCandidates
        content={'sourceReferences':source_refs,'briefFacts':facts,'uncoveredSources':uncovered}
    if existing_rules:
        content['existingVideoRules']=existing_rules
    return json_completion(config,secret,template,json.dumps(content,ensure_ascii=False),model,read_timeout=RULE_GENERATION_READ_TIMEOUT)


def recommendation_view(row):
    result=json.loads(row['result_json']) if row['result_json'] else None
    message=row['error_message']
    # Correct presentation of old HTTP failures without overwriting stored evidence.
    if result and isinstance(result.get('httpStatus'),int) and result['httpStatus']!=200:
        result={**result}
        if result.get('errorCode') != f"PROVIDER_HTTP_{result['httpStatus']}":
            result.update(storedErrorCode=result.get('errorCode'),storedErrorType=result.get('errorType'))
        result.update(errorCode=f"PROVIDER_HTTP_{result['httpStatus']}",errorType='ProviderHTTPError')
        message=provider_http_message(result['httpStatus'])
    return {'id':row['id'],'scope':row['scope'],'status':row['status'],'snapshot':json.loads(row['snapshot_json']),
            'result':result,'errorMessage':message,'createdAt':row['created_at'],'updatedAt':row['updated_at']}


@router.get('/workflow/prompts',response_model=Envelope[list[dict]])
def prompts(request:Request):
    local_only(request)
    return Envelope(data=catalog(),request_id=request.state.request_id)


@router.get('/workflow/standard-templates',response_model=Envelope[list[dict]])
def templates(request:Request):
    local_only(request)
    return Envelope(data=[{'id':key,'name':name,**template_rules(key)} for key,name in [('general','通用品牌种草'),('food','食品饮品'),('beauty','美妆个护')]],request_id=request.state.request_id)


@router.post('/projects/{project_id}/publish-task',response_model=Envelope[dict],status_code=201)
def publish_task(project_id:str,request:Request,payload:PublishTaskInput,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=publish(request.app.state.settings,project_id,payload,idempotency_key),request_id=request.state.request_id)


@router.post('/tasks/{task_id}/standards/confirm',response_model=Envelope[dict])
def confirm_standards(task_id:str,request:Request,payload:ReconfirmInput,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=reconfirm(request.app.state.settings,task_id,payload,idempotency_key),request_id=request.state.request_id)


@router.post('/projects/{project_id}/standard-recommendations',response_model=Envelope[dict])
def recommendations(project_id:str,request:Request,payload:RecommendationInput,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=recommend(request.app.state.settings,project_id,payload,idempotency_key),request_id=request.state.request_id)


@router.get('/projects/{project_id}/standard-recommendations',response_model=Envelope[list[dict]])
def recommendation_history(project_id:str,request:Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        project_for(db,project_id)
        rows=db.execute('SELECT * FROM standard_recommendations WHERE project_id=? ORDER BY rowid DESC LIMIT 20',(project_id,)).fetchall()
    return Envelope(data=[recommendation_view(row) for row in rows],request_id=request.state.request_id)


@router.post('/projects/{project_id}/standard-recommendations/{recommendation_id}/revalidate',response_model=Envelope[dict])
def revalidate_saved_recommendation(project_id:str,recommendation_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    """Create a derived candidate from saved output, without submitting a model request."""
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE');project_for(db,project_id)
        scope='revalidate-standards:'+project_id;fp=hashlib.sha256(recommendation_id.encode()).hexdigest()
        prior=replay(db,scope,idempotency_key,fp)
        if prior:return Envelope(data=recommendation_view(db.execute('SELECT * FROM standard_recommendations WHERE id=?',(prior['id'],)).fetchone()),request_id=request.state.request_id)
        row=db.execute('SELECT * FROM standard_recommendations WHERE id=? AND project_id=?',(recommendation_id,project_id)).fetchone()
        if not row:raise ApiError(404,'RESOURCE_NOT_FOUND','推荐记录不存在')
        if row['status'] not in ('succeeded','failed'):raise ApiError(409,'RECOMMENDATION_NOT_READY','原调用尚未结束，不能重新校验')
        from .project_brief import current_brief
        current=current_brief(db,project_id,include_blocks=False)
        if not current or current['sourceVersionId']!=row['source_version_id']:raise ApiError(409,'REVISION_CONFLICT','当前Brief已变化，请勿套用历史推荐')
        snapshot=json.loads(row['snapshot_json']);result=json.loads(row['result_json'] or '{}')
        raw=result.get('modelResponse','').strip()
        if raw.startswith('```') and raw.endswith('```'):raw=raw.split('\n',1)[-1].rsplit('```',1)[0].strip()
        try:value=json.loads(raw)
        except (ValueError,TypeError):raise ApiError(400,'RECOMMENDATION_REPORT_INVALID','上次没有可用的JSON返回，无法本地恢复')
        if snapshot.get('platformSkills'):
            from .platform_rule_skills import filter_stage_candidates
            value,excluded=filter_stage_candidates(value,row['scope'],snapshot['platformSkills'])
            if excluded:result['excludedCandidates']=excluded
        blocks,uncovered=source_blocks(db,project_id,row['source_version_id'])
        if snapshot.get('blocks') and snapshot['blocks']!=blocks:raise ApiError(409,'REVISION_CONFLICT','Brief识读依据已变化，请勿套用历史推荐')
        facts=snapshot.get('briefFacts')
        if facts is None and snapshot.get('factsUsed'):
            from .brief_facts import latest_facts
            facts=latest_facts(db,project_id,row['source_version_id'])
        if snapshot.get('factScopeVersion'):
            from .fact_scope import filter_scoped_candidates
            value,scope_rejected=filter_scoped_candidates(value,facts,row['scope'],snapshot.get('sourceReferences',[]))
            if scope_rejected:result.setdefault('excludedCandidates',[]).extend(scope_rejected)
        try:
            if row['scope']=='script' and snapshot.get('prompt',{}).get('key')!='brief-script-rules-text':
                from .script_citations import prepare_report,source_catalog
                references=snapshot.get('sourceReferences') or source_catalog(blocks,facts)
                report=prepare_report(value,blocks,facts,references,legacy=True)
                snapshot.update(recommendationVersion='script_refs_v1',sourceReferences=references)
            else:
                report=validate_recommendation(value,blocks,row['scope'],facts=facts)
        except ValidationError:raise ApiError(400,'RECOMMENDATION_REPORT_INVALID','上次结果字段不符合规则格式，无法本地恢复；已保留原记录')
        if row['scope']=='video' and snapshot.get('videoRuleMode')=='layered':
            from .video_layers import normalize_candidates
            report=normalize_candidates(report)
        if row['scope']=='video' and snapshot.get('videoRuleMode')=='template':
            from .video_layers import normalize_template
            report=normalize_template(report,facts)
        report.warnings.append(system_note('本次仅重新校验已保存的模型输出，未调用AI；引用可定位不代表判定口径合理，请品牌逐条核对背景、示例和必须要求。'))
        if uncovered:report.warnings.append(system_note(f'Brief仍有{len(uncovered)}项未完整识读，需核对原件。'))
        snapshot.update({'blocks':blocks,'uncoveredSources':uncovered,'derivedFromRecommendationId':row['id'],'operation':'local-revalidation'})
        result.update({'report':report.model_dump(by_alias=True),'localRevalidation':True})
        for field in ('errorCode','errorType'):result.pop(field,None)
        identity=uuid4().hex;stamp=now()
        db.execute('INSERT INTO standard_recommendations VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,project_id,row['source_version_id'],row['scope'],'succeeded',json.dumps(snapshot,ensure_ascii=False),json.dumps(result,ensure_ascii=False),None,stamp,stamp))
        record(db,scope,idempotency_key,fp,{'id':identity})
        return Envelope(data=recommendation_view(db.execute('SELECT * FROM standard_recommendations WHERE id=?',(identity,)).fetchone()),request_id=request.state.request_id)




@router.get('/workflow/script-rules/defaults',response_model=Envelope[dict])
def script_rule_defaults(request:Request):
    local_only(request)
    return Envelope(data={'systemPrompt':prompt('script-review'),'rulesText':DEFAULT_SCRIPT_RULES},request_id=request.state.request_id)


@router.get('/workflow/video-rule-defaults',response_model=Envelope[dict])
def video_rule_defaults(request:Request):
    from .video_layers import defaults
    return Envelope(data={'rules':defaults()},request_id=request.state.request_id)
