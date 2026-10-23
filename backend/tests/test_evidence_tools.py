import json
import subprocess

import pytest

from app.modules.video_model.evidence.tools import EvidenceSession


@pytest.fixture
def session(tmp_path, monkeypatch):
    video = tmp_path / "original.mp4"
    video.write_bytes(b"test-original")
    def run(args, **kwargs):
        if "-frames:v" in args or "-c:v" in args:
            from pathlib import Path
            Path(args[-1]).write_bytes(b"evidence")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(args, 1, "", "Duration: 00:00:10.00\n Stream #0:1: Audio: aac")
    monkeypatch.setattr(subprocess, "run", run)
    return EvidenceSession(video=video, output_dir=tmp_path / "output", ffmpeg="ffmpeg",
                           rules=[{"id": "r1", "text": "产品露出"}])


def test_evidence_identity_cache_budget_and_manifest(session):
    first = session.screenshot(1000, "issue-1")
    assert session.screenshot(1000, "issue-1") == first
    for timestamp in (2000, 3000, 4000):
        session.screenshot(timestamp, "issue-1")
    with pytest.raises(ValueError):
        session.screenshot(5000, "issue-1")
    # 同一条规则的多处独立违规：同 issue 允许最多 3 段片段，各自独立成证据。
    clips = [session.clip(start, start + 3000, "issue-1") for start in (0, 3000, 6000)]
    with pytest.raises(ValueError):
        session.clip(9000, 10000, "issue-1")
    clip = clips[0]
    assert clip["hasAudio"] and len({c['id'] for c in clips}) == 3
    assert clip["sourceSha256"] == first["sourceSha256"]
    manifest = json.loads((session.output_dir / "manifest.json").read_text())
    assert len(manifest["artifacts"]) == 7
    assert "original.mp4" not in json.dumps(manifest)


def test_invalid_times_and_cross_rule_access_rejected(session):
    with pytest.raises(ValueError):
        session.screenshot(10000, "bad")
    with pytest.raises(ValueError):
        session.clip(4000, 3000, "bad")
    with pytest.raises(ValueError):
        session.rule_source("other-task-rule")
    assert session.artifacts == []


def test_langchain_loop_uses_full_video_and_validates_evidence(session):
    pytest.importorskip("langchain_core")
    from app.modules.video_model.evidence.agent import run_agent
    rounds = []
    def complete(messages, tools):
        rounds.append(len(messages))
        assert messages[1]["content"][0]["video_url"]["url"].startswith("data:video/mp4;base64,")
        if len(rounds) == 1:
            assert len(tools) == 5
            return {"choices": [{"message": {"tool_calls": [{"id": "call1", "type": "function", "function": {
                "name": "capture_evidence_image", "arguments": '{"timestamp_ms":1000,"issue_id":"i1"}'}}]}}]}
        assert json.loads(messages[-1]["content"])["ok"]
        return {"choices": [{"message": {"content": json.dumps({"summary": "mock", "findings": [{"id": "i1", "rule_id": "r1",
                 "verdict": "needs_confirmation", "description": "人工核查", "evidence_ids": [session.artifacts[0]["id"]]}]})}}]}
    result = run_agent(session, complete)
    assert len(rounds) == 2
    assert result["conclusion"] == "incomplete"
    assert not result["coverageVerified"]


def test_agent_rejects_fabricated_evidence_and_budget(session):
    pytest.importorskip("langchain_core")
    from app.modules.video_model.evidence.agent import run_agent
    def forged(messages, tools):
        return {"choices": [{"message": {"content": json.dumps({"summary": "mock", "findings": [{
            "id": "i1", "rule_id": "r1", "verdict": "non_compliant", "description": "mock",
            "evidence_ids": ["fabricated"]}]})}}]}
    with pytest.raises(ValueError, match="证据 ID"):
        run_agent(session, forged)
    def excessive(messages, tools):
        return {"choices": [{"message": {"tool_calls": [{"id": str(i), "function": {
            "name": "get_video_metadata", "arguments": "{}"}} for i in range(13)]}}]}
    with pytest.raises(ValueError, match="预算"):
        run_agent(session, excessive)


def test_final_round_demands_report_and_accepts_fenced_json(session):
    from app.modules.video_model.evidence.agent import run_agent
    count = 0
    def complete(messages, tools):
        nonlocal count
        count += 1
        if count <= 3:
            return {'choices':[{'message':{'tool_calls':[{'id':str(count),'function':{'name':'get_video_metadata','arguments':'{}'}}]}}]}
        assert not tools
        assert messages[-1]['role'] == 'user'
        assert '工具轮次已结束' in messages[-1]['content']
        return {'choices':[{'message':{'content':'```json\n'+json.dumps({'summary':'ok','findings':[{'id':'i1','rule_id':'r1','verdict':'needs_confirmation','description':'核查','evidence_ids':[]}]})+'\n```'}}]}
    assert run_agent(session, complete)['report']['summary'] == 'ok'
    assert count == 4


def test_invalid_report_has_specific_safe_error(session):
    from app.modules.video_model.evidence.agent import run_agent, AnalysisFailure
    with pytest.raises(AnalysisFailure) as error:
        run_agent(session, lambda *_: {'choices':[{'message':{'content':'not JSON'}}]})
    assert error.value.code == 'FINAL_REPORT_INVALID_JSON'


def test_absence_report_needs_no_media_tools_but_keeps_coverage_unverified(session):
    from app.modules.video_model.evidence.agent import run_agent

    def complete(messages, tools):
        return {'choices': [{'message': {'content': json.dumps({
            'summary': '检查范围内未发现指定产品，交品牌核对。',
            'findings': [{'id': 'missing-product', 'rule_id': 'r1',
                          'verdict': 'non_compliant', 'observation_type': 'absence',
                          'description': '在0–10秒检查范围内未发现指定产品；模型观察仍需人工核对。',
                          'evidence_ids': []}],
        })}}]}

    result = run_agent(session, complete)
    assert result['report']['findings'][0]['observation_type'] == 'absence'
    assert result['report']['findings'][0]['verdict'] == 'non_compliant'
    assert result['artifacts'] == []
    assert result['toolCalls'] == []
    assert result['coverageVerified'] is False
    assert result['conclusion'] == 'incomplete'


def test_same_run_clip_can_support_multiple_rules_without_duplicate_rendering(session):
    from app.modules.video_model.evidence.agent import run_agent

    session.rules = {f'r{i}': {'id': f'r{i}'} for i in range(1, 5)}
    images = [session.screenshot(ms, 'issue-2')['id'] for ms in (1000, 2000)]
    clip = session.clip(0, 9000, 'issue-3')
    report = {'summary': '同一对话支持品牌、口播及脚本对照，仍须人工核对', 'findings': [
        {'id': f'issue-{i}', 'rule_id': f'r{i}', 'verdict': 'needs_confirmation',
         'description': '核对相关对话或产品画面', 'observation_type': 'observed',
         'evidence_ids': images if i == 2 else [clip['id']]} for i in range(1, 5)
    ]}
    result = run_agent(session, lambda *_: {'choices': [{'message': {'content': json.dumps(report)}}]})
    assert len(result['artifacts']) == 3
    assert clip['issueId'] == 'issue-3'
    references = result['evidenceReferences']
    assert len(references) == 5
    assert {r['ruleId'] for r in references if r['evidenceId'] == clip['id']} == {'r1', 'r3', 'r4'}
    assert all(r['originIssueId'] == 'issue-3' for r in references if r['evidenceId'] == clip['id'])
    manifest = json.loads((session.output_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['evidenceReferences'] == references
    assert not result['coverageVerified']
    assert result['conclusion'] == 'incomplete'


def test_shared_reference_rejects_other_video_source(session):
    from app.modules.video_model.evidence.agent import run_agent, AnalysisFailure

    artifact = session.screenshot(1000, 'original-issue')
    artifact['sourceSha256'] = 'another-video'
    report = {'summary': 'test', 'findings': [{'id': 'i1', 'rule_id': 'r1',
              'verdict': 'needs_confirmation', 'description': 'test', 'evidence_ids': [artifact['id']]}]}
    with pytest.raises(AnalysisFailure, match='哈希不一致'):
        run_agent(session, lambda *_: {'choices': [{'message': {'content': json.dumps(report)}}]})
    assert session.evidence_references == []


def test_reference_from_another_run_is_rejected_even_for_same_video(session):
    from app.modules.video_model.evidence.agent import AgentReport, validate_evidence_references, AnalysisFailure

    artifact = session.screenshot(1000, 'original-issue')
    report = AgentReport.model_validate({'summary': 'test', 'findings': [{'id': 'i1', 'rule_id': 'r1',
        'verdict': 'needs_confirmation', 'description': 'test', 'evidence_ids': [artifact['id']]}]})
    with pytest.raises(AnalysisFailure, match='不属于本次调用'):
        validate_evidence_references(report, [], session.sha256)
