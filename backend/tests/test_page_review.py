from app.core.database import connection
from app.modules.workflow import brief_vision
from test_brief_vision import prepare, wait, report
from test_script_versions import K


def setup_notes(client, monkeypatch):
    p, brief, body = prepare(client)
    calls = []
    def model(*args):
        calls.append(1)
        value = report(partial=True)
        value['pages'][0]['uncertainties'] = ['一串内部资源ID的用途不确定']
        return {'report': value, 'providerRequestIds': ['mock-pages']}
    monkeypatch.setattr(brief_vision, 'call_vision', model)
    client.post(f'/api/v1/projects/{p}/brief/vision', json=body, headers={'Idempotency-Key': K()})
    return p, wait(client, p), calls


def test_confirmed_page_disappears_persistently_without_changing_original_coverage(client, monkeypatch):
    p, brief, calls = setup_notes(client, monkeypatch)
    assert [c['pageNo'] for c in brief['pageChecks']] == [1, 2]
    raw = brief['visualRun']['result']
    url = f'/api/v1/projects/{p}/brief/pages/review'
    for index, action in enumerate(['confirmed', 'irrelevant']):
        body = {'sourceVersionId': brief['sourceVersionId'], 'checkId': brief['pageChecks'][index]['id'], 'action': action}
        key = K()
        response = client.post(url, json=body, headers={'Idempotency-Key': key})
        assert response.status_code == 200, response.text
        assert len(response.json()['data']['pageChecks']) == 1 - index
        assert client.post(url, json=body, headers={'Idempotency-Key': key}).json()['data'] == response.json()['data']
        assert client.post(url, json=body, headers={'Idempotency-Key': K()}).status_code == 200
    fresh = client.get(f'/api/v1/projects/{p}/brief').json()['data']['brief']
    assert fresh['pageChecks'] == []
    assert fresh['uncoveredSources'] == brief['uncoveredSources'] == ['page:1', 'page:2']
    assert fresh['visualRun']['result'] == raw
    assert calls == [1]
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM brief_page_reviews').fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='brief_page_reviewed'").fetchone()[0] == 2


def test_wrong_project_source_stale_or_unknown_check_cannot_be_resolved(client, monkeypatch):
    p, brief, calls = setup_notes(client, monkeypatch)
    body = {'sourceVersionId': brief['sourceVersionId'], 'checkId': brief['pageChecks'][0]['id'], 'action': 'confirmed'}
    path = f'/api/v1/projects/{p}/brief/pages/review'
    assert client.post(path.replace(p, 'other'), json=body, headers={'Idempotency-Key': K()}).status_code == 404
    assert client.post(path, json={**body, 'sourceVersionId': 'old-source'}, headers={'Idempotency-Key': K()}).status_code == 409
    assert client.post(path, json={**body, 'checkId': 'fake'}, headers={'Idempotency-Key': K()}).status_code == 409
    # A fresh successful visual run has a different scope, even when the same page has a similar note.
    with connection(client.app.state.settings.database_path) as db:
        row = dict(db.execute('SELECT * FROM brief_visual_runs WHERE id=?', (brief['visualRun']['id'],)).fetchone())
        row['id'] = 'new-visual-run'
        columns = list(row)
        db.execute(f"INSERT INTO brief_visual_runs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
    assert client.post(path, json=body, headers={'Idempotency-Key': K()}).status_code == 409
    fresh = client.get(f'/api/v1/projects/{p}/brief').json()['data']['brief']
    assert len(fresh['pageChecks']) == 2 and fresh['pageChecks'][0]['id'] != body['checkId']
    assert calls == [1]
