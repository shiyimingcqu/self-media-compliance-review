"""Fixed marketing review workflow: immutable inputs, AI advice, explicit brand decisions."""
import hashlib
import json
import time
from collections import Counter
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.eligibility import applicable, rules_enabled
from app.core.errors import ApiError
from app.modules.brief_script.script_service import authorize_task, require_task_writable, script_version_detail
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from app.modules.video_model.evidence.console_service import settings_view
from app.modules.video_model.evidence.secrets import cipher
from .prompts import prompt
from .script_output import OutputError, normalize_report_envelope, output_contract, parse_output, prompt_hash, repair_output, request_options
from .script_rule_refs import freeze_references, normalize_references, provider_material, recover_legacy_reference

SCRIPT_READ_TIMEOUT_SECONDS = 300


class ScriptProviderError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
from .script_media import prepare_media
from .script_image_context import annotate_image_context

router = APIRouter(tags=['workflow'])


class ScriptAnalysisInput(ContractModel):
    script_version_id: str
    config_revision: int = Field(ge=1)
    context_key: str
    allow_real_call: bool = False


class BrandDecisionInput(ContractModel):
    scope: str = Field(pattern='^(script|video)$')
    target_id: str
    context_key: str
    decision: str = Field(pattern='^(approved|returned)$')
    note: str = Field(min_length=1, max_length=2000)
    reviewed_material: bool = False
    confirmed_rule_ids: list[str] = Field(default_factory=list, max_length=1000)


class ScriptFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rule_id: str
    rule_number: int | None = Field(default=None, ge=1, strict=True)
    image_ids: list[str] = Field(default_factory=list, max_length=24)
    quote_refs: list[str] = Field(default_factory=list, max_length=20)
    verdict: Literal['compliant','non_compliant','needs_confirmation','not_applicable','pending']
    quote: str = ''
    quote_segments: list[str] = Field(default_factory=list,max_length=20)
    description: str = Field(max_length=4000)
    suggestion: str = Field(default='', max_length=2000)


class MandatoryCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rule_id: str
    rule_number: int | None = Field(default=None, ge=1, strict=True)
    image_ids: list[str] = Field(default_factory=list, max_length=24)
    quote_refs: list[str] = Field(default_factory=list, max_length=20)
    requirement: str = Field(default='', max_length=4000)
    verdict: Literal['compliant','non_compliant','needs_confirmation','not_applicable','pending']
    quote: str = ''
    quote_segments: list[str] = Field(default_factory=list,max_length=20)
    reason: str = Field(max_length=4000)


class ScriptImageCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_id: str
    description: str = Field(min_length=1, max_length=4000)
    # A reference image must be reviewed even when no frozen rule applies to it.
    rule_ids: list[str] = Field(min_length=0, max_length=60)


class ScriptReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(max_length=6000)
    findings: list[ScriptFinding]
    mandatory_checks: list[MandatoryCheck] = Field(default_factory=list)
    image_checks: list[ScriptImageCheck] = Field(default_factory=list, max_length=24)


def validate_quote(item,text):
    def ordered(segments,source,error):
        cursor=0
        for segment in segments:
            location=source.find(segment,cursor) if segment else -1
            if location<0:raise ValueError(error)
            cursor=location+len(segment)
    if item.quote and item.quote not in text:
        # Models sometimes join separate Excel cells into one quote. Accept only
        # independently exact, ordered spans; never fuzzy-match or invent text.
        segments=[line.strip() for line in item.quote.splitlines() if line.strip()]
        if len(segments)<2 or (item.quote_segments and item.quote_segments!=segments):raise ValueError('quote')
        ordered(segments,text,'quote')
        item.quote_segments=segments;item.quote=segments[0]
    ordered(item.quote_segments,text,'quote segment source')
    if item.quote_segments and item.quote and item.quote!=item.quote_segments[0]:
        # A genuine full quote can also contain the highlighted sub-spans.
        # Normalize only after proving every span belongs to this same quote.
        ordered(item.quote_segments,item.quote,'quote segment mismatch')
        item.quote=item.quote_segments[0]


def image_reference_map(media):
    """图片引用 → 真实 id。短 ref（i1…）与真实 id 都接受；未知引用一律不解析。

    模型只需抄对 `i3` 这种短引用；让它逐字复述 64 位哈希会稳定出错（真实任务里
    一张图的 id 在第 33 位被改写，整次审核因此判 EVIDENCE_MISSING 失败）。
    历史快照没有 ref 字段，只带真实 id，因此 id 本身也必须在映射里。
    """
    mapping={}
    for image in media.get('images') or []:
        real=image.get('id')
        if not real:continue
        mapping[real]=real
        ref=image.get('ref')
        if ref:
            mapping.setdefault(ref,real)
    return mapping


def validate_script_report(value,snapshot,require_complete=True):
    rules=snapshot['rules'];text=snapshot['script'];media=snapshot['multimodal']
    value=normalize_references(value,snapshot)
    report=ScriptReport.model_validate(value)
    ids=[f.rule_id for f in report.findings]
    known_ids={r['id'] for r in rules}
    numbers={r['id']:r['ruleNumber'] for r in rules}
    counts=Counter(ids)
    missing=sorted(known_ids-set(ids)) if require_complete and not snapshot['paragraphMode'] else []
    if snapshot['paragraphMode'] and not ids:missing=sorted(known_ids)
    duplicates=sorted(rid for rid,count in counts.items() if count>1) if not snapshot['paragraphMode'] else []
    all_refs=[*ids,*[item.rule_id for item in report.mandatory_checks],
              *[rid for check in report.image_checks for rid in check.rule_ids]]
    unknown=sorted(set(all_refs)-known_ids)
    mismatches=[{'section':section,'ruleId':item.rule_id,'returnedRuleNumber':item.rule_number,'expectedRuleNumber':numbers[item.rule_id]}
                for section,items in (('findings',report.findings),('mandatory_checks',report.mandatory_checks))
                for item in items if item.rule_id in numbers and item.rule_number not in (None,numbers[item.rule_id])]
    if missing or duplicates or unknown or mismatches:
        issues=[];messages=[]
        if missing:issues.append('missing');messages.append(f'报告遗漏{len(missing)}条规则')
        if duplicates:issues.append('duplicate');messages.append(f'报告重复返回{len(duplicates)}条规则')
        if unknown:issues.append('unknown');messages.append(f'报告包含{len(unknown)}个未知规则引用')
        if mismatches:issues.append('number_mismatch');messages.append(f'{len(mismatches)}处规则编号与冻结规则不一致')
        raise OutputError('RULE_REF_INVALID','；'.join(messages),issueTypes=issues,
                          missingRuleIds=missing,duplicateRuleIds=duplicates,unknownRuleRefs=unknown,
                          ruleNumberMismatches=mismatches,returnedRuleIds=ids,expectedRuleIds=sorted(known_ids))
    image_ids={im['id'] for im in media['images']}
    references=image_reference_map(media)
    for check in report.image_checks:
        check.image_id=references.get(check.image_id,check.image_id)
    for item in [*report.findings,*report.mandatory_checks]:
        item.image_ids=[references.get(ref,ref) for ref in item.image_ids]
    reviewed_images=[im.image_id for im in report.image_checks]
    if (require_complete and (len(reviewed_images)!=len(image_ids) or set(reviewed_images)!=image_ids)) or any(im.image_id not in image_ids for im in report.image_checks):
        raise OutputError('EVIDENCE_MISSING','图片未完整核对或引用了未知图片',expectedImageIds=sorted(image_ids),returnedImageIds=reviewed_images)
    items=[*report.findings,*report.mandatory_checks]
    unverified={}
    for item in items:
        if item.rule_id not in numbers or item.rule_number not in (None,numbers[item.rule_id]) or not set(item.image_ids).issubset(image_ids):
            raise OutputError('RULE_REF_INVALID' if item.rule_id not in numbers or item.rule_number not in (None,numbers.get(item.rule_id)) else 'EVIDENCE_MISSING',
                              '规则编号或图片引用与本次快照不符',ruleId=item.rule_id,ruleNumber=item.rule_number,imageIds=item.image_ids)
        item.rule_number=numbers[item.rule_id]
        if isinstance(item,MandatoryCheck) and not item.requirement:
            item.requirement=next(r['text'] for r in rules if r['id']==item.rule_id)
        if item.quote_refs:
            sources={q['id']:q['text'] for q in snapshot.get('quoteSources',[])}
            if any(ref not in sources for ref in item.quote_refs):
                raise OutputError('EVIDENCE_MISSING','引文引用不属于本次脚本',ruleId=item.rule_id,quoteRefs=item.quote_refs)
            item.quote_segments=[sources[ref] for ref in item.quote_refs]
            item.quote=item.quote_segments[0]
        original_quote=item.quote
        original_segments=list(item.quote_segments)
        try:
            validate_quote(item,text)
        except ValueError:
            # Preserve the model's claim for review, never fabricate a source
            # span or let unverifiable evidence grant mandatory acceptance.
            unverified[item.rule_id]={'unverified_quote':original_quote,
                'unverified_quote_segments':original_segments,
                'evidence_status':'unverified',
                'evidence_warning':'引文待人工核对：模型引文未逐字匹配脚本，本项不计为通过。'}
            item.quote=''
            item.quote_segments=[]
    for item in items:
        if item.rule_id in unverified:
            item.verdict='needs_confirmation'
    result=report.model_dump()
    for item in [*result['findings'],*result['mandatory_checks']]:
        if item['rule_id'] in unverified:
            item.update(unverified[item['rule_id']])
    return result


def retain_partial_report(result,snapshot):
    """Keep independently valid rules visible; never count a partial run as passed."""
    value=normalize_references(result.get('report'),snapshot)
    if not isinstance(value,dict):return
    known={r['id']:r for r in snapshot['rules']};seen=set();findings=[]
    raw_findings=value.get('findings',[]) if isinstance(value.get('findings'),list) else []
    # 重复项可能给出互相冲突的结论，不能随意挑第一条当作有效结果。
    counts=Counter(raw.get('rule_id') for raw in raw_findings if isinstance(raw,dict) and isinstance(raw.get('rule_id'),str))
    for raw in value.get('findings',[]) if isinstance(value.get('findings'),list) else []:
        try:
            finding=ScriptFinding.model_validate(raw)
            if finding.rule_id in seen or finding.rule_id not in known:continue
            if not snapshot.get('paragraphMode') and counts[finding.rule_id]>1:continue
            checked=validate_script_report({'summary':'部分结果','findings':[raw]},snapshot,require_complete=False)
            findings.extend(checked['findings']);seen.add(finding.rule_id)
        except (ValueError,ValidationError):continue
    pending=sorted(set(known)-seen)
    for rid in pending:
        findings.append({'rule_id':rid,'rule_number':known[rid]['ruleNumber'],'verdict':'pending',
                         'description':'本条尚无可采纳的完整判定，需人工核对','quote':'','suggestion':'','image_ids':[]})
    image_refs=image_reference_map(snapshot['multimodal'])
    raw_checks=value.get('image_checks',[]) if isinstance(value.get('image_checks'),list) else []
    image_counts=Counter(image_refs.get(raw['image_id'],raw['image_id']) for raw in raw_checks
                         if isinstance(raw,dict) and isinstance(raw.get('image_id'),str))
    image_checks=[]
    for raw in raw_checks:
        try:
            check=ScriptImageCheck.model_validate(raw)
            check.image_id=image_refs.get(check.image_id,check.image_id)
            if check.image_id not in image_refs.values() or image_counts[check.image_id]!=1:continue
            if not set(check.rule_ids).issubset(known):continue
            image_checks.append(check.model_dump())
        except (ValueError,ValidationError):continue
    result['partialReport']={'summary':'报告不完整；以下仅保留通过独立校验的条目，整体不得自动通过。',
                             'findings':findings,'image_checks':image_checks}
    result['coverage']={'totalRules':len(known),'validatedRules':len(seen),'pendingRuleIds':pending}


def applicable_rules(db, task, stage=None):
    from .standards import sets_for
    independent = sets_for(db, task['id'])
    if independent['script'] or independent['video']:
        return [rule for scope in ('script','video') if stage is None or scope == stage for rule in (independent[scope]['rules'] if independent[scope] else [])]
    rows = db.execute("SELECT * FROM requirements WHERE rule_version_id=? AND confirmation='confirmed'", (task['rule_version_id'],)).fetchall()
    result = []
    for row in rows:
        scope = {'platforms':json.loads(row['platforms_json']), 'phases':json.loads(row['phases_json']), 'content_types':json.loads(row['content_types_json'])}
        stages = json.loads(row['review_stages_json'])
        if applicable(scope, task['platform'], task['campaign_phase'], task['content_type']) and (stage is None or stage in stages):
            audiovisual = any(word in row['category'] + row['text'] for word in ('口播', '字幕', '音乐', '声音'))
            policy = {'preferredTools':['cut_evidence_clip'] if audiovisual else ['capture_evidence_image','cut_evidence_clip'],
                      'instruction':'完整音视频审查后，定位相关时间段再按需取证；证据须保留语境，无法确认则交人工。缺失项不能靠一张截图证明。',
                      'maxImagesPerIssue':2,'maxClipsPerIssue':1,'maxClipMs':30000}
            result.append({'id':row['id'], 'text':row['text'], 'category':row['category'], 'importance':row['importance'],
                           'reviewStages':stages, 'source':json.loads(row['source_json']), 'exceptions':row['exceptions'], 'evidencePolicy':policy})
    return result


def context(db, task_id):
    task = authorize_task(db, task_id)
    latest = db.execute('SELECT id FROM script_versions WHERE task_id=? ORDER BY ordinal DESC LIMIT 1', (task_id,)).fetchone()
    detail = script_version_detail(db, latest['id']) if latest else None
    # Includes invalidation events, so rebind A→B→A cannot resurrect an old approval.
    event = db.execute('SELECT id FROM task_invalidation_events WHERE task_id=? ORDER BY rowid DESC LIMIT 1', (task_id,)).fetchone()
    value = {'taskId':task_id, 'ruleVersionId':task['rule_version_id'], 'scriptVersionId':latest['id'] if latest else None,
             'platform':task['platform'], 'phase':task['campaign_phase'], 'contentType':task['content_type'], 'invalidationId':event['id'] if event else None}
    from .standards import sets_for
    value['standardSetIds'] = {scope: row['id'] if row else None for scope,row in sets_for(db,task_id).items()}
    from .project_brief import current_brief
    brief=current_brief(db,task['project_id'],include_blocks=False)
    value['briefSourceVersionId']=brief['sourceVersionId'] if brief else None
    if brief and brief.get('visionRunId'):value['briefVisionRunId']=brief['visionRunId']
    key = hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    return task, detail, key


def script_acceptance(row, accepted=None):
    pending={'passed':False,'status':'pending','checks':[],'blockingCount':0,'message':'提交脚本后进行AI必须项验收'}
    if row:
        saved=json.loads(row['result_json'] or '{}')
        if saved.get('recovery',{}).get('requiresManualReview') or saved.get('partialReport'):
            return {**pending,'status':'incomplete','message':'报告经格式修复或仅部分完整，需人工核对，不能自动通过'}
    if not row or row['status']!='succeeded':
        return pending
    snapshot=json.loads(row['snapshot_json']);report=(json.loads(row['result_json'] or '{}').get('report') or {})
    rules=snapshot.get('rules') or []
    coverage=snapshot.get('briefCoverage') or snapshot.get('brief') or {}
    if coverage.get('uncoveredSources'):
        return {**pending,'status':'incomplete','message':'项目 Brief 有未解析的资料，请补全后重新验收，暂不进入品牌复核'}
    if snapshot.get('multimodal', {}).get('incomplete') or snapshot.get('uncoveredText'):
        return {**pending,'status':'incomplete','message':'脚本存在未覆盖的文字或图片，请补全材料后重新验收，暂不进入品牌复核'}
    required={r['id'] for r in rules if r.get('importance')=='must'}
    if not required:
        return {**pending,'status':'incomplete','message':'没有可验证的必须项，暂不进入品牌复核'}
    if snapshot.get('paragraphMode'):
        checks=report.get('mandatory_checks') or []
    else:
        checks=[{'rule_id':f['rule_id'],'rule_number':f.get('rule_number'),'image_ids':f.get('image_ids',[]),'requirement':next((r['text'] for r in rules if r['id']==f['rule_id']),''),
                 'verdict':f['verdict'],'quote':f.get('quote',''),'reason':f['description']} for f in report.get('findings',[]) if f['rule_id'] in required]
    covered={c.get('rule_id') for c in checks}
    if not checks or not required.issubset(covered) or any(c.get('rule_id') not in required for c in checks):
        return {**pending,'status':'incomplete','checks':checks,'message':'AI未完整返回必须项验收结果，请重新验收后交品牌复核'}
    checks=[{**c, 'ai_verdict':c.get('verdict'), 'verdict':'compliant', 'brandRuling':'accepted'} if c.get('rule_id') in (accepted or set()) else c for c in checks]
    blocking=sum(c.get('verdict') not in ('compliant','not_applicable') for c in checks)
    failed=sum(c.get('verdict')=='non_compliant' for c in checks)
    return {'passed':blocking==0,'status':'passed' if blocking==0 else 'needs_changes' if failed else 'needs_confirmation','checks':checks,'blockingCount':blocking,
            'message':('品牌已采纳相关异议，必须项满足，仍需品牌确认脚本' if accepted else '必须项验收通过，可提交品牌复核') if blocking==0 else f'还有{failed}项必须项未通过，请修改或提出异议' if failed else f'还有{blocking}项必须项待确定，需品牌核对，不代表脚本违规'}


def script_submission_ready(row, accepted=None):
    """Submission allows unresolved judgments, never failed/incomplete execution."""
    if not row or row['status'] != 'succeeded':
        return False
    acceptance = script_acceptance(row, accepted)
    if acceptance['status'] in ('pending', 'incomplete'):
        return False
    return bool(acceptance['checks']) and not any(c.get('verdict') == 'non_compliant' for c in acceptance['checks'])


def review_view(row):
    if not row:
        return None
    return {'id':row['id'], 'scriptVersionId':row['script_version_id'], 'ruleVersionId':row['rule_version_id'],
            'contextKey':row['context_key'], 'acceptance':script_acceptance(row), 'status':row['status'], 'snapshot':json.loads(row['snapshot_json']),
            'result':json.loads(row['result_json']) if row['result_json'] else None,
            'errorCode':row['error_code'], 'errorMessage':row['error_message'], 'createdAt':row['created_at'], 'updatedAt':row['updated_at']}


def video_component_acceptance(row, accepted=None):
    if not row or row['status']!='succeeded':
        return {'passed':False,'blockingRules':[],'message':'视频审核尚未完成'}
    snapshot=json.loads(row['snapshot_json']);result=json.loads(row['result_json'] or '{}')
    from .video_layers import active_rules, legacy_skipped_competitors
    must=[r for r in active_rules(snapshot.get('rules',[])) if r.get('importance')=='must']
    values={r.get('ruleId'):r for r in result.get('componentResults',[])}
    findings={f.get('rule_id'):f for f in (result.get('report') or {}).get('findings',[])}
    skipped=legacy_skipped_competitors(snapshot,list(findings.values()))
    pending={r['ruleId']:r for r in snapshot.get('unconfiguredRules',[]) if r['ruleId'] not in skipped}
    def entry(r,reason):
        return {'ruleId':r['id'],'ruleNumber':r.get('ruleNumber'),'text':r['text'],
                'layer':r.get('layer') or ('precise' if r.get('componentPlan') else 'brief'),'reason':reason}
    def reason_for(r):
        verdict=findings.get(r['id'],{}).get('verdict')
        return '必须项未给出判定' if verdict is None else '必须项明确不合规'
    # 阻断口径统一：只有"明确不合规 / 未给出判定 / 待配置"挡人工确认通过；
    # AI 说待确认的进 review 列表交人工判断，不再因为"不确定"就剥夺品牌决定权。
    def blocking(r):
        if r['id'] in skipped:
            return False
        if r['id'] in (accepted or set()) and r['id'] not in pending:
            return False
        verdict=findings.get(r['id'],{}).get('verdict')
        component=values.get(r['id'],{}).get('verdict')
        return r['id'] in pending or verdict in (None,'non_compliant') or bool(r.get('componentPlan') and component=='non_compliant')
    blocked=[entry(r,pending[r['id']]['reason'] if r['id'] in pending else reason_for(r)) for r in must if blocking(r)]
    review=[entry(r,'AI 待确认，需人工核对') for r in must
            if r['id'] not in pending and r['id'] not in (accepted or set()) and findings.get(r['id'],{}).get('verdict') in ('needs_confirmation','pending')]
    review.extend(entry(r,'旧报告未执行竞品检查，提交品牌人工核对；重新验收时由模型自动判断') for r in must if r['id'] in skipped)
    unconfigured=any(r['ruleId'] in pending for r in blocked)
    if blocked:
        message=('部分必须项待品牌配置；配置缺失不记为达人违规，其余反馈请逐条核对' if unconfigured
                 else f'有 {len(blocked)} 项必须项明确不合规或未给出判定，不能直接确认通过；可退回修改')
    elif review:
        message=f'另有 {len(review)} 项 AI 待确认，请人工核对后决定'
    else:
        message='视频必须项已满足，仍需品牌复核'
    return {'passed':not blocked,'blockingRules':blocked,'reviewRules':review,'reviewCount':len(review),
            'configurationRequired':unconfigured,'message':message}



# Temporary product policy: allow video submission before script approval.
REQUIRE_SCRIPT_APPROVAL_FOR_VIDEO = False


def video_submission_ready(row, accepted=None):
    if not row or row['kind'] != 'video' or row['status'] != 'succeeded':
        return False
    result = json.loads(row['result_json'] or '{}')
    if result.get('partialReport') or result.get('recovery', {}).get('requiresManualReview'):
        return False
    from .video_layers import active_rules, legacy_skipped_competitors
    snapshot=json.loads(row['snapshot_json'])
    rules = active_rules(snapshot.get('rules', []))
    findings = (result.get('report') or {}).get('findings', [])
    skipped=legacy_skipped_competitors(snapshot,findings)
    expected={r['id'] for r in rules};actual={f.get('rule_id') for f in findings}
    if not rules or expected-skipped != actual or len(findings) != len(actual):
        return False
    return video_component_acceptance(row, accepted)['passed']


def state(db, task_id):
    task, script, key = context(db, task_id)
    eligibility = rules_enabled(db, task_id)
    from .standards import sets_for, sets_ready
    from app.core.eligibility import DENY
    if not sets_ready(db,task):
        eligibility = DENY('STANDARDS_NOT_CONFIRMED','请先由品牌分别确认独立脚本标准和视频标准')
    reviews = db.execute('SELECT * FROM workflow_script_reviews WHERE task_id=? ORDER BY rowid DESC LIMIT 20', (task_id,)).fetchall()
    current = db.execute('SELECT * FROM workflow_script_reviews WHERE task_id=? AND context_key=? ORDER BY rowid DESC LIMIT 1', (task_id,key)).fetchone()
    decision = db.execute("SELECT * FROM workflow_decisions WHERE task_id=? AND scope='script' AND context_key=? ORDER BY rowid DESC LIMIT 1", (task_id,key)).fetchone()
    from .discussions import accepted_rules
    accepted = accepted_rules(db, 'script', current['id'] if current else None)
    confirmed = {r['rule_id'] for r in db.execute('SELECT rule_id FROM workflow_script_confirmations WHERE decision_id=?', (decision['id'],))} if decision and current and decision['target_id']==current['id'] and decision['decision']=='approved' else set()
    acceptance = script_acceptance(current, accepted | confirmed)
    approved = bool(acceptance['passed'] and eligibility.allowed and current and current['status']=='succeeded' and decision and decision['decision']=='approved' and decision['target_id']==current['id'])
    latest_video = db.execute("SELECT * FROM evidence_runs WHERE task_id=? AND kind='video' ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
    snapshot = json.loads(latest_video['snapshot_json']) if latest_video else {}
    video_current = bool(eligibility.allowed and (approved or not REQUIRE_SCRIPT_APPROVAL_FOR_VIDEO) and latest_video and snapshot.get('workflowContextKey')==key and snapshot.get('scriptApprovalId')==(decision['id'] if approved else None))
    final = db.execute("SELECT * FROM workflow_decisions WHERE task_id=? AND scope='video' AND context_key=? ORDER BY rowid DESC LIMIT 1", (task_id,key)).fetchone()
    final_current = bool(video_current and final and final['target_id']==latest_video['id'])
    writable = task['archived_at'] is None and task['project_status']!='archived'
    submission = db.execute("SELECT updated_at FROM review_responses WHERE task_id=? AND scope='script' AND review_id=? AND action='submitted'", (task_id,current['id'])).fetchone() if current else None
    video_submission = db.execute("SELECT updated_at FROM review_responses WHERE task_id=? AND scope='video' AND review_id=? AND action='submitted'", (task_id,latest_video['id'])).fetchone() if video_current else None
    if approved:
        acceptance={**acceptance,'message':'必须项已通过，品牌已确认当前脚本'}
    elif acceptance['passed'] and decision and current and decision['target_id']==current['id'] and decision['decision']=='returned':
        acceptance={**acceptance,'message':'必须项已通过，品牌已退回修改，请按品牌意见提交新版本'}
    stage = ('requirements' if not eligibility.allowed else 'script' if not approved and not video_current else
             'completed' if final_current and final['decision']=='approved' else
             'video' if final_current and final['decision']=='returned' else
             'human' if video_current and latest_video['status']=='succeeded' else 'video')
    history = db.execute('SELECT * FROM workflow_decisions WHERE task_id=? ORDER BY rowid DESC LIMIT 30', (task_id,)).fetchall()
    return {'taskId':task_id,'projectId':task['project_id'],'contextKey':key,'stage':stage,'writable':writable,
            'rulesReady':eligibility.allowed,'rulesMessage':eligibility.message,'scriptAcceptance':acceptance,'brandReviewAllowed':bool(eligibility.allowed and acceptance['passed'] and writable),'ruleVersionId':task['rule_version_id'],
            'scriptSubmissionReady':bool(eligibility.allowed and writable and script_submission_ready(current, accepted_rules(db, 'script', current['id'] if current else None))),
            'scriptSubmitted':bool(submission),'scriptSubmittedAt':submission['updated_at'] if submission else None,
            'videoSubmitted':bool(video_submission),'videoSubmittedAt':video_submission['updated_at'] if video_submission else None,
            'rules':[rule for rule in applicable_rules(db,task) if rule.get('enabled',True)],'standardSets':sets_for(db,task_id),'taskRevision':task['revision'],'currentScript':script,'scriptReview':review_view(current),
            'scriptReviews':[review_view(r) for r in reviews], 'scriptApproval':dict(decision) if approved else None,
            'videoAllowed':bool(eligibility.allowed and writable and (approved or not REQUIRE_SCRIPT_APPROVAL_FOR_VIDEO)), 'latestVideoRunId':latest_video['id'] if video_current else None,
            'videoReviewReady':bool(video_current and latest_video['status']=='succeeded' and writable),
            'videoComponentAcceptance':video_component_acceptance(latest_video if video_current else None, accepted_rules(db, 'video', latest_video['id'] if video_current else None)),
            'finalDecision':dict(final) if final_current else None,'history':[dict(r) for r in history]}


def video_submission_context(db, task_id):
    view = state(db, task_id)
    if not view['videoAllowed']:
        raise ApiError(409, 'VIDEO_SUBMISSION_UNAVAILABLE', view['rulesMessage'] if not view['rulesReady'] else '当前任务不可提交视频')
    script = view['currentScript'] if view['scriptApproval'] else None
    text = (script['text'] or '\n'.join(block['text'] for block in script['sourceMap'])) if script else ''
    return view, text


def approved_script(db, task_id):
    view = state(db, task_id)
    if not view['scriptApproval']:
        raise ApiError(409,'SCRIPT_NOT_APPROVED','当前脚本尚未由品牌确认通过，请先完成脚本审核')
    script = view['currentScript']
    text = script['text'] or '\n'.join(block['text'] for block in script['sourceMap'])
    return view, text


def replay(db, scope, key, fingerprint):
    row = db.execute('SELECT * FROM idempotency_records WHERE scope=? AND key=?',(scope,key)).fetchone()
    if row:
        if row['request_hash']!=fingerprint:
            raise ApiError(409,'IDEMPOTENCY_CONFLICT','幂等键已用于不同请求')
        return json.loads(row['response_json'])


def remember(db, scope, key, fingerprint, value):
    db.execute('INSERT INTO idempotency_records VALUES (?,?,?,?,?)',(scope,key,fingerprint,json.dumps(value),now()))


def params_check(rules, text):
    """按规则 params 对脚本原文做逐字核对；结果作为确定性事实随请求送给模型。
    判断阶段不提供 Brief 原文，规则未内联的事实只能来自这里。"""
    facts = []
    for rule in rules:
        params = rule.get('params') or {}
        for kind, values in (('name', params.get('names') or []), ('term', params.get('terms') or [])):
            for value in values:
                facts.append({'ruleId': rule['id'], 'kind': kind, 'value': value, 'present': value in text})
        for item in (params.get('thresholds') or []):
            facts.append({'ruleId': rule['id'], 'kind': 'threshold', 'value': item.get('key'), 'expected': item.get('value')})
    return facts


def analyze(settings, task_id, payload, key):
    if not payload.allow_real_call:
        raise ApiError(400,'REAL_CALL_CONFIRMATION_REQUIRED','需要明确确认真实脚本调用')
    fingerprint=hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    scope=f'workflow:script-analysis:{task_id}'
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        authorize_task(db,task_id)
        prior=replay(db,scope,key,fingerprint)
        if prior:
            return review_view(db.execute('SELECT * FROM workflow_script_reviews WHERE id=?',(prior['id'],)).fetchone())
        task, script, context_key=context(db,task_id)
        require_task_writable(task)
        from .standards import sets_ready
        if not sets_ready(db,task):
            raise ApiError(409,'STANDARDS_NOT_CONFIRMED','品牌需先确认两套独立审核标准')
        eligible=rules_enabled(db,task_id)
        if not eligible.allowed:
            raise ApiError(409,eligible.code,eligible.message)
        if context_key!=payload.context_key or not script or script['scriptVersionId']!=payload.script_version_id:
            raise ApiError(409,'WORKFLOW_INPUT_CHANGED','规则或脚本已变更，请刷新后再审核')
        if script['extractionStatus']!='ready':
            raise ApiError(409,'SCRIPT_NOT_READY','脚本解析失败，不能审核')
        if db.execute("SELECT 1 FROM workflow_script_reviews WHERE status='processing'").fetchone():
            raise ApiError(409,'SCRIPT_REVIEW_BUSY','已有脚本审核正在运行，请等待完成')
        config=db.execute('SELECT * FROM evidence_model_settings WHERE id=1').fetchone()
        if not config or not config['encrypted_key']:
            raise ApiError(409,'MODEL_KEY_MISSING','请先在模型设置保存 Key')
        if config['revision']!=payload.config_revision:
            raise ApiError(409,'REVISION_CONFLICT','模型配置已变化，请重新确认')
        # enabled=false 是品牌停用开关：不送模型、不参与验收；上下文键随规则集版本变化，旧通过决定自动失效。
        rules=[{**r,'ruleNumber':i} for i,r in enumerate((r for r in applicable_rules(db,task,'script') if r.get('enabled',True)),1)]
        text=script['text'] or '\n'.join(block['text'] for block in script['sourceMap'])
        media, image_inputs=prepare_media(settings,script)
        media=annotate_image_context(media,script['sourceMap'])
        if not text.strip() and not media['images']:
            raise ApiError(409,'SCRIPT_NOT_READY','未读取到可审核的文字或图片，请检查脚本完整性、图片格式与体积')
        identity=uuid4().hex;stamp=now()
        paragraph_mode = len(rules)==1 and rules[0]['category']=='脚本审核规则'
        snapshot={'paragraphMode':paragraph_mode,'reviewRulesText':'\n\n'.join(f"第{r['ruleNumber']}条：{r['text']}。{r.get('criterion') or ''}" for r in rules),'rules':rules,'script':text,'scriptBlocks':script['sourceMap'],'multimodal':media,'uncoveredText':any(str(w).startswith('未覆盖：') for w in script['parseMeta'].get('warnings',[])),'parseMeta':script['parseMeta'],'model':config['model'],'endpoint':config['endpoint'],'configRevision':config['revision'],'prompt':prompt('script-review')}
        freeze_references(snapshot)
        snapshot['providerRequestOptions']=request_options(config['model'])
        snapshot['quoteSources']=[{'id':f'q{i}','text':line} for i,line in enumerate(text.splitlines(),1) if line.strip()]
        # Brief 全文只用于规则生成，不再作为判断输入；这里仅保留验收所需的覆盖元数据。
        from .project_brief import review_brief
        from .standards import sets_for
        brief_snapshot=review_brief(db,task['project_id'],(sets_for(db,task_id)['script'] or {}).get('source_version_id'))
        snapshot['briefCoverage']={'sourceVersionId':(brief_snapshot or {}).get('sourceVersionId'),
                                   'uncoveredSources':(brief_snapshot or {}).get('uncoveredSources') or []}
        # 判断阶段只依据规则：Brief 原文不再进入模型输入；规则未内联的事实只能来自 params 的逐字核对。
        snapshot['paramsCheck']=params_check(rules,text)
        snapshot['promptHash']=prompt_hash(snapshot['prompt']['content'])
        snapshot['ruleSetHash']=hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest()
        db.execute('INSERT INTO workflow_script_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (identity,task_id,script['scriptVersionId'],task['rule_version_id'],context_key,json.dumps(snapshot,ensure_ascii=False),'processing',None,None,None,stamp,stamp))
        db.execute("UPDATE creator_tasks SET stage='script',revision=revision+1,updated_at=? WHERE id=?",(stamp,task_id))
        remember(db,scope,key,fingerprint,{'id':identity})
        encrypted=config['encrypted_key'];config=dict(config)
    # No transaction across network; a replay only reads the existing request and never resends.
    result={};status='failed';code=None;error=None
    try:
        secret=cipher(settings.data_dir).decrypt(encrypted.encode()).decode()
        def persist_output(saved):
            # Commit the original response before parsing or validation. A crash
            # after this point leaves inspectable evidence on the processing row.
            with connection(settings.database_path) as db:
                db.execute('UPDATE workflow_script_reviews SET result_json=?,updated_at=? WHERE id=?',
                           (json.dumps(saved,ensure_ascii=False),now(),identity))
        result=call_script_model(config,secret,{**snapshot,'_imageInputs':image_inputs,'_persistOutput':persist_output})
        if result.get('providerError'):
            raise ScriptProviderError(result['providerError']['code'],result['providerError']['message'])
        if result.get('outputError'):
            failure=result['outputError']
            if failure['code']=='OUTPUT_INVALID_JSON':
                repaired=repair_output(result.get('modelResponse',''))
                if repaired:
                    candidate,fixed=repaired
                    try:
                        candidate=normalize_references(candidate,snapshot,result)
                        candidate=validate_script_report(candidate,snapshot)
                    except (ValueError,ValidationError):pass
                    else:
                        result['report']=candidate
                        result['recovery']={'repaired':True,'requiresManualReview':True,'status':'NEEDS_MANUAL_REVIEW',
                                            'method':'paired_cjk_quotes_v1','repairedOutput':fixed,'providerResubmitted':False}
            raise OutputError(failure['code'],failure['message'],**failure.get('details',{}))
        result['report']=normalize_report_envelope(result['report'],result)
        result['report']=normalize_references(result['report'],snapshot,result)
        result['report']=validate_script_report(result['report'],snapshot);status='succeeded'
        result['warnings']=[{'code':'EVIDENCE_MISSING','ruleId':f['rule_id'],'ruleNumber':f['rule_number']}
                            for f in result['report']['findings'] if f.get('evidence_status')=='unverified']
    except ScriptProviderError as exc:
        result.pop('report', None)
        code=exc.code;error=exc.message
    except httpx.ReadTimeout:
        result.pop('report', None)
        result['diagnostics']={'errorType':'ReadTimeout','readTimeoutSeconds':SCRIPT_READ_TIMEOUT_SECONDS}
        code='PROVIDER_READ_TIMEOUT';error=f'等待模型返回超过 {SCRIPT_READ_TIMEOUT_SECONDS} 秒，未收到完整报告；是否计费未知，未自动重试'
    except httpx.ConnectTimeout:
        result.pop('report', None)
        result['diagnostics']={'errorType':'ConnectTimeout','connectTimeoutSeconds':15}
        code='PROVIDER_CONNECT_TIMEOUT';error='连接模型服务超过 15 秒，请检查网络和接口地址；未自动重试'
    except httpx.HTTPError as exc:
        result.pop('report', None)
        result['diagnostics']={'errorType':type(exc).__name__}
        code='PROVIDER_SUBMISSION_UNKNOWN';error='连接中断，是否计费未知；未自动重试'
    except OutputError as exc:
        retain_partial_report(result,snapshot)
        if not result.get('recovery') and 'report' in result:result['parsedOutput']=result.pop('report')
        result['diagnostics']=exc.details
        result['diagnostics']['ruleSetHash']=snapshot.get('ruleSetHash')
        code=exc.code;error=str(exc)+'；已保留原始输出，未自动重试'
        if result.get('recovery'):error+='；本地已生成修复候选，须人工核对'
    except ValidationError as exc:
        retain_partial_report(result,snapshot)
        if 'report' in result:result['parsedOutput']=result.pop('report')
        result['diagnostics']={'errorType':'ValidationError','validationError':'JSON 已解析，但字段不符合报告约定',
                               'schemaErrors':[{'path':'.'.join(map(str,e['loc'])),'constraint':e['type'],'message':e['msg']} for e in exc.errors(include_input=False,include_url=False)]}
        code='OUTPUT_SCHEMA_INVALID';error='模型报告字段不符合约定；已保留原始输出及可验证的部分条目，未自动重试'
    except ValueError as exc:
        if 'report' in result:result['parsedOutput']=result.pop('report')
        result['diagnostics']={'errorType':type(exc).__name__,'validationError':str(exc)[:120]}
        code='OUTPUT_SCHEMA_INVALID';error='模型报告无法采纳；已保留原始输出，未自动重试'
    except Exception:
        if 'report' in result:result['parsedOutput']=result.pop('report')
        code='INTERNAL_REVIEW_ERROR';error='审核处理发生内部错误；已保留已收到的原始输出，未自动重试'
    finally:
        secret=''
    with connection(settings.database_path) as db:
        from app.core.timing import stamp_duration
        stamp_duration(db, 'workflow_script_reviews', identity, result)
        db.execute('UPDATE workflow_script_reviews SET status=?,result_json=?,error_code=?,error_message=?,updated_at=? WHERE id=?',
                   (status,json.dumps(result,ensure_ascii=False),code,error,now(),identity))
        return review_view(db.execute('SELECT * FROM workflow_script_reviews WHERE id=?',(identity,)).fetchone())


def call_script_model(config, secret, snapshot):
    options=snapshot.get('providerRequestOptions') or request_options(config['model'])
    contract=output_contract(ScriptReport.model_json_schema(),options['outputMode'],snapshot)
    schema=output_contract(ScriptReport.model_json_schema(),'json_schema',snapshot)['response_format']['json_schema']['schema']
    system=snapshot['prompt']['content']+'\n报告最外层必须是单个 JSON 对象，禁止使用数组包裹报告。\nJSON Schema:'+json.dumps(schema,ensure_ascii=False)
    material={k:snapshot.get(k) for k in ('paragraphMode','reviewRulesText','rules','paramsCheck','script','scriptBlocks','parseMeta','multimodal','quoteSources')}
    material=provider_material(snapshot,material)
    if snapshot.get('ruleReferenceVersion'):
        system+='\n本次rule_id只填写rules中的R1、R2等短引用；image_checks.rule_ids和mandatory_checks.rule_id同样使用短引用。不得输出rule_number，由服务端按冻结规则回填。'
    # 发给模型的图片清单只保留短引用与位置：长哈希既容易被抄错（曾整次审核因此判失败），也白占 token。
    if isinstance(material.get('multimodal'),dict) and material['multimodal'].get('images'):
        material={**material,'multimodal':{**material['multimodal'],'images':[
            {k:v for k,v in {'ref':image.get('ref') or image.get('id'),'locations':image.get('locations',[]),
                             'purpose':image.get('purpose','unknown'),'contexts':image.get('contexts',[]),
                             'mimeType':image.get('mimeType'),'width':image.get('width'),'height':image.get('height')}.items()
             if v is not None} for image in material['multimodal']['images']]}}
    content=[{'type':'text','text':json.dumps(material,ensure_ascii=False)}]
    for image in snapshot.get('_imageInputs',[]):
        content.extend([{'type':'text','text':snapshot.get('_mediaLabel','下一张为脚本内嵌图片，ref=')+(image.get('ref') or image['id'])},
                        {'type':'image_url','image_url':{'url':image['dataUrl']}}])
    body={'model':config['model'],'messages':[{'role':'system','content':system},{'role':'user','content':content}],
          'stream':False,'reasoning_effort':'none','temperature':options.get('temperature',0),'max_tokens':options['maxTokens'],**contract}
    started=time.monotonic()
    result={'attemptCount':1,'requestOptions':options,'promptHash':prompt_hash(system),'requestedModel':config['model'],
            'requestHash':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),
            'requestParameters':{k:v for k,v in body.items() if k!='messages'}}
    persist=snapshot.get('_persistOutput',lambda saved:None)
    try:
        with httpx.Client(timeout=httpx.Timeout(SCRIPT_READ_TIMEOUT_SECONDS,connect=15,write=60,pool=15),follow_redirects=False) as client:
            response=client.post(config['endpoint'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+secret},json=body)
    except httpx.HTTPError as exc:
        result.update(elapsedMs=round((time.monotonic()-started)*1000),diagnostics={'errorType':type(exc).__name__})
        code='PROVIDER_READ_TIMEOUT' if isinstance(exc,httpx.ReadTimeout) else 'PROVIDER_CONNECT_TIMEOUT' if isinstance(exc,httpx.ConnectTimeout) else 'TRANSPORT_ERROR'
        result['providerError']={'code':code,'message':'模型连接或等待返回失败，是否计费未知；未自动重试'}
        persist(result)
        return result
    result.update(elapsedMs=round((time.monotonic()-started)*1000),providerHttpStatus=response.status_code,
                  rawProviderResponse=response.text.replace(secret,'[REDACTED]'))
    persist(result)
    try:
        data=response.json()
    except ValueError:
        data={}
    if not isinstance(data,dict):data={}
    request_id=response.headers.get('x-request-id') or data.get('id')
    result['providerRequestIds']=[request_id] if request_id else []
    result['usage']=[data.get('usage')]
    result['returnedModel']=data.get('model')
    if response.status_code!=200:
        detail=data.get('error') or {};detail=detail if isinstance(detail,dict) else {}
        message=str(detail.get('message') or '服务商拒绝请求').replace(secret,'[REDACTED]')[:1000]
        result['providerError']={'code':'TRANSPORT_ERROR' if response.status_code in (408,429) or response.status_code>=500 else f'PROVIDER_HTTP_{response.status_code}',
                                 'message':f'模型服务返回 HTTP {response.status_code}：{message}；未自动重试'}
        result['diagnostics']={'errorType':'ProviderHttpError','httpStatus':response.status_code,'elapsedMs':result['elapsedMs']}
        persist(result)
        return result
    choices=data.get('choices')
    choice=choices[0] if isinstance(choices,list) and choices and isinstance(choices[0],dict) else {}
    message=choice.get('message') or {};message=message if isinstance(message,dict) else {}
    raw=message.get('content') or ''
    tool_calls=message.get('tool_calls') or []
    protocol_error=None
    if options['outputMode']=='tool':
        tool=tool_calls[0] if isinstance(tool_calls,list) and len(tool_calls)==1 and isinstance(tool_calls[0],dict) else {}
        function=tool.get('function') or {};function=function if isinstance(function,dict) else {}
        if function.get('name')!='submit_script_report':
            protocol_error={'code':'OUTPUT_SCHEMA_INVALID','message':'未返回唯一的 submit_script_report 工具调用'}
            raw=''
        else:raw=function.get('arguments') or ''
    if not isinstance(raw,str):
        protocol_error={'code':'OUTPUT_SCHEMA_INVALID','message':'模型报告内容不是文本'}
        raw=json.dumps(raw,ensure_ascii=False)
    result['modelResponse']=raw.replace(secret,'[REDACTED]')
    result['finishReason']=choice.get('finish_reason')
    # The database commit happens before any interpretation of model content.
    persist(result)
    if protocol_error:
        result['outputError']=protocol_error
        return result
    try:
        result['report']=parse_output(result['modelResponse'],result['finishReason'])
    except OutputError as exc:
        result['outputError']={'code':exc.code,'message':str(exc),'details':exc.details}
    return result


def revalidate_saved_script_report(settings,review_id,idempotency_key=None):
    """Validate a retained supplier response locally; no provider request or input changes."""
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM workflow_script_reviews WHERE id=?',(review_id,)).fetchone()
        if not row:raise ApiError(404,'RESOURCE_NOT_FOUND','审核记录不存在')
        task=authorize_task(db,row['task_id']);require_task_writable(task)
        scope=f'workflow:revalidate:{review_id}';fingerprint=hashlib.sha256(review_id.encode()).hexdigest()
        if idempotency_key:
            previous=replay(db,scope,idempotency_key,fingerprint)
            if previous:return previous
        if not row or row['status']!='failed' or row['error_code'] not in {'SCRIPT_REPORT_INVALID','OUTPUT_INVALID_JSON','OUTPUT_SCHEMA_INVALID','RULE_REF_INVALID','EVIDENCE_MISSING'}:
            raise ApiError(409,'REVALIDATION_NOT_AVAILABLE','只有已保留模型返回的格式校验失败记录可重新校验')
        result=json.loads(row['result_json'] or '{}');raw=(result.get('modelResponse') or '').strip()
        if not raw:raise ValueError('No stored response')
        if raw.startswith('```') and raw.endswith('```'):raw=raw.split('\n',1)[-1].rsplit('```',1)[0].strip()
        repaired=False
        try:value=parse_output(raw,result.get('finishReason'))
        except OutputError as exc:
            candidate=repair_output(raw) if exc.code=='OUTPUT_INVALID_JSON' else None
            if not candidate:raise
            value,fixed=candidate;repaired=True
        value=normalize_report_envelope(value,result)
        snapshot=json.loads(row['snapshot_json'])
        value=normalize_references(value,snapshot,result)
        value,reference_recovery=recover_legacy_reference(value,snapshot)
        result['report']=validate_script_report(value,snapshot)
        if repaired:
            result['recovery']={'repaired':True,'requiresManualReview':True,'status':'NEEDS_MANUAL_REVIEW',
                                'method':'paired_cjk_quotes_v1','repairedOutput':fixed,'providerResubmitted':False}
        if reference_recovery:
            if result.get('recovery'):reference_recovery['additionalRecovery']=result['recovery']
            result['recovery']=reference_recovery
        result.pop('partialReport',None)
        returned={item['rule_id'] for item in result['report']['findings']}
        known={rule['id'] for rule in snapshot['rules']}
        result['coverage']={'totalRules':len(known),'validatedRules':len(known & returned),'pendingRuleIds':sorted(known-returned)}
        result['revalidation']={'method':'partial_quote_evidence_v3','previousErrorCode':row['error_code'],'previousDiagnostics':result.pop('diagnostics',None),'validatedAt':now(),'providerResubmitted':False}
        if reference_recovery:result['revalidation']['referenceRecovery']=reference_recovery
        manual=result.get('recovery',{}).get('requiresManualReview',False)
        db.execute('UPDATE workflow_script_reviews SET status=?,result_json=?,error_code=?,error_message=?,updated_at=? WHERE id=?',
                   ('failed' if manual else 'succeeded',json.dumps(result,ensure_ascii=False),'NEEDS_MANUAL_REVIEW' if manual else None,
                    '本地格式修复候选已保存，须人工核对，不能自动通过' if manual else None,now(),review_id))
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',(uuid4().hex,'local-reviewer','script_review',review_id,'saved_response_revalidated','demo',json.dumps(result['revalidation']),now()))
        view=review_view(db.execute('SELECT * FROM workflow_script_reviews WHERE id=?',(review_id,)).fetchone())
        if idempotency_key:remember(db,scope,idempotency_key,fingerprint,view)
        return view


def decide(settings,task_id,payload,key):
    scope=f'workflow:decision:{task_id}'
    fingerprint=hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        task=authorize_task(db,task_id)
        prior=replay(db,scope,key,fingerprint)
        if prior:
            return prior
        require_task_writable(task)
        view=state(db,task_id)
        if payload.context_key!=view['contextKey']:
            raise ApiError(409,'WORKFLOW_INPUT_CHANGED','脚本或规则已变化，请刷新')
        if not payload.note.strip() or not payload.reviewed_material:
            raise ApiError(400,'REVIEW_CONFIRMATION_REQUIRED','请填写理由，并确认已核对相关材料与证据')
        if payload.scope=='script':
            current=view['scriptReview']
            if not view['rulesReady'] or not current or current['id']!=payload.target_id or current['status']!='succeeded':
                raise ApiError(409,'SCRIPT_REVIEW_REQUIRED','需要当前脚本的有效 AI 审核报告后才能作品牌决定')
            if payload.decision=='approved' and not view['brandReviewAllowed']:
                acceptance = view['scriptAcceptance']
                pending = {c['rule_id'] for c in acceptance['checks'] if c['verdict'] in ('needs_confirmation', 'pending')}
                if acceptance['status']!='needs_confirmation' or not pending or set(payload.confirmed_rule_ids)!=pending:
                    raise ApiError(409,'MANDATORY_CHECKS_NOT_PASSED','请逐项确认待确定必须项；明确未通过或报告不完整时不能审核通过')
            stage='video' if payload.decision=='approved' else 'script'
        else:
            if not view['videoReviewReady'] or view['latestVideoRunId']!=payload.target_id:
                raise ApiError(409,'VIDEO_REVIEW_REQUIRED','需要当前资料和规则下的最新视频审核结果')
            if payload.decision=='approved' and not view['videoComponentAcceptance']['passed']:
                raise ApiError(409,'VIDEO_MANDATORY_CHECKS_NOT_PASSED','视频必须项存在明确不合规或尚未配置，不能确认通过；可退回修改')
            stage='completed' if payload.decision=='approved' else 'video'
        identity=uuid4().hex;stamp=now()
        db.execute('INSERT INTO workflow_decisions VALUES (?,?,?,?,?,?,?,?,?)',
                   (identity,task_id,payload.scope,payload.target_id,view['contextKey'],payload.decision,payload.note.strip(),'local-reviewer',stamp))
        if payload.scope=='script' and payload.decision=='approved':
            pending = {c['rule_id'] for c in view['scriptAcceptance']['checks'] if c['verdict'] in ('needs_confirmation', 'pending')}
            db.executemany('INSERT INTO workflow_script_confirmations (decision_id,rule_id) VALUES (?,?)', [(identity, rule_id) for rule_id in pending & set(payload.confirmed_rule_ids)])
        db.execute('UPDATE creator_tasks SET stage=?,revision=revision+1,updated_at=? WHERE id=?',(stage,stamp,task_id))
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',(uuid4().hex,'local-reviewer','workflow',task_id,payload.scope+'_'+payload.decision,'demo',payload.model_dump_json(),stamp))
        value={'id':identity,'stage':stage};remember(db,scope,key,fingerprint,value)
        return value


@router.get('/tasks/{task_id}/workflow',response_model=Envelope[dict])
def workflow_view(task_id:str,request:Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        data=state(db,task_id)
    return Envelope(data=data,request_id=request.state.request_id)


@router.get('/workflow/script-reviews/{review_id}/images/{image_id}')
def script_image(review_id:str,image_id:str,request:Request):
    """Expose only frozen review images, revalidated against the immutable source."""
    import base64
    local_only(request)
    settings=request.app.state.settings
    with connection(settings.database_path) as db:
        row=db.execute('SELECT * FROM workflow_script_reviews WHERE id=?',(review_id,)).fetchone()
        if not row:
            raise ApiError(404,'RESOURCE_NOT_FOUND','审核记录不存在')
        authorize_task(db,row['task_id'])
        frozen=json.loads(row['snapshot_json']).get('multimodal',{}).get('images',[])
        expected=next((im for im in frozen if im['id']==image_id),None)
        if not expected:
            raise ApiError(404,'RESOURCE_NOT_FOUND','图片不属于本次审核')
        script=script_version_detail(db,row['script_version_id'])
    manifest,inputs=prepare_media(settings,script)
    actual=next((im for im in manifest['images'] if im['id']==image_id),None)
    if not actual or actual['sha256']!=expected['sha256']:
        raise ApiError(409,'SCRIPT_FILE_CHANGED','图片与审核快照不一致')
    data=next(im['dataUrl'] for im in inputs if im['id']==image_id).split(',',1)[1]
    return Response(base64.b64decode(data),media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.post('/tasks/{task_id}/workflow/script-reviews',response_model=Envelope[dict])
def script_analysis(task_id:str,request:Request,payload:ScriptAnalysisInput,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=analyze(request.app.state.settings,task_id,payload,idempotency_key),request_id=request.state.request_id)


@router.post('/tasks/{task_id}/workflow/decisions',response_model=Envelope[dict])
def brand_decision(task_id:str,request:Request,payload:BrandDecisionInput,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=decide(request.app.state.settings,task_id,payload,idempotency_key),request_id=request.state.request_id)


@router.get('/workflow/review-queue',response_model=Envelope[list[dict]])
def review_queue(request:Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        tasks=db.execute("SELECT t.id,t.creator_name,t.platform,p.id AS project_id,p.name AS project_name FROM creator_tasks t JOIN projects p ON p.id=t.project_id WHERE p.mode='demo' AND p.status!='archived' AND t.archived_at IS NULL ORDER BY t.updated_at DESC").fetchall()
        items=[]
        for task in tasks:
            from .image_posts import queue_item as image_queue_item
            image_item=image_queue_item(db,task)
            if image_item:items.append(image_item)
            view=state(db,task['id'])
            review=view['scriptReview']
            script_decision=db.execute("SELECT target_id FROM workflow_decisions WHERE task_id=? AND scope='script' AND context_key=? ORDER BY rowid DESC LIMIT 1",(task['id'],view['contextKey'])).fetchone()
            if view['scriptSubmissionReady'] and view['scriptSubmitted'] and review and (not script_decision or script_decision['target_id']!=review['id']):
                items.append({**dict(task),'scope':'script','submittedAt':view['scriptSubmittedAt'],'summary':(review.get('result') or {}).get('report',{}).get('summary','')})
            if view['videoReviewReady'] and view['videoSubmitted'] and view['videoComponentAcceptance']['passed'] and not view['finalDecision']:
                run=db.execute('SELECT created_at,result_json FROM evidence_runs WHERE id=?',(view['latestVideoRunId'],)).fetchone()
                result=json.loads(run['result_json'] or '{}')
                items.append({**dict(task),'scope':'video','submittedAt':run['created_at'],'summary':result.get('report',{}).get('summary','')})
    return Envelope(data=items,request_id=request.state.request_id)


@router.post('/workflow/script-reviews/{review_id}/revalidate',response_model=Envelope[dict])
def revalidate(review_id:str,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    """Re-validate the stored supplier response locally; never resends the paid request."""
    local_only(request)
    settings=request.app.state.settings
    try:
        value=revalidate_saved_script_report(settings,review_id,idempotency_key)
    except ValueError:
        raise ApiError(409,'REVALIDATION_NOT_AVAILABLE','已保存的返回仍不完整或不符合约定，原始内容已保留供人工核对；未调用模型')
    return Envelope(data=value,request_id=request.state.request_id)


from app.modules.brief_script.router import ScriptCreate, ScriptVersionCreated
from app.modules.brief_script import script_service


class AutomaticScriptInput(ScriptCreate):
    config_revision: int = Field(ge=1)
    allow_real_call: bool = False


class AutomaticScriptResult(ContractModel):
    script: ScriptVersionCreated
    auto_review: dict | None = None
    auto_review_error: str | None = None


@router.post('/tasks/{task_id}/scripts/submit-and-review',response_model=Envelope[AutomaticScriptResult],status_code=201)
def submit_and_review(task_id:str,request:Request,payload:AutomaticScriptInput,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    if not payload.allow_real_call:
        raise ApiError(400,'REAL_CALL_CONFIRMATION_REQUIRED','提交并自动审核会调用真实模型，请明确启用')
    settings=request.app.state.settings
    with connection(settings.database_path) as db:
        saved=script_service.create_script_version(db,settings,task_id,payload,idempotency_key)
    response=AutomaticScriptResult(script=ScriptVersionCreated(**saved))
    if saved['extractionStatus']!='ready':
        response.auto_review_error='脚本解析失败，未调用AI；版本已保存，请检查文件。'
        return Envelope(data=response,request_id=request.state.request_id)
    auto_key=hashlib.sha256(('automatic-script:'+task_id+':'+idempotency_key).encode()).hexdigest()
    with connection(settings.database_path) as db:
        prior=db.execute('SELECT response_json FROM idempotency_records WHERE scope=? AND key=?',(f'workflow:script-analysis:{task_id}',auto_key)).fetchone()
        if prior:
            identity=json.loads(prior['response_json'])['id']
            response.auto_review=review_view(db.execute('SELECT * FROM workflow_script_reviews WHERE id=?',(identity,)).fetchone())
            return Envelope(data=response,request_id=request.state.request_id)
        _,current,key=context(db,task_id)
    if not current or current['scriptVersionId']!=saved['scriptVersionId']:
        response.auto_review_error='已有更新的脚本版本，本版本已保存，不再自动发送AI。'
    else:
        try:
            response.auto_review=analyze(settings,task_id,ScriptAnalysisInput(script_version_id=saved['scriptVersionId'],config_revision=payload.config_revision,context_key=key,allow_real_call=True),auto_key)
        except ApiError as exc:
            response.auto_review_error=exc.message
    return Envelope(data=response,request_id=request.state.request_id)


@router.get('/creator/tasks',response_model=Envelope[list[dict]])
def creator_tasks(request:Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        rows=db.execute("SELECT t.id,t.project_id,t.creator_name,t.platform,p.name AS project_name,b.name AS brand_name FROM creator_tasks t JOIN projects p ON p.id=t.project_id JOIN brands b ON b.id=p.brand_id WHERE p.mode='demo' AND p.status!='archived' AND t.archived_at IS NULL ORDER BY t.updated_at DESC,t.id").fetchall()
        items=[]
        for task in rows:
            view=state(db,task['id'])
            from .image_posts import state as image_state
            image_view=image_state(db,task['id'],summary=True)
            from .project_brief import current_brief
            project_brief=current_brief(db,task['project_id'],include_blocks=False)
            source=project_brief['sourceVersionId'] if project_brief else (view['standardSets'].get('script') or {}).get('source_version_id')
            brief=db.execute('SELECT b.title,s.file_id FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id WHERE s.id=? AND b.project_id=?',(source,task['project_id'])).fetchone() if source else None
            from .discussions import progress_item
            progress = progress_item(db, task, view)
            script_decision=next((h for h in view['history'] if h['scope']=='script' and h['context_key']==view['contextKey'] and view['scriptReview'] and h['target_id']==view['scriptReview']['id']),None)
            video=db.execute('SELECT created_at,file_id,status FROM evidence_runs WHERE id=?',(view['latestVideoRunId'],)).fetchone() if view['latestVideoRunId'] else None
            image_status=('approved' if image_view['brandApproved'] else 'returned' if (image_view['decision'] or {}).get('decision')=='returned' else
                          'reviewing' if image_view['reviewStatus'] in ('queued','processing') else 'call_failed' if image_view['reviewStatus'] and image_view['reviewStatus']!='succeeded' else
                          'pending_brand' if image_view['brandReviewAllowed'] else 'needs_response' if image_view['reviewStatus']=='succeeded' else 'pending_review' if image_view['currentVersion'] else 'not_submitted')
            deliveries={
                'script':{'status':'returned' if script_decision and script_decision['decision']=='returned' else progress['script']['status'],
                          'hasMaterial':bool(view['currentScript']),'hasReview':bool(view['scriptReview']),
                          'reviewStatus':view['scriptReview']['status'] if view['scriptReview'] else None,
                          'submitted':bool(view['scriptSubmitted'] or script_decision),'submittedAt':view['scriptSubmittedAt'],
                          'uploadedAt':view['currentScript'].get('createdAt') if view['currentScript'] else None,
                          'approved':bool(view['scriptApproval'])},
                'video':{'status':'returned' if (view['finalDecision'] or {}).get('decision')=='returned' else progress['video']['status'],
                         'hasMaterial':bool(video and video['file_id']),'hasReview':bool(video),'reviewStatus':video['status'] if video else None,
                         'submitted':bool(view['videoSubmitted'] or view['finalDecision']),'submittedAt':view['videoSubmittedAt'],
                         'uploadedAt':video['created_at'] if video else None,'approved':(view['finalDecision'] or {}).get('decision')=='approved'},
                'image':{'status':image_status,'submitted':bool(image_view['brandReviewAllowed'] or image_view['decision']),
                         'hasMaterial':bool(image_view['currentVersion']),'hasReview':bool(image_view['reviewStatus']),'reviewStatus':image_view['reviewStatus'],
                         'submittedAt':image_view['reviewCreatedAt'] if image_view['brandReviewAllowed'] or image_view['decision'] else None,
                         'uploadedAt':image_view['currentVersion']['createdAt'] if image_view['currentVersion'] else None,'approved':image_view['brandApproved']},
            }
            items.append({**dict(task),'scriptProgress':progress['script']['status'],'videoProgress':progress['video']['status'],'briefTitle':brief['title'] if brief else '品牌自定义审核要求','briefFileId':brief['file_id'] if brief else None,
                          'deliveries':deliveries,
                          'rulesReady':view['rulesReady'],'scriptAcceptance':view['scriptAcceptance'],'brandApproved':bool(view['scriptApproval']),
                          'brandDecision':script_decision,
                          'currentVersion':view['currentScript']['versionLabel'] if view['currentScript'] else None,
                          'videoAllowed':view['videoAllowed'],'videoReviewReady':view['videoReviewReady'],
                          'imageRulesReady':image_view['rulesReady'],'imageVersion':image_view['currentVersion']['versionLabel'] if image_view['currentVersion'] else None,
                          'imageAcceptance':image_view['acceptance'],'imageBrandApproved':image_view['brandApproved']})
    return Envelope(data=items,request_id=request.state.request_id)
