"""组件独立精判：只把量化检查交给模型，结论由代码按冻结参数计算。

设计要点：
- 一条组件规则一次独立请求，和"逐条规则审核"分开，避免互相干扰。
- 组件参数带明确时间范围（startSeconds/endSeconds 且不超过 60 秒）时，先裁片再发送；
  需要全片判断（没给范围 / 范围覆盖整片 / 范围过长）时发送完整视频。
- 模型只提交观察（时间区间、是否可辨认、是否遮挡等），不提交结论；
  `component_runtime.evaluate()` 负责算时长、次数和判定。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from pydantic import BaseModel, Field

from .component_runtime import ComponentObservationInput

MAX_CLIP_MS = 60_000


class ComponentPassReport(BaseModel):
    observations: list[ComponentObservationInput] = Field(default_factory=list)


def plan_inputs(session, clip_ms=MAX_CLIP_MS):
    """按"需要的视频范围"给组件分组：同一段的组件共用一次请求。"""
    duration = session.duration_ms
    groups = {}
    for rule in session.rules.values():
        plan = rule.get('componentPlan')
        if not plan:
            continue
        for node in plan['nodes']:
            params = node.get('parameters') or {}
            start = max(0, round((params.get('startSeconds') or 0) * 1000))
            end = round(params['endSeconds'] * 1000) if params.get('endSeconds') is not None else duration
            end = min(end, duration)
            bounded = 0 <= start < end <= duration and (end - start) <= clip_ms and (start > 0 or end < duration - 1)
            key = (start, end) if bounded else 'full'
            groups.setdefault(key, []).append((rule, node))
    planned = []
    for key, items in groups.items():
        if key == 'full':
            planned.append({'key': 'full', 'startMs': 0, 'endMs': duration, 'clip': False, 'nodes': items})
        else:
            start, end = key
            planned.append({'key': f'clip-{start}-{end}', 'startMs': start, 'endMs': end, 'clip': True, 'nodes': items})
    return planned


def _media(path: Path):
    return {'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,' + base64.b64encode(Path(path).read_bytes()).decode('ascii')}}


def _schema():
    return ComponentPassReport.model_json_schema()


def _shift(values, offset):
    shifted = []
    for item in values:
        value = item.model_dump() if hasattr(item, 'model_dump') else dict(item)
        value['start_ms'] = int(value['start_ms']) + offset
        value['end_ms'] = int(value['end_ms']) + offset
        shifted.append(value)
    return shifted


def run_component_pass(session, complete, *, prompt, video_input, render_clip, plan=None):
    """逐组发请求，返回 {(ruleId, componentId): {coverage_complete, observations, notes}}。"""
    results = {}
    meta = {'groups': [], 'usage': [], 'requestIds': []}
    tool = {'type': 'function', 'function': {'name': 'submit_component_pass', 'description': '提交本组量化组件的观察', 'parameters': _schema()}}
    for group in plan if plan is not None else plan_inputs(session):
        offset = 0 if not group['clip'] else group['startMs']
        if group['clip']:
            path = render_clip(session, {'id': group['key'], 'startMs': group['startMs'], 'endMs': group['endMs']})
            video_part = _media(path)
        else:
            video_part = _media(video_input)
        payload = {'sourceOffsetSeconds': offset / 1000,
                   'durationSeconds': (group['endMs'] - group['startMs']) / 1000,
                   'hasAudio': session.has_audio,
                   'videoScope': 'clip' if group['clip'] else 'full',
                   'components': [{'rule_id': rule['id'], 'rule_text': rule.get('text', ''),
                                   'component_id': node['id'], 'kind': node['kind'],
                                   'parameters': node.get('parameters') or {},
                                   'instruction': (node.get('prompt') or {}).get('content', '')}
                                  for rule, node in group['nodes']]}
        content = [video_part, {'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
        # 只附带这些组件真正引用的参考图，不把无关图片塞进请求。
        wanted = {identity for _, node in group['nodes']
                  for identity in ((node.get('parameters') or {}).get('referenceImageIds') or [])}
        for ref in session.references:
            if ref.get('id') not in wanted:
                continue
            content.append({'type': 'text', 'text': json.dumps({k: v for k, v in ref.items() if k != 'data'}, ensure_ascii=False)})
            content.append({'type': 'image_url',
                            'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(ref['data']).decode('ascii')}})
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': content}]
        reply = complete(messages, [tool])
        meta['usage'].append(reply.get('usage'))
        if reply.get('id'):
            meta['requestIds'].append(reply.get('id'))
        message = reply['choices'][0]['message']
        calls = message.get('tool_calls') or []
        if len(calls) != 1 or calls[0].get('function', {}).get('name') != 'submit_component_pass':
            raise ValueError('组件精判未返回结构化观察')
        raw = calls[0]['function']['arguments']
        value = json.loads(raw) if isinstance(raw, str) else raw
        report = ComponentPassReport.model_validate(value)
        expected = {(rule['id'], node['id']) for rule, node in group['nodes']}
        seen = set()
        for item in report.observations:
            key = (item.rule_id, item.component_id)
            if key not in expected or key in seen:
                continue
            seen.add(key)
            results[key] = {'coverage_complete': item.coverage_complete,
                            'observations': _shift(item.observations, offset),
                            'notes': item.notes}
        meta['groups'].append({'key': group['key'], 'clip': group['clip'], 'startMs': group['startMs'],
                               'endMs': group['endMs'], 'submitted': len(seen), 'expected': len(expected)})
    return results, meta