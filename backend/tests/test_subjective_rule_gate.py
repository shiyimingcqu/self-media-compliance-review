"""主观判断类规则的举证门槛。

背景（2026-10-24 真实误判复盘）：模型看滤镜、造型、肤色得出的"歧视/容貌焦虑"观感判断并不可靠
——它把抖音常见的变脸娱乐特效判成了"肤色歧视"，一条 must 规则因此一票否决整条视频。
但模型读字幕、认画面文字、听口播是可靠的。所以这类规则要有逐字证据才能定不合规，
否则降级为待确认交品牌，既不放过真问题，也不让观感当判决。
"""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.video_model.evidence import localized as pipeline
from app.modules.workflow.prompts import prompt

SUBJECTIVE_RULE = {'id': 'r22', 'text': '避免出现容貌焦虑、歧视歧义等内容',
                   'criterion': '审查视频文案、画面及博主表现，确认不包含引发外貌焦虑、性别对立、物化女性或种族/肤色歧视等争议性内容。',
                   'importance': 'must', 'ruleNumber': 22, 'category': 'social_compliance'}
OBJECTIVE_RULE = {'id': 'r2', 'text': '出现指定产品', 'criterion': '产品在视频中有实际出现',
                  'importance': 'must', 'ruleNumber': 2, 'category': '内容'}


def row(rid, verdict='compliant', windows=None, observation_type='observed', **extra):
    return dict(rule_id=rid, verdict=verdict, observation_type=observation_type, description='固定观察',
                coverage_complete=True, windows=windows or [], components=[], **extra)


def window(start=3, end=4):
    return {'start': start, 'end': end, 'reason': '核对画面内容'}


def quote(start, end, channel, text):
    return {'start': start, 'end': end, 'channel': channel, 'text': text, 'description': '逐字证据'}


def reply(rows, stage='localization'):
    return {'id': 'fixed-' + stage, 'usage': {'total_tokens': 10}, 'choices': [{'message': {'tool_calls': [
        {'id': 'submit-fixed', 'type': 'function',
         'function': {'name': 'submit_' + stage, 'arguments': json.dumps({'findings': rows})}}]}}]}


@pytest.fixture
def session(tmp_path, monkeypatch):
    source = tmp_path / 'original.mp4'; source.write_bytes(b'original-full-video')
    target = SimpleNamespace(video=source, output_dir=tmp_path, ffmpeg='ffmpeg', duration_ms=100_000,
                             has_audio=True, sha256='original-hash', rules={}, script='批准脚本',
                             references=[], artifacts=[], calls=[], component_results={}, evidence_references=[])
    target.metadata = lambda: {'durationMs': target.duration_ms, 'sourceSha256': target.sha256, 'hasAudio': True}
    target.rule_source = lambda rid: target.rules[rid]
    target.save_manifest = lambda: None
    def render(session, batch):
        path = tmp_path / (batch['id'] + '.mp4'); path.write_bytes(batch['id'].encode()); return path
    monkeypatch.setattr(pipeline, 'render_internal_clip', render)
    return target


def run(session, *responses):
    """跑完整两阶段。non_compliant 一定会触发精判批次，所以响应要给够。"""
    pending = list(responses)
    result = pipeline.run_localized(session, lambda messages, tools: pending.pop(0), video_input=session.video,
                                    prompts={'localize': prompt('video-localize'), 'refine': prompt('video-refine')})
    assert not pending, '模型响应数与实际请求数不一致'
    return result


def verdict_of(result, rid='r22'):
    return next(f for f in result['report']['findings'] if f['rule_id'] == rid)


# ---- 精判路径（真实任务走的就是这条）----

def test_refined_visual_impression_alone_cannot_convict(session):
    """两轮都只有观感依据：降级为待确认，理由写进描述与 judgment_note。"""
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)])], 'refinement'))
    finding = verdict_of(result)
    assert finding['verdict'] == 'needs_confirmation'
    assert '逐字证据' in finding['judgment_note']
    assert '逐字证据' in finding['description']          # 品牌在卡片上能直接看懂为什么没判违规
    assert result['pipeline']['decisions'][0]['finalVerdict'] == 'needs_confirmation'


def test_refined_verbatim_quote_still_convicts(session):
    """精判片段给出画面文字/字幕/口播逐字证据：门槛放行，必须项照样判不合规。"""
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)],
                            evidence_quotes=[quote(2, 5, 'visible_text', '画面里写着贬损某族群的口号')])], 'refinement'))
    finding = verdict_of(result)
    assert finding['verdict'] == 'non_compliant'
    assert 'judgment_note' not in finding


@pytest.mark.parametrize('channel', ['audio', 'subtitle', 'visible_text'])
def test_every_verbatim_channel_satisfies_the_gate(session, channel):
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)],
                            evidence_quotes=[quote(2, 5, channel, '可核对的原话')])], 'refinement'))
    assert verdict_of(result)['verdict'] == 'non_compliant'


def test_visual_channel_quote_is_still_only_an_impression(session):
    """channel=visual 的"证据"仍是观感，不算数。"""
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)],
                            evidence_quotes=[quote(2, 5, 'visual', '博主肤色被加深并加了胡子')])], 'refinement'))
    assert verdict_of(result)['verdict'] == 'needs_confirmation'


def test_blank_quote_text_does_not_satisfy_the_gate(session):
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)],
                            evidence_quotes=[quote(2, 5, 'subtitle', '   ')])], 'refinement'))
    assert verdict_of(result)['verdict'] == 'needs_confirmation'


def test_first_round_quote_cannot_bail_out_a_refined_violation(session):
    """只认确认违规那段自己给的逐字证据；首轮的观感描述不能补位。"""
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)],
                            evidence_quotes=[quote(15, 18, 'subtitle', '首轮引用的一句正常台词')])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)])], 'refinement'))
    finding = verdict_of(result)
    assert finding['verdict'] == 'needs_confirmation'
    assert 'judgment_note' in finding


# ---- 首轮直接定论路径（absence 类不进精判）----

def test_whole_video_absence_verdict_is_also_gated(session):
    """首轮判不合规且不需要精判（无窗口）时，同样要过门槛。"""
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session, reply([row('r22', 'non_compliant', [], observation_type='absence')]))
    finding = verdict_of(result)
    assert result['pipeline']['totalBatches'] == 0
    assert finding['verdict'] == 'needs_confirmation'
    assert 'judgment_note' in finding


def test_whole_video_absence_with_quote_convicts(session):
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session, reply([row('r22', 'non_compliant', [], observation_type='absence',
                                     evidence_quotes=[quote(30, 33, 'audio', '口播原话：针对某群体的贬损说法')])]))
    assert verdict_of(result)['verdict'] == 'non_compliant'


# ---- 影响面控制 ----

def test_objective_rules_are_not_gated(session):
    """非主观规则（产品露出、名称核对）不受门槛影响，看见即判。"""
    session.rules['r2'] = OBJECTIVE_RULE
    result = run(session,
                 reply([row('r2', 'non_compliant', [window()])]),
                 reply([row('r2', 'non_compliant', [window(1, 2), window(3, 4)])], 'refinement'))
    finding = verdict_of(result, 'r2')
    assert finding['verdict'] == 'non_compliant'
    assert 'judgment_note' not in finding


def test_gate_keeps_the_models_time_location(session):
    """降级不改变模型给的时间定位，品牌仍能看到它说的那一段。"""
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)])], 'refinement'))
    located = result['pipeline']['localization']['findings']['r22']['windows']
    assert [round(w['start']) for w in located] == [15] and [round(w['end']) for w in located] == [21]


def test_downgraded_rule_lands_in_review_not_blocking(session):
    """降级后的规则进"待品牌确认"，不再算明确不合规（与既有验收口径一致）。"""
    from app.modules.workflow.router import video_component_acceptance
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)])], 'refinement'))
    stored = {'status': 'succeeded',
              'snapshot_json': json.dumps({'rules': [SUBJECTIVE_RULE]}),
              'result_json': json.dumps({'report': result['report'], 'componentResults': []})}
    acceptance = video_component_acceptance(stored)
    assert acceptance['blockingRules'] == []
    assert acceptance['reviewCount'] == 1


def test_subjective_rule_is_detected_by_category_or_wording():
    from app.modules.workflow.video_layers import subjective_rule
    assert subjective_rule(SUBJECTIVE_RULE)
    assert subjective_rule({'text': '内容不得低俗', 'criterion': 'x'})
    assert subjective_rule({'text': 'x', 'criterion': '避免引发性别对立争议'})
    assert not subjective_rule(OBJECTIVE_RULE)
    assert not subjective_rule({'text': '品牌名称正确', 'criterion': '检查有没有叫错品牌'})
    # 带量化组件的规则由程序算结论，不走这条门槛
    assert not subjective_rule({'text': '避免出现歧视', 'criterion': 'x', 'componentPlan': {'nodes': [{}]}})


def test_prompts_tell_the_model_about_the_bar():
    """提示词必须同步：否则模型不知道要提交 evidence_quotes，门槛会把所有违规都降级。"""
    for key in ('video-localize', 'video-refine'):
        content = prompt(key)['content']
        assert 'evidence_quotes' in content, key
        assert 'needs_confirmation' in content, key


def test_gated_finding_still_gets_a_frame_to_look_at(session):
    """降级不该让品牌失去可看的东西：按模型定位的区间补一帧。"""
    shots = []
    def screenshot(timestamp_ms, issue_id):   # 与 EvidenceSession.screenshot 同字段，引用校验才会认
        shots.append(timestamp_ms)
        artifact = {'id': 'frame-1', 'kind': 'image', 'issueId': issue_id, 'startMs': timestamp_ms,
                    'endMs': timestamp_ms, 'filename': 'frame-1.jpg', 'sourceSha256': session.sha256, 'hasAudio': False}
        session.artifacts.append(artifact)
        return artifact
    session.screenshot = screenshot
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)])], 'refinement'))
    finding = verdict_of(result)
    assert finding['verdict'] == 'needs_confirmation'
    assert shots == [int((15_000 + 21_000) / 2)]        # 用精判片段对应的原片区间中点
    assert finding['evidence_ids'] == ['frame-1']
    assert [r['evidenceId'] for r in result['evidenceReferences']] == ['frame-1']   # 新补的证据通过引用校验


def test_gated_finding_without_location_gets_no_frame(session):
    """模型没给任何可定位区间时不硬造证据。"""
    called = []
    session.screenshot = lambda ms, issue: called.append(ms) or {'id': 'x', 'kind': 'image', 'issueId': issue}
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session, reply([row('r22', 'non_compliant', [], observation_type='absence')]))
    assert verdict_of(result)['verdict'] == 'needs_confirmation'
    assert called == []


def test_screenshot_failure_does_not_break_the_verdict(session):
    def boom(timestamp_ms, issue_id):
        raise RuntimeError('ffmpeg 不可用')
    session.screenshot = boom
    session.rules['r22'] = SUBJECTIVE_RULE
    result = run(session,
                 reply([row('r22', 'non_compliant', [window(15, 21)])]),
                 reply([row('r22', 'non_compliant', [window(2, 8)])], 'refinement'))
    finding = verdict_of(result)
    assert finding['verdict'] == 'needs_confirmation'
    assert finding['evidence_ids'] == []
