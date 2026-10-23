from app.core.database import connection
from app.modules.workflow import brief_vision
from test_brief_vision import prepare, wait, report
from test_script_versions import K


def test_visual_run_records_duration_and_replay_preserves_it(client, monkeypatch):
    p, _, body = prepare(client)
    monkeypatch.setattr(brief_vision, 'call_vision', lambda *_: {'report': report()})
    path = f'/api/v1/projects/{p}/brief/vision'
    key = K()
    client.post(path, json=body, headers={'Idempotency-Key': key})
    run = wait(client, p)['visualRun']
    assert run['status'] == 'succeeded'
    assert run['createdAt'] and run['updatedAt']
    assert isinstance(run['result']['durationMs'], int) and run['result']['durationMs'] >= 0
    saved = run['result']['durationMs']
    repeated = client.post(path, json=body, headers={'Idempotency-Key': key}).json()['data']
    assert repeated['result']['durationMs'] == saved
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT created_at,updated_at FROM brief_visual_runs WHERE id=?', (run['id'],)).fetchone()
