import json
import pytest
from app.core.database import connection
from test_creator_discussions import seed, objection, ruling
from test_script_versions import task_with_rule, submit, K


@pytest.mark.parametrize('scope', ['script', 'video'])
def test_failed_rules_stay_in_records_not_queue_and_ruling_is_respected(client, monkeypatch, scope):
    project, task, rid, rule = seed(client, monkeypatch, scope)
    queue = client.get('/api/v1/workflow/review-queue').json()['data']
    assert not queue
    listing = '/api/v1/workflow/submission-records?projectId=' + project['id']
    records = client.get(listing).json()['data']
    assert records['total'] == 1 and records['items'][0]['outcome'] == 'needs_changes'
    assert records['items'][0]['id'] == rid
    path = f"/api/v1/projects/{project['id']}/submission-records/{scope}/{rid}"
    detail = client.get(path)
    assert detail.status_code == 200, detail.text
    assert any(f['verdict'] == 'non_compliant' for f in detail.json()['data']['findings'])
    assert 'snapshot_json' not in detail.text and 'modelResponse' not in detail.text and 'encrypted_key' not in detail.text
    dispute = objection(client, task, scope, rid, rule).json()['data']['id']
    assert ruling(client, project, dispute).status_code == 200
    queue = client.get('/api/v1/workflow/review-queue').json()['data']
    assert [item['scope'] for item in queue] == [scope]
    # A manual ruling updates the effective result, never the saved AI finding.
    assert client.get(listing).json()['data']['total'] == 1
    assert client.get(listing).json()['data']['items'][0]['manualAcceptedCount'] == 1
    assert any(f['verdict'] == 'non_compliant' for f in client.get(path).json()['data']['findings'])


@pytest.mark.parametrize('status,outcome', [('processing', 'processing'), ('failed', 'call_failed'), ('interrupted', 'call_failed')])
def test_incomplete_calls_are_logged_without_entering_queue(client, monkeypatch, status, outcome):
    project, task, rid, _ = seed(client, monkeypatch, 'video')
    with connection(client.app.state.settings.database_path) as db:
        db.execute('UPDATE evidence_runs SET status=? WHERE id=?', (status, rid))
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []
    records = client.get(f"/api/v1/workflow/submission-records?projectId={project['id']}").json()['data']
    assert records['total'] == 1 and records['items'][0]['outcome'] == outcome


def test_video_pending_and_suggested_findings_do_not_count_as_failed_must_rules(client, monkeypatch):
    project, task, rid, rule = seed(client, monkeypatch, 'video')
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT * FROM evidence_runs WHERE id=?', (rid,)).fetchone()
        snapshot, result = json.loads(row['snapshot_json']), json.loads(row['result_json'])
        result['report']['findings'][0]['verdict'] = 'needs_confirmation'
        snapshot['rules'].append({'id': 'advice', 'text': '构图建议', 'importance': 'suggested', 'ruleNumber': 2})
        result['report']['findings'].append({'rule_id': 'advice', 'verdict': 'non_compliant', 'description': '优化建议'})
        db.execute('UPDATE evidence_runs SET snapshot_json=?,result_json=? WHERE id=?', (json.dumps(snapshot), json.dumps(result), rid))
    assert len(client.get('/api/v1/workflow/review-queue').json()['data']) == 1
    # Missing configuration is not a creator violation, and does not enter queue.
    snapshot['unconfiguredRules'] = [{'ruleId': rule, 'ruleNumber': 1, 'importance': 'must', 'reason': '待品牌配置'}]
    with connection(client.app.state.settings.database_path) as db:
        db.execute('UPDATE evidence_runs SET snapshot_json=? WHERE id=?', (json.dumps(snapshot), rid))
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []

    assert client.get(f"/api/v1/workflow/submission-records?projectId={project['id']}").json()['data']['items'][0]['outcome'] == 'needs_confirmation'


def test_submission_history_is_project_scoped_and_survives_new_content(client, monkeypatch):
    project, task, rid, _ = seed(client, monkeypatch)
    other, _ = task_with_rule(client)
    path = f"/api/v1/projects/{project['id']}/submission-records/script/{rid}"
    assert client.get(f"/api/v1/projects/{other['id']}/submission-records/script/{rid}").status_code == 404
    assert client.get(f"/api/v1/workflow/submission-records?projectId={other['id']}").json()['data']['total'] == 0
    assert submit(client, task['id'], {'inputType': 'text', 'text': '这是新脚本，不覆盖过去的记录'}).status_code == 201
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []
    assert '麦咖啡' in client.get(path).json()['data']['material']['script']
    assert client.get(f"/api/v1/workflow/submission-records?projectId={project['id']}&outcome=needs_changes").json()['data']['total'] == 1


def test_image_calls_also_have_submission_records(client, monkeypatch):
    project, task, _, _ = seed(client, monkeypatch)
    with connection(client.app.state.settings.database_path) as db:
        stamp, vid, rid = '2026-10-24T01:00:00Z', K(), K()
        db.execute('INSERT INTO image_post_versions VALUES (?,?,?,?,?,?,?)', (vid, task['id'], 1, '封面', '正文', '[]', stamp))
        snapshot = {'rules': [{'id': 'i1', 'text': '封面展示产品', 'importance': 'must'}], 'version': {'versionLabel': '图文v1', 'title': '封面', 'body': '正文', 'images': []}}
        result = {'report': {'summary': '封面未展示产品', 'findings': [{'rule_id': 'i1', 'verdict': 'non_compliant', 'description': '封面没有产品'}]}}
        db.execute('INSERT INTO image_post_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)', (rid, task['id'], vid, 'old-context', json.dumps(snapshot), 'succeeded', json.dumps(result), None, None, stamp, stamp))
    records = client.get(f"/api/v1/workflow/submission-records?scope=image&projectId={project['id']}").json()['data']
    assert records['total'] == 1 and records['items'][0]['outcome'] == 'needs_changes'
    detail = client.get(f"/api/v1/projects/{project['id']}/submission-records/image/{rid}").json()['data']
    assert detail['material']['post']['title'] == '封面'
