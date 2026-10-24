import json
from app.core.database import connection
from test_creator_discussions import seed
from test_script_versions import K


def test_creator_summary_preserves_video_submission_without_requiring_images(client, monkeypatch):
    _, task, rid, _ = seed(client, monkeypatch, 'video')
    with connection(client.app.state.settings.database_path) as db:
        result = json.loads(db.execute('SELECT result_json FROM evidence_runs WHERE id=?', (rid,)).fetchone()[0])
        result['report']['findings'][0]['verdict'] = 'needs_confirmation'
        db.execute('UPDATE evidence_runs SET result_json=? WHERE id=?', (json.dumps(result), rid))
    response = client.post(f"/api/v1/tasks/{task['id']}/review-responses", json={'scope': 'video', 'reviewId': rid, 'action': 'submitted'}, headers={'Idempotency-Key': K()})
    assert response.status_code == 200
    task_view = next(t for t in client.get('/api/v1/creator/tasks').json()['data'] if t['id'] == task['id'])
    assert task_view['deliveries']['video']['submitted']
    assert task_view['deliveries']['video']['submittedAt']
    assert task_view['deliveries']['video']['status'] == 'pending_brand'
    assert not task_view['deliveries']['image']['submitted']
    assert task_view['deliveries']['image']['status'] == 'not_submitted'
