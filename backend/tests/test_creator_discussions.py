import json
import pytest
from app.core.database import connection
from app.modules.workflow import router as workflow
from test_script_versions import task_with_rule, submit, K
from test_workflow import configure, analyze, state, decide


def seed(client, monkeypatch, scope='script'):
    project, task = task_with_rule(client)
    configure(client, task)
    assert submit(client, task['id'], {'inputType': 'text', 'text': '麦咖啡产品展示，核心卖点已经安排。'}).status_code == 201
    if scope == 'script':
        def model(config, key, snapshot):
            bad = next(r['id'] for r in snapshot['rules'] if r['importance'] == 'must')
            return {'report': {'summary': '固定响应：一条存在争议', 'findings': [
                {'rule_id': r['id'], 'verdict': 'non_compliant' if r['id'] == bad else 'compliant',
                 'quote': '', 'description': 'AI认为未展示产品' if r['id'] == bad else '符合', 'suggestion': ''}
                for r in snapshot['rules']]}}
        monkeypatch.setattr(workflow, 'call_script_model', model)
        response = analyze(client, task, state(client, task))
        assert response.json()['data']['status'] == 'succeeded', response.text
        review = state(client, task)['scriptReview']
        bad = next(f['rule_id'] for f in review['result']['report']['findings'] if f['verdict'] == 'non_compliant')
        rid = review['id']
    else:
        view = state(client, task)
        rid, bad = K(), 'video-rule-1'
        snapshot = {'workflowContextKey': view['contextKey'], 'scriptApprovalId': None,
                    'rules': [{'id': bad, 'ruleNumber': 1, 'text': '产品可辨认出现', 'criterion': '展示产品即可', 'importance': 'must', 'layer': 'baseline'}]}
        result = {'report': {'findings': [{'id': 'issue1', 'rule_id': bad, 'verdict': 'non_compliant', 'description': 'AI认为没有产品', 'evidence_ids': []}]}}
        with connection(client.app.state.settings.database_path) as db:
            db.execute('INSERT INTO evidence_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (rid, 'video', task['id'], None, 'succeeded', 1, json.dumps(snapshot), json.dumps(result), None, None, '2026-10-23T10:00:00Z', '2026-10-23T10:00:00Z'))
    return project, task, rid, bad


def objection(client, task, scope, rid, rule, **extra):
    return client.post(f"/api/v1/tasks/{task['id']}/review-disputes", json={'scope': scope, 'reviewId': rid, 'ruleId': rule, 'message': '原稿已经展示产品，请核对', 'location': '脚本第2行 / 视频第3秒', **extra}, headers={'Idempotency-Key': K()})


@pytest.mark.parametrize('verdict', ['compliant', 'needs_confirmation', 'not_applicable'])
def test_formal_script_submission_is_persisted_without_reanalysis(client, monkeypatch, verdict):
    _, task, rid, rule = seed(client, monkeypatch)
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT result_json FROM workflow_script_reviews WHERE id=?', (rid,)).fetchone()
        result = json.loads(row['result_json'])
        for finding in result['report']['findings']:
            finding['verdict'] = verdict
        db.execute('UPDATE workflow_script_reviews SET result_json=? WHERE id=?', (json.dumps(result), rid))
    def no_model(*args, **kwargs):
        pytest.fail('正式提交不得调用模型')
    monkeypatch.setattr(workflow, 'call_script_model', no_model)
    path = f"/api/v1/tasks/{task['id']}/review-responses"
    body = {'scope': 'script', 'reviewId': rid, 'action': 'submitted'}
    key = K()
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).status_code == 200
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).status_code == 200
    discussion = client.get(f"/api/v1/tasks/{task['id']}/review-discussion?scope=script&reviewId={rid}").json()['data']
    assert discussion['response']['action'] == 'submitted'
    assert discussion['response']['revision'] == 1
    queue = client.get('/api/v1/workflow/review-queue').json()['data']
    assert len(queue) == 1 and queue[0]['scope'] == 'script'
    assert state(client, task)['scriptSubmitted']
    assert client.get('/api/v1/workflow/creator-progress?scope=script').json()['data']['items'][0]['status'] == 'pending_brand'
    assert not state(client, task)['scriptApproval']
    if verdict == 'needs_confirmation':
        assert decide(client, task, state(client, task)).status_code == 409
        assert decide(client, task, state(client, task), decision='returned', note='请补充可核对的产品名称').status_code == 200
    if verdict == 'compliant':
        assert objection(client, task, 'script', rid, rule).status_code == 200
    assert submit(client, task['id'], {'inputType': 'text', 'text': '新的脚本版本'}).status_code == 201
    assert client.post(path, json={**body, 'revision': 1}, headers={'Idempotency-Key': K()}).status_code == 409


@pytest.mark.parametrize('scope', ['script', 'video'])
def test_formal_submission_rejects_failed_rules(client, monkeypatch, scope):
    _, task, rid, _ = seed(client, monkeypatch, scope)
    response = client.post(f"/api/v1/tasks/{task['id']}/review-responses", json={
        'scope': scope, 'reviewId': rid, 'action': 'submitted'}, headers={'Idempotency-Key': K()})
    assert response.status_code == 409
    assert response.json()['error']['code'] == ('SCRIPT_SUBMISSION_UNAVAILABLE' if scope == 'script' else 'VIDEO_SUBMISSION_UNAVAILABLE')


@pytest.mark.parametrize('defect', ['incomplete_media', 'partial_report', 'processing', 'mandatory_failure'])
def test_brand_submission_cannot_bypass_incomplete_or_mandatory_failure(client, monkeypatch, defect):
    _, task, rid, rule = seed(client, monkeypatch)
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT snapshot_json,result_json FROM workflow_script_reviews WHERE id=?', (rid,)).fetchone()
        snapshot, result = json.loads(row[0]), json.loads(row[1])
        for finding in result['report']['findings']: finding['verdict'] = 'compliant'
        if defect == 'incomplete_media': snapshot['multimodal']['incomplete'] = True
        if defect == 'partial_report': result['partialReport'] = result['report']
        if defect == 'mandatory_failure':
            snapshot['paragraphMode'] = True
            result['report']['mandatory_checks'] = [
                {'rule_id': r['id'], 'verdict': 'non_compliant' if r['id'] == rule else 'compliant', 'reason': '固定测试'}
                for r in snapshot['rules'] if r['importance'] == 'must']
        db.execute('UPDATE workflow_script_reviews SET snapshot_json=?,result_json=?,status=? WHERE id=?',
                   (json.dumps(snapshot), json.dumps(result), 'processing' if defect == 'processing' else 'succeeded', rid))
    assert not state(client, task)['scriptSubmissionReady']
    response = client.post(f"/api/v1/tasks/{task['id']}/review-responses", json={
        'scope': 'script', 'reviewId': rid, 'action': 'submitted'}, headers={'Idempotency-Key': K()})
    assert response.status_code == 409
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []


def ruling(client, project, dispute, outcome='accepted', **extra):
    return client.post(f"/api/v1/projects/{project['id']}/review-disputes/{dispute}/decisions", json={'outcome': outcome, 'note': '已核对原始材料，此处AI误判', 'reviewedMaterial': True, 'revision': 1, **extra}, headers={'Idempotency-Key': K()})


@pytest.mark.parametrize('scope', ['script', 'video'])
def test_response_dispute_ruling_and_effective_gate_without_rewriting_ai(client, monkeypatch, scope):
    project, task, rid, rule = seed(client, monkeypatch, scope)
    base = f"/api/v1/tasks/{task['id']}"
    table = 'workflow_script_reviews' if scope == 'script' else 'evidence_runs'
    with connection(client.app.state.settings.database_path) as db:
        original = db.execute(f'SELECT snapshot_json,result_json FROM {table} WHERE id=?', (rid,)).fetchone()
        frozen = tuple(original)
    key = K(); body = {'scope': scope, 'reviewId': rid, 'action': 'modifying'}
    assert client.post(base + '/review-responses', json=body, headers={'Idempotency-Key': key}).status_code == 200
    assert client.post(base + '/review-responses', json=body, headers={'Idempotency-Key': key}).status_code == 200
    progress = client.get('/api/v1/workflow/creator-progress?scope=' + scope).json()['data']['items'][0]
    assert progress[scope]['status'] == 'modifying'
    first = objection(client, task, scope, rid, rule)
    assert first.status_code == 200, first.text
    dispute = first.json()['data']['id']
    assert client.get('/api/v1/workflow/creator-progress?scope=' + scope).json()['data']['items'][0]['status'] == 'disputed'
    assert client.get('/api/v1/workflow/review-disputes').json()['data']['items'][0]['rule']['id'] == rule
    assert ruling(client, project, dispute, reviewedMaterial=False).status_code == 400
    assert ruling(client, project, dispute).status_code == 200
    view = state(client, task)
    assert (view['scriptAcceptance'] if scope == 'script' else view['videoComponentAcceptance'])['passed'] is True
    assert not (view['scriptApproval'] if scope == 'script' else view['finalDecision'])
    assert client.get('/api/v1/workflow/creator-progress?scope=' + scope).json()['data']['items'][0]['status'] == 'ready_to_submit'
    discussion = client.get(base + f'/review-discussion?scope={scope}&reviewId={rid}').json()['data']
    assert client.post(base + '/review-responses', json={'scope': scope, 'reviewId': rid, 'action': 'submitted', 'revision': discussion['response']['revision']}, headers={'Idempotency-Key': K()}).status_code == 200
    assert decide(client, task, view, scope=scope).status_code == 200
    assert client.get('/api/v1/workflow/creator-progress?scope=' + scope).json()['data']['items'][0][scope]['status'] == 'approved'
    with connection(client.app.state.settings.database_path) as db:
        assert tuple(db.execute(f'SELECT snapshot_json,result_json FROM {table} WHERE id=?', (rid,)).fetchone()) == frozen
    assert objection(client, task, scope, rid, rule, revision=2).status_code == 409


def test_needs_info_followup_revision_and_rejection(client, monkeypatch):
    project, task, rid, rule = seed(client, monkeypatch)
    dispute = objection(client, task, 'script', rid, rule).json()['data']['id']
    assert ruling(client, project, dispute, 'needs_info').status_code == 200
    assert objection(client, task, 'script', rid, rule, revision=1).status_code == 409
    assert objection(client, task, 'script', rid, rule, revision=2, message='补充：第2行有清晰产品名').status_code == 200
    assert ruling(client, project, dispute, 'rejected', revision=3).status_code == 200
    assert state(client, task)['scriptAcceptance']['passed'] is False
    detail = client.get(f"/api/v1/projects/{project['id']}/review-disputes/{dispute}").json()['data']
    assert [m['actor'] for m in detail['messages']] == ['creator', 'brand', 'creator', 'brand']
    assert detail['script'].startswith('麦咖啡')
    assert detail['scriptMaterial']['scriptVersionId'] == state(client, task)['scriptReview']['scriptVersionId']
    assert detail['scriptReview']['id'] == rid
    assert detail['rules'] and detail['findings']
    assert any(r['id'] == rule for r in detail['rules'])
    assert client.get('/api/v1/workflow/review-disputes').json()['data']['total'] == 0


def test_cross_task_rule_and_stale_run_are_rejected_and_history_is_kept(client, monkeypatch):
    project, task, rid, rule = seed(client, monkeypatch)
    other_project, other_task = task_with_rule(client)
    assert objection(client, other_task, 'script', rid, rule).status_code == 404
    assert objection(client, task, 'script', rid, 'invented-rule').status_code == 400
    dispute = objection(client, task, 'script', rid, rule).json()['data']['id']
    assert client.get(f"/api/v1/projects/{other_project['id']}/review-disputes/{dispute}").status_code == 404
    assert ruling(client, other_project, dispute).status_code == 404
    assert submit(client, task['id'], {'inputType': 'text', 'text': '新版本脚本，不沿用旧异议资格。'}).status_code == 201
    assert objection(client, task, 'script', rid, rule, revision=1).status_code == 409
    assert ruling(client, project, dispute).status_code == 200  # Historical adjudication is archival only.
    assert not state(client, task)['scriptAcceptance']['passed']
    detail = client.get(f"/api/v1/projects/{project['id']}/review-disputes/{dispute}").json()['data']
    assert not detail['current'] and detail['status'] == 'accepted'
    assert detail['scriptMaterial']['text'].startswith('麦咖啡')
    assert detail['scriptMaterial']['scriptVersionId'] != state(client, task)['currentScript']['scriptVersionId']
    assert detail['scriptReview']['id'] == rid
    assert client.get('/api/v1/workflow/creator-progress').json()['data']['items'][0]['script']['status'] == 'pending_review'


def test_reminders_idempotency_ownership_and_read_state(client, monkeypatch):
    _, task, _, _ = seed(client, monkeypatch)
    _, other = task_with_rule(client)
    path = f"/api/v1/tasks/{task['id']}/creator-reminders"
    body = {'scope': 'script', 'message': '请完成脚本修改或提交异议'}
    key = K()
    first = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert first.status_code == 200
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).json()['data'] == first.json()['data']
    assert len(client.get(path).json()['data']) == 1

    identity = first.json()['data']['id']
    assert client.post(f"/api/v1/tasks/{other['id']}/creator-reminders/{identity}/read").status_code == 404
    assert client.post(path + f'/{identity}/read').status_code == 200
    assert client.get(path).json()['data'][0]['read_at']
    with connection(client.app.state.settings.database_path) as db:
        db.execute('UPDATE creator_tasks SET archived_at=? WHERE id=?', ('2026-10-23T12:00:00Z', task['id']))
    assert client.post(path, json=body, headers={'Idempotency-Key': K()}).status_code == 409
    assert len(client.get(path).json()['data']) == 1


def test_dispute_idempotency_and_invalid_report_cannot_grant_acceptance(client, monkeypatch):
    project, task, rid, rule = seed(client, monkeypatch)
    path = f"/api/v1/tasks/{task['id']}/review-disputes"
    body = {'scope': 'script', 'reviewId': rid, 'ruleId': rule, 'message': '请核对原稿'}
    key = K()
    first = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert first.status_code == 200
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).json()['data'] == first.json()['data']
    assert client.post(path, json={**body, 'message': '不同请求'}, headers={'Idempotency-Key': key}).status_code == 409
    dispute = first.json()['data']['id']
    detail_path = f"/api/v1/projects/{project['id']}/review-disputes/{dispute}"
    assert len(client.get(detail_path).json()['data']['messages']) == 1
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE workflow_script_reviews SET status='failed' WHERE id=?", (rid,))
    assert objection(client, task, 'script', rid, rule, revision=1).status_code == 400
    # Even a recorded ruling cannot turn a failed or incomplete report into a valid run.
    assert ruling(client, project, dispute).status_code == 200
    assert not state(client, task)['scriptAcceptance']['passed']
