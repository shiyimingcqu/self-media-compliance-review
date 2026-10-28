"""Whole-video localization followed by independent, shared-window refinement.

Model observations are claims. Only timestamps, source identity and component
arithmetic are verified by code; no implicit provider retries are performed.
"""
from __future__ import annotations

import base64
import copy
import json
import subprocess
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent import AgentReport, AnalysisFailure, Finding, validate_evidence_references
from .component_runtime import ComponentObservationInput, Observation, aggregate_rule, evaluate

VERSION = 'localized_refinement_v1'
Verdict = Literal['compliant', 'non_compliant', 'needs_confirmation', 'not_applicable']


def interrupt_pipeline(pipeline):
    value=copy.deepcopy(pipeline)
    value['interruptedStage']=value.get('stage');value['stage']='interrupted'
    for batch in value.get('batches',[]):
        if batch['status'] in ('queued','preparing','processing'):batch['status']='interrupted'
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Window(Contract):
    start: float = Field(strict=True, ge=0)
    end: float = Field(strict=True, gt=0)
    reason: str = Field(min_length=1, max_length=2000)


class TimedObservation(Contract):
    start: float = Field(strict=True, ge=0)
    end: float = Field(strict=True, gt=0)
    matched: bool
    certain: bool
    whole_visible: bool
    logo_clear: bool
    occluded: bool
    orientation: Literal['normal', 'mirrored', 'uncertain']
    channel: Literal['visual', 'audio', 'subtitle', 'visible_text']
    text: str = Field(max_length=2000)
    term: str = Field(max_length=200)
    description: str = Field(min_length=1, max_length=2000)


class EvidenceQuote(Contract):
    start: float = Field(strict=True, ge=0)
    end: float = Field(strict=True, gt=0)
    # visual = 只看到画面（造型、滤镜、肤色、表情）；其余三项是可读/可听的逐字证据。
    channel: Literal['visual', 'audio', 'subtitle', 'visible_text']
    text: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1, max_length=2000)


class NodeObservations(Contract):
    component_id: str
    coverage_complete: bool
    observations: list[TimedObservation] = Field(max_length=100)
    notes: str = Field(max_length=3000)


class LocatedFinding(Contract):
    rule_id: str
    verdict: Verdict
    observation_type: Literal['observed', 'absence', 'uncertain']
    description: str = Field(min_length=1, max_length=10000)
    coverage_complete: bool
    windows: list[Window]
    components: list[NodeObservations]
    # 主观判断类规则要定不合规，必须给出逐字证据（口播/字幕/画面文字），见 subjective_verdict_gate。
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list, max_length=20)


class LocatedReport(Contract):
    findings: list[LocatedFinding] = Field(max_length=60)


def _media(path, url=None):
    return {'type': 'video_url', 'video_url': {'url': url or 'data:video/mp4;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}}


def _schema():
    return LocatedReport.model_json_schema()


# 每个阶段（整片定位 / 单批精判）最多尝试次数：模型偶发格式跑偏时重发同一份请求，
# 不追加内容也不改判据，所以重试不会引入新的判断依据。
STAGE_ATTEMPTS = max(1, min(int(__import__('os').getenv('REVIEW_VIDEO_STAGE_ATTEMPTS', '3')), 3))


def with_rule_refs(rules):
    """给本批规则挂短引用 ref（r1、r2…）并返回副本。

    规则 id 是 `video-` 加 32 位十六进制，对模型毫无语义，逐字复述会稳定出错——
    真实任务里一条 id 尾部掉了 7 个字符，整批精判因此作废。与图片 ref 同一套路：
    模型只引用 r 号，服务端还原成真实 id。
    """
    out = []
    for index, rule in enumerate(rules, 1):
        copy_rule = dict(rule)
        copy_rule['ref'] = f'r{index}'
        out.append(copy_rule)
    return out


def rule_reference_map(rules):
    """ref / 真实 id → 真实 id。历史快照没有 ref，所以 id 本身也必须在表里。"""
    mapping = {}
    for rule in rules:
        real = rule.get('id')
        if not real:
            continue
        mapping[real] = real
        ref = rule.get('ref')
        if ref:
            mapping.setdefault(ref, real)
    return mapping


def _model_rules(rules):
    """发给模型的规则清单：保留短引用与判据，剥掉长 id 与内部提示词字段。

    模型不需要、也不应该去抄 `video-<32位hex>` 这种不透明串；它只需要 r1/r2。
    """
    keep = ('ref', 'text', 'criterion', 'params', 'category', 'importance', 'layer', 'baselineKey', 'componentPlan')
    cleaned = []
    for rule in rules:
        item = {key: rule[key] for key in keep if key in rule}
        plan = item.get('componentPlan')
        if isinstance(plan, dict):
            item['componentPlan'] = {**plan, 'nodes': [{k: v for k, v in node.items() if k != 'prompt'}
                                                       for node in plan.get('nodes', [])]}
        cleaned.append(item)
    return cleaned


def resolve_rule_refs(value, rules):
    """把模型返回的 rule_id（可能是 r1 短引用）统一还原成真实 id。"""
    mapping = rule_reference_map(rules)
    unknown = []
    for row in value.get('findings') or []:
        if not isinstance(row, dict):
            continue
        given = row.get('rule_id')
        if given in mapping:
            row['rule_id'] = mapping[given]
        else:
            unknown.append(given)
    return unknown


def parse_stage_arguments(raw):
    """把模型提交的 arguments 解成对象，容忍两类常见跑偏（只还原结构，不改内容）。

    ① 整个 arguments 外层又被包了一层 JSON 字符串；
    ② findings 被当成字符串返回（`{"findings": "\n[...]"}`）——再解一次。
    解不出来照旧抛错，绝不猜内容。
    """
    value = json.loads(raw) if isinstance(raw, str) else raw
    for _ in range(2):
        if isinstance(value, str):
            value = json.loads(value)
        else:
            break
    if not isinstance(value, dict):
        raise ValueError('结构化观察不是对象')
    if isinstance(value.get('findings'), str):
        value = {**value, 'findings': json.loads(value['findings'])}
    if not isinstance(value.get('findings'), list):
        raise ValueError('结构化观察缺少findings')
    return value


def _request(complete, messages, stage):
    """提交并解析本阶段的结构化观察；格式不合法时最多重发 STAGE_ATTEMPTS 次。

    返回 (value, reply, attempts)。全部尝试都失败时 value=None，由调用方决定
    是整次失败（首轮）还是只作废本批（精判），原始回复仍会记账。
    """
    name = 'submit_' + stage
    tool = {'type': 'function', 'function': {'name': name, 'description': '提交本阶段的结构化观察', 'parameters': _schema()}}
    reply = None
    for _ in range(STAGE_ATTEMPTS):
        reply = complete(messages, [tool])
        try:
            message = reply['choices'][0]['message']
            calls = message.get('tool_calls') or []
            if len(calls) != 1 or calls[0].get('function', {}).get('name') != name:
                raise AnalysisFailure('LOCALIZED_OUTPUT_INVALID', '未返回指定的结构化观察')
            raw = calls[0]['function']['arguments']
            if len(raw if isinstance(raw, str) else json.dumps(raw)) > 200_000:
                raise ValueError('结构化观察超出处理预算')
            return parse_stage_arguments(raw), reply, STAGE_ATTEMPTS
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError, AnalysisFailure):
            continue
    return None, reply, STAGE_ATTEMPTS


def normalize_window(window, duration_ms, offset_ms=0):
    """把窗口统一成原片绝对时间：精判片段里模型可能给片段内相对时间，也可能直接给原片时间。

    返回 (start_ms, end_ms) 或 None。offset_ms=0 表示整片首轮，窗口本就是绝对时间。
    """
    start, end = round(window.start * 1000), round(window.end * 1000)
    clip_start, clip_end = offset_ms, offset_ms + duration_ms
    candidates = [(start + offset_ms, end + offset_ms), (start, end)] if offset_ms else [(start, end)]
    for a, b in candidates:
        if clip_start <= a < b <= clip_end:
            return a, b
    return None


def parse_findings(value, rules, duration_ms, *, partial=False, offset_ms=0, downgrade_incomplete=True, require_components=True):
    """Validate windows independently so one bad timestamp never erases other rules."""
    rows = {}; errors = {}
    for raw in value['findings']:
        # 精判阶段一条规则写坏（未知 id / 重复）只作废那一条，其余结论仍可用；
        # 首轮没有"部分结果"可言，仍按整次失败处理。
        if not isinstance(raw, dict) or raw.get('rule_id') not in rules:
            if not partial:raise ValueError('返回了未知规则')
            continue
        rid = raw['rule_id']
        if rid in rows:
            if not partial:raise ValueError('返回了重复规则')
            errors[rid] = '该规则被重复返回，已保留首次结果'
            continue
        clean = dict(raw); clean['windows'] = []; clean['components'] = []
        clean['evidence_quotes'] = []
        try:
            row = LocatedFinding.model_validate(clean)
        except ValueError:
            if not partial:raise
            errors[rid] = '精判字段无效'; continue
        if not isinstance(raw.get('windows'),list) or not isinstance(raw.get('components'),list):
            errors[rid] = '窗口或组件列表缺失'
        spans = []; bad_window = False
        for window in raw['windows'] if isinstance(raw.get('windows'),list) else []:
            try:
                w = Window.model_validate(window)
                span = normalize_window(w, duration_ms, offset_ms)
                if span is None:raise ValueError('窗口超出视频范围')
                spans.append((span[0], span[1], w.reason))
            except (ValueError, TypeError, OverflowError, AttributeError):
                bad_window = True
        # 模型给的窗口可能乱序或重叠，先排序合并；只有真正落在视频外才算定位失败。
        spans.sort(key=lambda item: (item[0], item[1]))
        merged = []
        for a, b, reason in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b, reason])
        row.windows = [Window(start=a / 1000, end=b / 1000, reason=reason) for a, b, reason in merged]
        if bad_window:
            errors[rid] = '定位窗口超出视频范围'
        expected = {n['id'] for n in (rules[rid].get('componentPlan') or {}).get('nodes', [])}
        seen = set()
        for component in raw['components'] if isinstance(raw.get('components'),list) else []:
            try:
                node = NodeObservations.model_validate(component)
                if node.component_id not in expected or node.component_id in seen:raise ValueError('组件引用无效')
                seen.add(node.component_id)
                for obs in node.observations:
                    if not 0 <= round(obs.start*1000) < round(obs.end*1000) <= duration_ms:raise ValueError('组件时间无效')
                row.components.append(node)
            except (ValueError, TypeError, OverflowError):
                errors[rid] = '组件观察无效或超出视频范围'
        if require_components and (seen != expected or len(row.components) != len(expected)):
            errors[rid] = '组件观察缺失或无效'
        # 证据引用与窗口同一时间基准：片段内相对秒数优先，越界再按原片绝对时间解释。
        for quote in raw.get('evidence_quotes') or []:
            try:
                item = EvidenceQuote.model_validate(quote)
            except (ValueError, TypeError):
                continue
            span = normalize_window(Window(start=item.start, end=item.end, reason=item.text), duration_ms, offset_ms)
            if span is None:
                continue
            row.evidence_quotes.append(EvidenceQuote(start=span[0] / 1000, end=span[1] / 1000,
                                                     channel=item.channel, text=item.text, description=item.description))
        if row.observation_type == 'absence':row.windows = []
        # 只有整片首轮才用"未声明全片覆盖"降级；精判片段的 coverage_complete 只代表这个片段。
        if downgrade_incomplete and not row.coverage_complete and row.verdict=='compliant' and not row.windows:
            row.verdict='needs_confirmation'
        if rid in errors:
            row.verdict = 'needs_confirmation'; row.coverage_complete = False
        rows[rid] = row
    missing = set(rules) - set(rows)
    if missing and not partial:raise ValueError('首轮规则缺项')
    for rid in missing:errors[rid] = '精判规则缺项'
    return rows, errors


# 可逐字核对的证据通道：口播、字幕、画面文字。visual 只是观感，不足以定罪。
VERBATIM_CHANNELS = ('audio', 'subtitle', 'visible_text')
SUBJECTIVE_GATE_NOTE = ('按平台口径，容貌/歧视/导向这类主观判断必须有画面文字、字幕或口播的逐字证据才能定不合规；'
                        '本次只给了视觉观感依据，因此转为待品牌确认，不直接判违规。')


def subjective_verdict_gate(rule, verdict, quotes):
    """主观判断类规则的举证门槛：没有逐字证据就不许判不合规。

    返回 (verdict, note)。模型看滤镜/造型/肤色的观感判断并不可靠（实测把抖音常见的
    变脸娱乐特效判成"肤色歧视"），但读字幕、认画面文字、听口播是可靠的——所以要求
    这类结论必须引用可核对的原文，否则降级为待确认交人工，不阻断也不放过。
    """
    from app.modules.workflow.video_layers import subjective_rule
    if verdict != 'non_compliant' or not subjective_rule(rule):
        return verdict, None
    if any((quote.get('channel') if hasattr(quote, 'get') else quote.channel) in VERBATIM_CHANNELS
           and ((quote.get('text') if hasattr(quote, 'get') else quote.text) or '').strip() for quote in quotes or []):
        return verdict, None
    return 'needs_confirmation', SUBJECTIVE_GATE_NOTE


def plan_batches(findings, duration_ms, skip=frozenset(), *, whole_context=False):
    intervals = []
    for rid, finding in findings.items():
        # skip：只做整片判断、不参与逐段精判的规则（如"与已通过脚本基本匹配"）。
        if rid in skip:continue
        if finding.verdict not in ('non_compliant', 'needs_confirmation') or finding.observation_type == 'absence':continue
        for w in finding.windows:
            intervals.append((max(0, round(w.start*1000)-2000), min(duration_ms, round(w.end*1000)+2000), rid, w.reason))
    # A short whole-video question must not become several context-starved calls.
    # Evidence-only clipping keeps its usual 30-second limit (whole_context=False).
    if whole_context and 0 < duration_ms <= 90_000 and any(
        rid not in skip and finding.verdict in ('non_compliant','needs_confirmation')
        and finding.observation_type!='absence' and any(round(w.start*1000)==0 and round(w.end*1000)==duration_ms for w in finding.windows)
        for rid,finding in findings.items()
    ):
        targets={}
        for _,_,rid,reason in intervals:targets.setdefault(rid,[]).append(reason)
        return [{'id':'batch-1','startMs':0,'endMs':duration_ms,'scope':'whole_video',
                 'ruleIds':list(targets),'tasks':{rid:list(dict.fromkeys(reasons)) for rid,reasons in targets.items()},'status':'queued'}]
    spans = []
    for a, b, _, _ in sorted(intervals):
        if spans and a <= spans[-1][1]:spans[-1][1] = max(b, spans[-1][1])
        else:spans.append([a,b])
    batches = []
    for start, end in spans:
        while start < end:
            stop = min(start+30_000, end)
            targets = {}
            for a,b,rid,reason in intervals:
                if a < stop and b > start:targets.setdefault(rid, []).append(reason)
            batches.append({'id': f'batch-{len(batches)+1}', 'startMs':start, 'endMs':stop,
                            'ruleIds':list(targets), 'tasks':{rid:list(dict.fromkeys(reasons)) for rid,reasons in targets.items()}, 'status':'queued'})
            if stop == end:break
            start = stop-2000
    return batches


def render_internal_clip(session, batch):
    """Internal clips do not consume the user-facing evidence artifact budget."""
    path = session.output_dir / ('refine-' + batch['id'] + '.mp4')
    args = [session.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-i', str(session.video),
            '-ss', f"{batch['startMs']/1000:.3f}", '-t', f"{(batch['endMs']-batch['startMs'])/1000:.3f}",
            '-map', '0:v:0', '-map', '0:a?', '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-movflags', '+faststart', str(path)]
    try:
        result = subprocess.run(args, capture_output=True, timeout=120)
        if result.returncode or not path.is_file() or not path.stat().st_size:raise ValueError('精判裁片失败')
    except BaseException:
        path.unlink(missing_ok=True); raise
    return path


def _observations(node, offset=0):
    result = []
    for o in node.observations:
        value = o.model_dump(); start = value.pop('start'); end = value.pop('end')
        result.append(Observation(start_ms=round(start*1000)+offset, end_ms=round(end*1000)+offset, **value).model_dump())
    return result


def replace_range(observations, start, end, replacements):
    """Replace only a reviewed interval; preserve everything outside it."""
    result = []
    for obs in observations:
        if obs['end_ms'] <= start or obs['start_ms'] >= end:result.append(obs); continue
        if obs['start_ms'] < start:result.append({**obs, 'end_ms':start})
        if obs['end_ms'] > end:result.append({**obs, 'start_ms':end})
    unique = {json.dumps(o,sort_keys=True):o for o in result + replacements}
    return sorted(unique.values(),key=lambda o:(o['start_ms'],o['end_ms']))


def attach_gate_frames(session, findings, located) -> list[str]:
    """被举证门槛降级的规则，按模型定位的区间补一帧画面供品牌直接看。

    降级只改变结论强度，不该让品牌失去可看的东西：没有这张图，卡片上就只剩一句
    "某段有争议"却无从对照。截图失败（例如环境缺 ffmpeg）不影响结论，与既有取证一致。
    """
    created = []
    for finding in findings:
        if finding.verdict != 'needs_confirmation' or finding.evidence_ids:
            continue
        spans = located.get(finding.rule_id) or []
        if not spans:
            continue
        middle = int((min(a for a, _ in spans) + max(b for _, b in spans)) / 2)
        try:
            artifact = session.screenshot(middle, finding.id)
        except Exception:
            continue
        finding.evidence_ids.append(artifact['id'])
        created.append(artifact['id'])
    return created


def promote_clip(session, batch, path, rid):
    artifact=next((a for a in session.artifacts if a.get('batchId')==batch['id']),None)
    if artifact is None:
        artifact={'id':uuid4().hex,'kind':'clip','issueId':'issue-'+rid,'startMs':batch['startMs'],'endMs':batch['endMs'],
                  'filename':path.name,'sourceSha256':session.sha256,'hasAudio':session.has_audio,'batchId':batch['id']}
        session.artifacts.append(artifact)
    return artifact['id']


def attach_pass_evidence(session, findings, first, refined, batches, internal, combinations):
    """Retain supporting original clips without making another model request.

    Negative/global absence checks have a scope record, never an arbitrary clip.
    """
    candidates={};notes={};errors={}
    by_batch={b['id']:b for b in batches}
    for finding in findings:
        if finding.verdict!='compliant':continue
        rid=finding.rule_id
        for review in refined[rid]:
            batch=by_batch[review['batchId']]
            if review.get('coverage_complete') and batch['id'] in internal and batch['status'] in ('succeeded','partial'):
                finding.evidence_ids.append(promote_clip(session,batch,internal[batch['id']],rid))
        if finding.evidence_ids:
            finding.evidence_ids=list(dict.fromkeys(finding.evidence_ids));continue
        windows=list(first[rid].windows) if first[rid].verdict=='compliant' else []
        for node in combinations.get(rid,{}).get('components',[]):
            for start,end in node.get('intervals',[]):
                if 0<=start<end<=session.duration_ms:
                    windows.append(Window(start=start/1000,end=end/1000,reason='组件记录中的有效通过区间'))
        if windows:
            candidates[rid]=first[rid].model_copy(update={'windows':windows,'verdict':'needs_confirmation','observation_type':'observed'})
        else:
            notes[rid]={'status':'scope_record' if finding.observation_type=='absence' else 'missing',
                        'startMs':0,'endMs':session.duration_ms,'coverageClaimed':first[rid].coverage_complete,
                        'note':'全片范围检查记录；单段不能证明全片未出现' if finding.observation_type=='absence' else '模型未提供可定位的通过区间，请结合原片核对'}
    for batch in plan_batches(candidates,session.duration_ms):
        batch['id']='pass-'+batch['id']
        try:
            path=render_internal_clip(session,batch)
            for rid in batch['ruleIds']:
                finding=next(f for f in findings if f.rule_id==rid)
                finding.evidence_ids.append(promote_clip(session,batch,path,rid))
        except Exception:
            for rid in batch['ruleIds']:
                errors[rid]='通过证据裁片未完成，原判断保留，请核对原片'
    for finding in findings:finding.evidence_ids=list(dict.fromkeys(finding.evidence_ids))
    return notes,errors


def _components(session, observations, pending, *, preview=False):
    target = copy.copy(session) if preview else session
    if preview:
        target.component_results = {}; target.save_manifest = lambda:None
    results = {}
    for rule in session.rules.values():
        if not rule.get('componentPlan'):continue
        for node in rule['componentPlan']['nodes']:
            data = observations.get((rule['id'],node['id']), {'coverage_complete':False,'observations':[],'notes':'未获得有效组件观察'})
            # Multiple overlapping clips may add many observations: consolidate identical
            # observations in time before applying the existing per-node contract.
            values = data['observations']
            if len(values)>100:
                values=[]; pending.add(rule['id'])
            payload = ComponentObservationInput(rule_id=rule['id'], component_id=node['id'], issue_id='issue-'+rule['id'],
                coverage_complete=data['coverage_complete'] and rule['id'] not in pending,
                observations=values, notes=data['notes'][:3000])
            evaluate(target,node['kind'],payload)
            computed = target.component_results[(rule['id'],node['id'])]
            if rule['id'] in pending or (node['kind'] in ('exposure_duration','exposure_count') and not payload.coverage_complete):
                computed['verdict']='needs_confirmation';computed['reason']='精判或整片覆盖不足，不能确认该组件结果'
        results[rule['id']] = aggregate_rule(target,rule)
    return results


def run_localized(session, complete, *, video_input: Path, video_url=None, prompts, progress=None, upload_clip=None):
    progress = progress or (lambda _:None)
    state = {'pipelineVersion':VERSION,'stage':'localizing','completedBatches':0,'totalBatches':0,'batches':[], 'decisions':[]}
    usages=[]; request_ids=[]; started=time.monotonic()
    def save():
        state['elapsedMs']=round((time.monotonic()-started)*1000)
        progress(copy.deepcopy(state))
    def remember(reply, attempts=1):
        if reply and reply.get('id'):request_ids.append(reply['id'])
        if reply and reply.get('usage'):usages.append(reply['usage'])
        if attempts and attempts > 1:state.setdefault('retriedStages',[]).append({'attempts':attempts,'requestId':(reply or {}).get('id')})
    def messages(path, payload, prompt_text, refs, url=None):
        if 'isWholeVideo' in payload:
            prompt_text += ('\n范围校验：本请求 isWholeVideo=true，已提供完整原片。不得声称看不到段外或要求评论区、封面等未纳入本条的视频外材料。未见要求内容时，按本条是“必须出现”还是“出现时须正确”判断；确实看不清听不清仍可待确定。'
                            if payload['isWholeVideo'] else '\n范围校验：本请求只含局部片段。仅判断任务窗口中的事实，不以未提供段外画面本身作为不确定原因，也不能从局部未见推断整片缺失。')
        content=[_media(path,url), {'type':'text','text':json.dumps(payload,ensure_ascii=False)}]
        for ref in refs:
            content.extend([{'type':'text','text':json.dumps({k:v for k,v in ref.items() if k!='data'},ensure_ascii=False)},
                            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(ref['data']).decode('ascii')}}])
        return [{'role':'system','content':prompt_text},{'role':'user','content':content}]
    from app.modules.workflow.video_rule_boundaries import assessment_boundaries
    boundaries=assessment_boundaries(session.rules.values())
    state['assessmentBoundaries']=boundaries
    save()
    value, reply, attempts = _request(complete,messages(video_input,{'rules':_model_rules(with_rule_refs(session.rules.values())),'assessmentBoundaries':boundaries,'approvedScript':session.script,'media':session.metadata()},prompts['localize']['content'],session.references,video_url),'localization')
    remember(reply,attempts)
    if value is None:raise AnalysisFailure('LOCALIZED_OUTPUT_INVALID',f'整片定位连续 {attempts} 次未返回可用的结构化观察，已保留原始回复，未再重试')
    resolve_rule_refs(value,with_rule_refs(session.rules.values()))
    first, errors = parse_findings(value,session.rules,session.duration_ms,require_components=False)
    state['localization']={'findings':{k:v.model_dump() for k,v in first.items()},'errors':errors,'requestId':reply.get('id')}
    pending=set(errors); observations={}
    component_prompt=prompts.get('component')
    if component_prompt:
        # 量化组件改为独立一次请求：只把组件规则和它需要的视频范围发过去，结论仍由代码计算。
        from .component_pass import run_component_pass
        try:
            pass_results, pass_meta = run_component_pass(session, complete, prompt=component_prompt['content'],
                                                         video_input=video_input, render_clip=render_internal_clip)
            observations.update(pass_results)
            usages.extend([u for u in pass_meta['usage'] if u]); request_ids.extend(pass_meta['requestIds'])
            state['componentPass']={'status':'succeeded','groups':pass_meta['groups'],'requestIds':pass_meta['requestIds']}
        except Exception as exc:
            state['componentPass']={'status':'failed','error':str(exc)[:200]}
    if not observations:
        for rid,row in first.items():
            for node in row.components:
                observations[(rid,node.component_id)]={'coverage_complete':node.coverage_complete,'observations':_observations(node),'notes':node.notes}
    save()
    # Compute provisional component outcomes without freezing anything in the session.
    preview=_components(session,observations,set(pending),preview=True)
    for rid,result in preview.items():
        if result['verdict'] in ('non_compliant','needs_confirmation'):
            first[rid].verdict=result['verdict']
            if first[rid].observation_type!='absence' and not first[rid].windows:
                related=[o for n in first[rid].components for o in n.observations if not o.certain or o.matched]
                # Windows only from real observations, never manufacture a missing-item clip.
                if related:
                    first[rid].windows=[Window(start=o.start,end=o.end,reason=o.description) for o in related]
    # 脚本一致性类规则（baselineKey=script_match）只做一次全片判断：它需要整片对照，
    # 逐段精判既看不到全貌，又会把同一条规则重复塞进每个批次、白白抬成本。
    from app.modules.workflow.video_layers import whole_video_only as _whole_video_only
    whole_video_only={rid for rid,rule in session.rules.items() if _whole_video_only(rule)}
    # 组件规则已由独立精判给出结论，不再参与逐段精判。
    component_rules={rid for rid,rule in session.rules.items() if rule.get('componentPlan')}
    batches=plan_batches(first,session.duration_ms,skip=whole_video_only|component_rules,whole_context=True)
    state.update(stage='preparing',batches=batches,totalBatches=len(batches));save()
    refined={rid:[] for rid in first}
    internal={}
    settled=set()  # 已经确认违规的规则不再重复精判：后续片段看不到证据只会产生噪音
    for batch in batches:
        remaining=[rid for rid in batch['ruleIds'] if rid not in settled]
        if not remaining:
            batch.update(status='skipped',errors={},findings={},sourceFindings={})
            state['completedBatches']+=1;save();continue
        batch['ruleIds']=remaining
        batch['tasks']={rid:batch['tasks'][rid] for rid in remaining if rid in batch.get('tasks',{})}
        batch['status']='preparing';state['stage']='preparing';save()
        try:
            path=render_internal_clip(session,batch);internal[batch['id']]=path
            batch.update(status='processing',hasAudio=session.has_audio);state['stage']='refining';save()
            selected={rid:session.rules[rid] for rid in batch['ruleIds']}
            refids={identity for r in selected.values() for n in (r.get('componentPlan') or {}).get('nodes',[]) for identity in n['parameters'].get('referenceImageIds',[])}
            refs=[ref for ref in session.references if ref['id'] in refids]
            batch['modelRules']=with_rule_refs(list(selected.values()))
            payload={'rules':_model_rules(batch['modelRules']),'assessmentBoundaries':assessment_boundaries(session.rules.values(),set(selected)),'approvedScript':session.script,
                     'sourceOffsetSeconds':batch['startMs']/1000,'durationSeconds':(batch['endMs']-batch['startMs'])/1000,
                     'fullVideoDurationSeconds':session.duration_ms/1000,
                     'isWholeVideo':batch['startMs']==0 and batch['endMs']==session.duration_ms,
                     'hasAudio':session.has_audio,'tasks':batch['tasks']}
            raw,reply,attempts=_request(complete,messages(path,payload,prompts['refine']['content'],refs,upload_clip(path) if upload_clip else None),'refinement')
            remember(reply,attempts)
            if raw is None:raise AnalysisFailure('LOCALIZED_OUTPUT_INVALID',f'本批连续 {attempts} 次未返回可用的结构化观察')
            resolve_rule_refs(raw,batch['modelRules'])
            rows,bad=parse_findings(raw,selected,batch['endMs']-batch['startMs'],partial=True,
                                     offset_ms=batch['startMs'],downgrade_incomplete=False)
            batch.update(status='succeeded' if not bad else 'partial',requestId=reply.get('id'),findings={k:v.model_dump() for k,v in rows.items()},sourceFindings={},errors=bad)
            pending.update(bad)
            for rid in batch['ruleIds']:
                row=rows.get(rid)
                if row is None or rid in bad:
                    refined[rid].append({'batchId':batch['id'],'verdict':'needs_confirmation',
                                          'description':'这一段对本条的复核结果无效，本条仍按整片初判保留，请重新验收或人工核对该时段。',
                                          'windows':[],'technical_error':bad.get(rid,'精判缺项')});continue
                # parse_findings 已把窗口统一成原片绝对时间，这里不再二次加偏移。
                mapped={'batchId':batch['id'],**row.model_dump()}
                batch['sourceFindings'][rid]=mapped
                refined[rid].append(mapped)
                if row.verdict=='non_compliant' and row.coverage_complete:
                    settled.add(rid)
                if row.verdict in ('needs_confirmation','not_applicable') or not row.coverage_complete:pending.add(rid)
                for node in row.components:
                    if not node.coverage_complete:
                        pending.add(rid);continue
                    key=(rid,node.component_id)
                    old=observations.setdefault(key,{'coverage_complete':False,'observations':[],'notes':''})
                    old['observations']=replace_range(old['observations'],batch['startMs'],batch['endMs'],_observations(node,batch['startMs']))
        except Exception as exc:
            # error 是技术诊断（进"查看诊断"），brandNote 才是给品牌看的话；两者不再混用。
            batch.update(status='failed',errorCode=getattr(exc,'code','REFINEMENT_FAILED'),
                         error='该批次裁片、请求或结果校验失败，未自动重试',
                         brandNote='这一段未能完成复核，本条结论仍按整片初判保留，请重新验收或人工核对该时段。')
            pending.update(batch['ruleIds'])
            for rid in batch['ruleIds']:
                refined[rid].append({'batchId':batch['id'],'verdict':'needs_confirmation','description':batch['brandNote'],'windows':[],'technical_error':batch['error']})
        state['completedBatches']+=1;save()
    state['stage']='finalizing';save()
    combinations=_components(session,observations,pending)
    by_batch={batch['id']:batch for batch in batches}
    def full_review(review):
        batch=by_batch[review['batchId']]
        return batch['startMs']==0 and batch['endMs']==session.duration_ms and review.get('coverage_complete')
    findings=[]
    subjective_notes={}
    for rid,row in first.items():
        rule=session.rules[rid];reviews=refined[rid];evidence=[]
        if reviews:
            confirmed=[r for r in reviews if r['verdict']=='non_compliant' and r.get('coverage_complete')
                       and (r.get('windows') or (full_review(r) and r.get('observation_type')=='absence'))]
            if confirmed:verdict='non_compliant'
            elif rid in pending or any(r['verdict']!='compliant' for r in reviews):verdict='needs_confirmation'
            else:verdict='compliant'
            # 门槛只认"确认违规的那些片段"给出的逐字证据，其他片段的观感描述不算。
            verdict,gate_note=subjective_verdict_gate(rule,verdict,[q for r in confirmed for q in (r.get('evidence_quotes') or [])])
            if gate_note:confirmed=[];subjective_notes[rid]=gate_note
            def review_text(review):
                times='、'.join(f"{w['start']:g}–{w['end']:g}秒" for w in review.get('windows',[]))
                return (f'原片{times}：' if times else '')+review['description']
            # 已确认违规时只保留确认违规的那些片段说明；其他片段"这里没看到"的意见会自相矛盾，不再拼进来。
            shown=confirmed if confirmed else reviews
            description='；'.join(dict.fromkeys(review_text(r) for r in shown))
            if gate_note:description += '；'+gate_note
            observation_type='absence' if confirmed and all(r.get('observation_type')=='absence' for r in confirmed) else 'observed' if confirmed else 'uncertain' if verdict=='needs_confirmation' else 'observed'
            # Promote only confirmed problem clips; internal clips remain diagnostic files.
            for review in confirmed if verdict=='non_compliant' else []:
                if review.get('observation_type')=='absence':continue
                batch=next(b for b in batches if b['id']==review['batchId'])
                evidence.append(promote_clip(session,batch,internal[batch['id']],rid))
        else:
            verdict=row.verdict;description=row.description+'（未经过片段精判）';observation_type=row.observation_type
            if rid in pending:verdict='needs_confirmation'
            verdict,gate_note=subjective_verdict_gate(rule,verdict,row.evidence_quotes)
            if gate_note:
                description += '；'+gate_note
                subjective_notes[rid]=gate_note
        if rid in combinations:
            verdict=combinations[rid]['verdict']
            description += '；'+combinations[rid]['reason']
        if verdict=='non_compliant' and reviews and not evidence and rid not in combinations and observation_type!='absence':
            verdict='needs_confirmation'
        findings.append(Finding(id='issue-'+rid,rule_id=rid,rule_number=rule.get('ruleNumber'),verdict=verdict,description=description,observation_type=observation_type,evidence_ids=list(dict.fromkeys(evidence))))
        state['decisions'].append({'ruleId':rid,'initialVerdict':state['localization']['findings'][rid]['verdict'],
                                  'finalVerdict':verdict,'refined':bool(reviews),'batchIds':[r['batchId'] for r in reviews]})
    coverage_notes={}
    for finding in findings:
        row=first.get(finding.rule_id)
        if not row or finding.verdict!='compliant' or row.coverage_complete or any(full_review(r) for r in refined[finding.rule_id]):continue
        spans='、'.join(f"{w.start:g}–{w.end:g}秒" for w in row.windows)
        coverage_notes[finding.rule_id]=(f'首轮未声明全片覆盖；已核对片段 {spans}，其余时段未覆盖，请品牌核对' if spans
                                         else '首轮未声明全片覆盖，也未给出可定位片段，请品牌核对原片')
    scope_records,evidence_errors=attach_pass_evidence(session,findings,first,refined,batches,internal,combinations)
    state['passEvidenceErrors']=evidence_errors
    mandatory=[f for f in findings if session.rules[f.rule_id].get('importance')!='suggested']
    failures=sum(f.verdict=='non_compliant' for f in mandatory)
    uncertain=sum(f.verdict not in ('compliant','non_compliant') for f in mandatory)
    suggestions=sum(f.verdict!='compliant' for f in findings if session.rules[f.rule_id].get('importance')=='suggested')
    passed=sum(f.verdict=='compliant' for f in findings)
    # 被门槛降级的规则：优先用确认违规片段的位置，其次用首轮定位，给品牌一帧可看的画面。
    gate_windows={rid:[(round(w.start*1000),round(w.end*1000)) for w in (first[rid].windows if rid in first else [])]
                  for rid in subjective_notes}
    for rid in subjective_notes:
        for review in refined.get(rid) or []:
            spans=[(round(w['start']*1000),round(w['end']*1000)) for w in review.get('windows',[])]
            if spans:gate_windows[rid]=spans
    report=AgentReport(findings=findings,summary=f"逐条审查完成：必须项不符合{failures}项、待确认{uncertain}项，建议{suggestions}项，通过{passed}项；完成{len(batches)}批片段精判，最终由品牌核查。")
    attach_gate_frames(session,findings,gate_windows)
    session.evidence_references=validate_evidence_references(report,session.artifacts,session.sha256);session.save_manifest()
    rendered=report.model_dump()
    from app.modules.workflow.video_layers import layer
    for finding in rendered['findings']:
        finding['layer']=layer(session.rules[finding['rule_id']])
        if finding['rule_id'] in scope_records:finding['evidence_basis']=scope_records[finding['rule_id']]
        if finding['rule_id'] in coverage_notes:finding['coverage_note']=coverage_notes[finding['rule_id']]
        if finding['rule_id'] in subjective_notes:finding['judgment_note']=subjective_notes[finding['rule_id']]
        if finding['rule_id'] in evidence_errors:finding['evidence_note']=evidence_errors[finding['rule_id']]
    state['stage']='completed';save()
    return {'pipeline':state,'report':rendered,'componentResults':list(combinations.values()),'artifacts':session.artifacts,
            'evidenceReferences':session.evidence_references,'toolCalls':session.calls,'providerRequestIds':request_ids,'usage':usages,
            'coverageVerified':False,'conclusion':'incomplete','note':'时间与组件计算经代码校验；观察覆盖和判断仍需品牌核查'}
