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


class LocatedReport(Contract):
    findings: list[LocatedFinding] = Field(max_length=60)


def _media(path, url=None):
    return {'type': 'video_url', 'video_url': {'url': url or 'data:video/mp4;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}}


def _schema():
    return LocatedReport.model_json_schema()


def _request(complete, messages, stage):
    name = 'submit_' + stage
    tool = {'type': 'function', 'function': {'name': name, 'description': '提交本阶段的结构化观察', 'parameters': _schema()}}
    reply = complete(messages, [tool])
    message = reply['choices'][0]['message']
    calls = message.get('tool_calls') or []
    if len(calls) != 1 or calls[0].get('function', {}).get('name') != name:
        raise AnalysisFailure('LOCALIZED_OUTPUT_INVALID', '未返回指定的结构化观察，已保留原始回复，未自动重试')
    raw = calls[0]['function']['arguments']
    if len(raw if isinstance(raw,str) else json.dumps(raw))>200_000:
        raise ValueError('结构化观察超出处理预算')
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict) or not isinstance(value.get('findings'), list):
        raise ValueError('结构化观察缺少findings')
    return value, reply


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


def parse_findings(value, rules, duration_ms, *, partial=False, offset_ms=0, downgrade_incomplete=True):
    """Validate windows independently so one bad timestamp never erases other rules."""
    rows = {}; errors = {}
    for raw in value['findings']:
        if not isinstance(raw, dict) or raw.get('rule_id') not in rules:
            raise ValueError('返回了未知规则')
        rid = raw['rule_id']
        if rid in rows:
            raise ValueError('返回了重复规则')
        clean = dict(raw); clean['windows'] = []; clean['components'] = []
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
        if seen != expected or len(row.components) != len(expected):errors[rid] = '组件观察缺失或无效'
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


def plan_batches(findings, duration_ms, skip=frozenset()):
    intervals = []
    for rid, finding in findings.items():
        # skip：只做整片判断、不参与逐段精判的规则（如"与已通过脚本基本匹配"）。
        if rid in skip:continue
        if finding.verdict not in ('non_compliant', 'needs_confirmation') or finding.observation_type == 'absence':continue
        for w in finding.windows:
            intervals.append((max(0, round(w.start*1000)-2000), min(duration_ms, round(w.end*1000)+2000), rid, w.reason))
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
    def remember(reply):
        if reply.get('id'):request_ids.append(reply['id'])
        if reply.get('usage'):usages.append(reply['usage'])
    def messages(path, payload, prompt_text, refs, url=None):
        content=[_media(path,url), {'type':'text','text':json.dumps(payload,ensure_ascii=False)}]
        for ref in refs:
            content.extend([{'type':'text','text':json.dumps({k:v for k,v in ref.items() if k!='data'},ensure_ascii=False)},
                            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(ref['data']).decode('ascii')}}])
        return [{'role':'system','content':prompt_text},{'role':'user','content':content}]
    save()
    value, reply = _request(complete,messages(video_input,{'rules':list(session.rules.values()),'approvedScript':session.script,'media':session.metadata()},prompts['localize']['content'],session.references,video_url),'localization')
    remember(reply)
    first, errors = parse_findings(value,session.rules,session.duration_ms)
    state['localization']={'findings':{k:v.model_dump() for k,v in first.items()},'errors':errors,'requestId':reply.get('id')}
    pending=set(errors); observations={}
    for rid,row in first.items():
        for node in row.components:
            observations[(rid,node.component_id)]={'coverage_complete':node.coverage_complete,'observations':_observations(node),'notes':node.notes}
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
    batches=plan_batches(first,session.duration_ms,skip=whole_video_only)
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
            payload={'rules':list(selected.values()),'approvedScript':session.script,
                     'sourceOffsetSeconds':batch['startMs']/1000,'durationSeconds':(batch['endMs']-batch['startMs'])/1000,
                     'hasAudio':session.has_audio,'tasks':batch['tasks']}
            raw,reply=_request(complete,messages(path,payload,prompts['refine']['content'],refs,upload_clip(path) if upload_clip else None),'refinement')
            remember(reply)
            rows,bad=parse_findings(raw,selected,batch['endMs']-batch['startMs'],partial=True,
                                     offset_ms=batch['startMs'],downgrade_incomplete=False)
            batch.update(status='succeeded' if not bad else 'partial',requestId=reply.get('id'),findings={k:v.model_dump() for k,v in rows.items()},sourceFindings={},errors=bad)
            pending.update(bad)
            for rid in batch['ruleIds']:
                row=rows.get(rid)
                if row is None or rid in bad:
                    refined[rid].append({'batchId':batch['id'],'verdict':'needs_confirmation','description':bad.get(rid,'精判缺项'),'windows':[]});continue
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
            batch.update(status='failed',errorCode=getattr(exc,'code','REFINEMENT_FAILED'),error='该批次裁片、请求或结果校验失败，未自动重试')
            pending.update(batch['ruleIds'])
            for rid in batch['ruleIds']:refined[rid].append({'batchId':batch['id'],'verdict':'needs_confirmation','description':batch['error'],'windows':[]})
        state['completedBatches']+=1;save()
    state['stage']='finalizing';save()
    combinations=_components(session,observations,pending)
    findings=[]
    for rid,row in first.items():
        rule=session.rules[rid];reviews=refined[rid];evidence=[]
        if reviews:
            confirmed=[r for r in reviews if r['verdict']=='non_compliant' and r.get('coverage_complete') and r.get('windows')]
            if confirmed:verdict='non_compliant'
            elif rid in pending or any(r['verdict']!='compliant' for r in reviews):verdict='needs_confirmation'
            else:verdict='compliant'
            def review_text(review):
                times='、'.join(f"{w['start']:g}–{w['end']:g}秒" for w in review.get('windows',[]))
                return (f'原片{times}：' if times else '')+review['description']
            # 已确认违规时只保留确认违规的那些片段说明；其他片段"这里没看到"的意见会自相矛盾，不再拼进来。
            shown=confirmed if confirmed else reviews
            description='；'.join(dict.fromkeys(review_text(r) for r in shown))
            observation_type='observed' if confirmed else 'uncertain' if verdict=='needs_confirmation' else 'observed'
            if rid in combinations:
                verdict=combinations[rid]['verdict']
            # Promote only confirmed problem clips; internal clips remain diagnostic files.
            for review in confirmed if verdict=='non_compliant' else []:
                batch=next(b for b in batches if b['id']==review['batchId'])
                evidence.append(promote_clip(session,batch,internal[batch['id']],rid))
        else:
            verdict=row.verdict;description=row.description+'（未经过片段精判）';observation_type=row.observation_type
            if rid in pending:verdict='needs_confirmation'
        if rid in combinations:
            verdict=combinations[rid]['verdict']
            description += '；'+combinations[rid]['reason']
        if verdict=='non_compliant' and reviews and not evidence and rid not in combinations:
            verdict='needs_confirmation'
        findings.append(Finding(id='issue-'+rid,rule_id=rid,rule_number=rule.get('ruleNumber'),verdict=verdict,description=description,observation_type=observation_type,evidence_ids=list(dict.fromkeys(evidence))))
        state['decisions'].append({'ruleId':rid,'initialVerdict':state['localization']['findings'][rid]['verdict'],
                                  'finalVerdict':verdict,'refined':bool(reviews),'batchIds':[r['batchId'] for r in reviews]})
    coverage_notes={}
    for finding in findings:
        row=first.get(finding.rule_id)
        if not row or finding.verdict!='compliant' or row.coverage_complete:continue
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
    report=AgentReport(findings=findings,summary=f"逐条审查完成：必须项不符合{failures}项、待确认{uncertain}项，建议{suggestions}项，通过{passed}项；完成{len(batches)}批片段精判，最终由品牌核查。")
    session.evidence_references=validate_evidence_references(report,session.artifacts,session.sha256);session.save_manifest()
    rendered=report.model_dump()
    from app.modules.workflow.video_layers import layer
    for finding in rendered['findings']:
        finding['layer']=layer(session.rules[finding['rule_id']])
        if finding['rule_id'] in scope_records:finding['evidence_basis']=scope_records[finding['rule_id']]
        if finding['rule_id'] in coverage_notes:finding['coverage_note']=coverage_notes[finding['rule_id']]
        if finding['rule_id'] in evidence_errors:finding['evidence_note']=evidence_errors[finding['rule_id']]
    state['stage']='completed';save()
    return {'pipeline':state,'report':rendered,'componentResults':list(combinations.values()),'artifacts':session.artifacts,
            'evidenceReferences':session.evidence_references,'toolCalls':session.calls,'providerRequestIds':request_ids,'usage':usages,
            'coverageVerified':False,'conclusion':'incomplete','note':'时间与组件计算经代码校验；观察覆盖和判断仍需品牌核查'}
