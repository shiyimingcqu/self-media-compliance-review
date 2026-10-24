import json
import pytest
from app.core.database import connection
from test_creator_discussions import seed
from test_script_versions import K
from test_workflow import state


@pytest.mark.parametrize('verdict', ['compliant', 'needs_confirmation', 'non_compliant'])
def test_video_submission_is_explicit_and_keeps_manual_uncertainty(client, monkeypatch, verdict):
    _, task, rid, _ = seed(client, monkeypatch, 'video')
    with connection(client.app.state.settings.database_path) as db:
        result = json.loads(db.execute('SELECT result_json FROM evidence_runs WHERE id=?', (rid,)).fetchone()[0])
        result['report']['findings'][0]['verdict'] = verdict
        db.execute('UPDATE evidence_runs SET result_json=? WHERE id=?', (json.dumps(result), rid))
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []
    key = K(); body = {'scope': 'video', 'reviewId': rid, 'action': 'submitted'}
    path = f"/api/v1/tasks/{task['id']}/review-responses"
    response = client.post(path, json=body, headers={'Idempotency-Key': key})
    if verdict == 'non_compliant':
        assert response.status_code == 409
        return
    assert response.status_code == 200, response.text
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).status_code == 200
    assert state(client, task)['videoSubmitted']
    queue = client.get('/api/v1/workflow/review-queue').json()['data']
    assert len(queue) == 1 and queue[0]['scope'] == 'video'
    with connection(client.app.state.settings.database_path) as db:
        assert json.loads(db.execute('SELECT result_json FROM evidence_runs WHERE id=?', (rid,)).fetchone()[0]) == result
