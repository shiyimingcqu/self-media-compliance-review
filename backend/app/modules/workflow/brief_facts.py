"""Brief 事实卡：一次解构品牌客观事实（品牌名/产品全名/必提禁用词句/发布规则/阈值），
供脚本与视频两条规则生成链路共同参考；判断阶段仍只看已确认规则，事实卡不进判断输入。"""
import json
from uuid import uuid4
from typing import Literal

import httpx
from fastapi import APIRouter, Header, Request
from pydantic import Field, ValidationError

from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from app.modules.video_model.evidence.secrets import cipher
from .prompts import prompt
from .fact_scope import FactApplicability
from .standards import (RECOMMENDATION_READ_TIMEOUT, fingerprint, json_completion, project_for,
                        record, replay, source_blocks)

router = APIRouter(tags=['brief-facts'])

FACT_GROUPS = ('products', 'competitors', 'sellingPoints', 'requiredTerms', 'forbiddenTerms', 'publishRules', 'thresholds', 'freeform')


class FactItem(ContractModel):
    # strict json_schema 要求字段全部存在，因此空值在输出层允许，由 validate_facts 逐条拒绝。
    value: str = Field(default='', max_length=1000)
    block_id: str = Field(default='', max_length=100)
    quote: str = Field(default='', max_length=3000)
    applicability: FactApplicability | None = None


class BrandEntity(ContractModel):
    name: FactItem
    aliases: list[FactItem] = Field(default_factory=list,max_length=30)
    role: Literal['primary','related']


class BriefFacts(ContractModel):
    # 必填但可留空：strict json_schema 不支持 anyOf；value 为空的 brand 在 extract 中按缺品牌失败。
    brand: FactItem = Field(default_factory=lambda: FactItem(value='', block_id='', quote=''))
    brand_entities: list[BrandEntity] = Field(default_factory=list,max_length=30)
    background: str = Field(default='', max_length=2000)
    products: list[FactItem] = Field(default_factory=list, max_length=30)
    competitors: list[FactItem] = Field(default_factory=list, max_length=30)
    selling_points: list[FactItem] = Field(default_factory=list, max_length=30)
    required_terms: list[FactItem] = Field(default_factory=list, max_length=30)
    forbidden_terms: list[FactItem] = Field(default_factory=list, max_length=30)
    publish_rules: list[FactItem] = Field(default_factory=list, max_length=30)
    thresholds: list[FactItem] = Field(default_factory=list, max_length=30)
    freeform: list[FactItem] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=40)

    def items(self):
        groups = [self.products, self.competitors, self.selling_points, self.required_terms, self.forbidden_terms,
                  self.publish_rules, self.thresholds, self.freeform]
        return ([self.brand] if self.brand.value else []) + [item for group in groups for item in group] + [item for entity in self.brand_entities for item in [entity.name,*entity.aliases]]


class FactsInput(ContractModel):
    source_version_id: str
    config_revision: int = Field(ge=1, description='独立 Brief 解构模型配置版本，不是内容审核模型版本')
    allow_real_call: bool = False


def validate_facts(value, blocks, allow_missing_brand=False):
    """事实卡每条出处必须逐字来自所选 Brief 的同一个原文块；不满足即整次解构失败，不部分采信。"""
    try:
        facts = BriefFacts.model_validate(value)
    except ValidationError:
        raise ApiError(400, 'BRIEF_FACTS_REPORT_INVALID', 'AI 事实卡结构不符合约定，未采信本次结果，不自动重试') from None
    lookup = {b['id']: b['text'] for b in blocks}
    if not facts.brand.value.strip() and not allow_missing_brand:
        raise ApiError(400, 'BRIEF_FACTS_BRAND_MISSING', '事实卡缺少可核对的品牌名，整次结果未采信；请人工核对 Brief 后重新解构')
    blank = [group for group in FACT_GROUPS if any(not item.value.strip() for item in getattr(facts, _snake(group)))]
    if blank:
        raise ApiError(400, 'BRIEF_FACTS_REPORT_INVALID', f'事实卡分组 {",".join(blank)} 存在空条目，整次结果未采信')
    invalid = [item.value for item in facts.items() if item.block_id not in lookup or item.quote not in lookup[item.block_id]]
    if invalid:
        raise ApiError(400, 'BRIEF_FACTS_CITATION_MISMATCH',
                       f'事实卡有{len(invalid)}条出处无法逐字核对，整次结果未采信：' + '；'.join(v[:30] for v in invalid[:3]))
    from .fact_scope import validate_scope
    for item in facts.items():
        if item.applicability is not None:
            # Same-page visual description may contain the merged-cell coverage label.
            prefix=item.block_id.rsplit(':',1)[0] if item.block_id.startswith('vision:') else None
            sources=[text for bid,text in lookup.items() if bid==item.block_id or (prefix and bid.rsplit(':',1)[0]==prefix)]
            item.applicability=FactApplicability.model_validate(validate_scope(item.applicability.model_dump(by_alias=True),sources))
    return facts


def _snake(name):
    import re
    return re.sub(r'([A-Z])', lambda m: '_' + m.group(1).lower(), name)


def compact_fact_blocks(blocks):
    """Remove only exact, same-page duplication; retain original IDs and source text."""
    visual_text={}
    for block in blocks:
        page=block.get('pageNo')
        if page is not None and block.get('origin')=='ai-visual-extraction' and block['id'].endswith(':text'):
            visual_text.setdefault(page,[]).append(block['text'])
    result=[];seen=set()
    for block in blocks:
        page=block.get('pageNo');text=block['text']
        if block.get('origin')!='ai-visual-extraction' and text.strip() and any(text in source for source in visual_text.get(page,[])):
            continue
        identity=(page,text)
        if identity in seen:continue
        seen.add(identity);result.append(block)
    return result


def call_facts(config, secret, template, blocks, uncovered):
    return json_completion(config, secret, template, json.dumps({'blocks': blocks, 'uncoveredSources': uncovered}, ensure_ascii=False), BriefFacts)


def facts_view(row):
    return {'id': row['id'], 'status': row['status'], 'sourceVersionId': row['source_version_id'],
            'snapshot': json.loads(row['snapshot_json']),
            'result': json.loads(row['result_json']) if row['result_json'] else None,
            'errorMessage': row['error_message'], 'createdAt': row['created_at'], 'updatedAt': row['updated_at']}


def latest_facts_run(db, project_id, source_version_id):
    """当前 Brief 版本最近一次成功的事实卡，附完整性与待核对数量。逐字引用已校验，但不等于卡片覆盖了 Brief 全部要求。"""
    row = db.execute("SELECT id,snapshot_json,result_json FROM brief_facts_runs WHERE project_id=? AND source_version_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1",
                     (project_id, source_version_id)).fetchone()
    if not row:return None
    # A newer VLM transcription changes evidence block IDs, even for the same PDF.
    visual=db.execute("SELECT id,snapshot_json FROM brief_visual_runs WHERE source_version_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1",(source_version_id,)).fetchone()
    if visual and json.loads(visual['snapshot_json']).get('extractFacts'):
        if json.loads(row['snapshot_json']).get('visionRunId')!=visual['id']:return None
    result=json.loads(row['result_json'] or '{}') or {}
    facts=result.get('facts')
    if not facts:return None
    pending=result.get('pendingFacts') or []
    # 只有显式标为不完整（图文校验拒收、待核对未处理）或没有可核对品牌时才算不完整；
    # TXT 提取路径不写 factsComplete，按“无待核对即完整”处理。
    complete=result.get('factsComplete') is not False and not pending and bool((facts.get('brand') or {}).get('value'))
    return {'facts':facts,'pendingCount':len(pending),'complete':complete,'runId':row['id']}


def latest_facts(db, project_id, source_version_id):
    """当前 Brief 版本上最近一次成功的事实卡；旧版本的事实卡不会串台。"""
    bundle=latest_facts_run(db, project_id, source_version_id)
    return bundle['facts'] if bundle else None


def extract(settings, project_id, payload, key):
    if not payload.allow_real_call:
        raise ApiError(400, 'REAL_CALL_CONFIRMATION_REQUIRED', '请确认 AI 事实解构为真实计费调用')
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        project_for(db, project_id)
        scope = 'brief-facts:' + project_id
        fp = fingerprint(payload)
        prior = replay(db, scope, key, fp)
        if prior:
            return facts_view(db.execute('SELECT * FROM brief_facts_runs WHERE id=?', (prior['id'],)).fetchone())
        source=db.execute('SELECT s.page_count FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id WHERE s.id=? AND b.project_id=?',(payload.source_version_id,project_id)).fetchone()
        if source and source['page_count']:
            raise ApiError(409,'BRIEF_FACTS_USE_VLM','PDF事实卡请通过图文解析直接生成，不再单独调用语言模型提取')
        blocks, uncovered = source_blocks(db, project_id, payload.source_version_id)
        source_block_count=len(blocks)
        blocks=compact_fact_blocks(blocks)
        config = db.execute('SELECT * FROM decomposition_model_settings WHERE id=1').fetchone()
        if not config or not config['encrypted_key']:
            raise ApiError(409, 'DECOMPOSITION_MODEL_KEY_MISSING', '请先配置独立的 Brief 解构模型和 Key')
        if config['revision'] != payload.config_revision:
            raise ApiError(409, 'REVISION_CONFLICT', 'Brief 解构模型配置已变化，请重新确认')
        if db.execute("SELECT 1 FROM brief_facts_runs WHERE status='processing'").fetchone():
            raise ApiError(409, 'BRIEF_FACTS_BUSY', '已有事实卡解构正在处理')
        template = prompt('brief-facts')
        from .brief_import import document_context
        context = document_context(db, project_id, payload.source_version_id)
        template = {**template, 'content': template['content'] + '\n文档上下文（仅用于消歧；事实出处仍须引用正文块）：' + json.dumps(context, ensure_ascii=False)}
        identity = uuid4().hex
        stamp = now()
        snapshot = {'modelRole': 'decomposition', 'prompt': {k: template[k] for k in ('key', 'title', 'purpose', 'version')},
                    'documentContext': context,
                    'configRevision': config['revision'], 'endpoint': config['endpoint'], 'model': config['model'],
                    'sourceVersionId': payload.source_version_id, 'uncoveredSources': uncovered, 'blockCount': len(blocks), 'sourceBlockCount': source_block_count, 'inputCompaction': 'exact-same-page-containment',
                    'requestOptions': {'reasoningEffort': config['reasoning_effort'], 'temperature': config['temperature'],
                                       'readTimeoutSeconds': RECOMMENDATION_READ_TIMEOUT, 'maxAttempts': 1}}
        db.execute('INSERT INTO brief_facts_runs VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (identity, project_id, payload.source_version_id, 'processing',
                    json.dumps(snapshot, ensure_ascii=False), None, None, None, stamp, stamp))
        record(db, scope, key, fp, {'id': identity})
        config = dict(config)
    result = {}
    status = 'failed'
    error = None
    try:
        secret = cipher(settings.data_dir).decrypt(config['encrypted_key'].encode()).decode()
        try:
            raw = call_facts(config, secret, template, blocks, uncovered)
            result = {k: raw[k] for k in ('providerRequestIds', 'usage', 'modelResponse') if k in raw}
            facts = validate_facts(raw['report'], blocks)
            result['facts'] = facts.model_dump(by_alias=True)
            if uncovered:
                result['facts']['warnings'].append(f'Brief有{len(uncovered)}项未解析或未覆盖资料，事实卡可能不完整，需品牌核对原件。')
            status = 'succeeded'
        finally:
            secret = ''
    except httpx.ReadTimeout as exc:
        result.update({'errorCode': 'PROVIDER_READ_TIMEOUT', 'errorType': type(exc).__name__, 'readTimeoutSeconds': RECOMMENDATION_READ_TIMEOUT})
        error = f'等待模型返回超过{RECOMMENDATION_READ_TIMEOUT}秒；供应商是否已处理或计费未知，未自动重试。请先核对供应商用量，再决定是否重新解构'
    except httpx.HTTPError as exc:
        result.update({'errorCode': 'PROVIDER_CONNECTION_INTERRUPTED', 'errorType': type(exc).__name__})
        error = '模型连接中断；是否计费未知，未自动重试。请先核对供应商用量，再决定是否重新解构'
    except Exception as exc:
        result.update({'errorCode': getattr(exc, 'code', None) or 'BRIEF_FACTS_REPORT_INVALID', 'errorType': type(exc).__name__})
        error = getattr(exc, 'message', None) or 'AI 事实卡解构或出处校验失败；已保留返回内容，未自动重试'
    with connection(settings.database_path) as db:
        from app.core.timing import stamp_duration
        stamp_duration(db, 'brief_facts_runs', identity, result)
        db.execute('UPDATE brief_facts_runs SET status=?,result_json=?,error_message=?,updated_at=? WHERE id=?',
                   (status, json.dumps(result, ensure_ascii=False), error, now(), identity))
        return facts_view(db.execute('SELECT * FROM brief_facts_runs WHERE id=?', (identity,)).fetchone())


@router.post('/projects/{project_id}/brief/facts', response_model=Envelope[dict], status_code=201)
def create_facts(project_id: str, payload: FactsInput, request: Request,
                 idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request)
    return Envelope(data=extract(request.app.state.settings, project_id, payload, idempotency_key),
                    request_id=request.state.request_id)


@router.get('/projects/{project_id}/brief/facts', response_model=Envelope[dict])
def get_facts(project_id: str, request: Request):
    """返回当前项目 Brief 最近一次成功的事实卡与最近一次运行（含失败原因），供规则页与 Brief 面板展示。"""
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        from .project_brief import current_brief
        brief = current_brief(db, project_id, include_blocks=False)
        if not brief:
            return Envelope(data={'sourceVersionId': None, 'facts': None, 'lastRun': None}, request_id=request.state.request_id)
        row = db.execute('SELECT * FROM brief_facts_runs WHERE project_id=? AND source_version_id=? ORDER BY rowid DESC LIMIT 1',
                         (project_id, brief['sourceVersionId'])).fetchone()
        facts = latest_facts(db, project_id, brief['sourceVersionId'])
    return Envelope(data={'sourceVersionId': brief['sourceVersionId'], 'facts': facts,
                          'lastRun': facts_view(row) if row else None}, request_id=request.state.request_id)
