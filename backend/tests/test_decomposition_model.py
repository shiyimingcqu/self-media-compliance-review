import json

import httpx

from app.core.database import connection, migrate
from app.modules.workflow import standards
from test_script_versions import task_with_rule, K

PATH = '/api/v1/settings/models/decomposition'


def audit_config(client, **kw):
    body = {'endpoint': 'https://audit.example.com/v1', 'model': 'omni-audit', 'revision': 0, 'apiKey': 'sk-audit-secret', **kw}
    response = client.put('/api/v1/settings/models', json=body)
    assert response.status_code == 200, response.text
    return response.json()['data']


def decomposition_config(client, **kw):
    response = client.put(PATH, json={'endpoint': 'https://decomposition.example.com/v1', 'revision': 0, 'apiKey': 'sk-decomposition-secret', **kw})
    assert response.status_code == 200, response.text
    return response.json()['data']


def test_defaults_and_credentials_are_independent(client):
    initial = client.get(PATH).json()['data']
    assert initial['model'] == 'qwen3.8-flash' and initial['revision'] == 0 and not initial['hasKey']
    assert initial['reasoningEffort'] == 'xhigh' and initial['temperature'] == 0.1
    audit = audit_config(client)
    # Configuring audit on a fresh install does not create an implicit fallback.
    assert not client.get(PATH).json()['data']['hasKey']
    separate = decomposition_config(client)
    assert separate['revision'] == 1 and separate['model'] == 'qwen3.8-flash'
    assert client.get('/api/v1/settings/models').json()['data'] == audit
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT encrypted_key FROM decomposition_model_settings').fetchone()
        assert row[0] != 'sk-decomposition-secret'
    assert 'sk-decomposition-secret' not in client.get(PATH).text
    cleared = client.put(PATH, json={'endpoint': separate['endpoint'], 'revision': 1, 'clearKey': True})
    assert cleared.status_code == 200 and not cleared.json()['data']['hasKey']
    assert client.get('/api/v1/settings/models').json()['data'] == audit
    assert client.put(PATH, json={'endpoint': separate['endpoint'], 'revision': 1}).status_code == 409


def test_explicit_credential_copy_preserves_language_model_parameters(client):
    audit_config(client)
    first = client.post(PATH + '/copy-review-credentials', json={'revision': 0})
    assert first.status_code == 200
    view = first.json()['data']
    assert view['hasKey'] and view['model'] == 'qwen3.8-flash'
    assert view['endpoint'] == 'https://audit.example.com/v1'
    decomposition_config(client, revision=1, model='another-text-model', reasoningEffort='low', temperature=0.2)
    again = client.post(PATH + '/copy-review-credentials', json={'revision': 2}).json()['data']
    assert again['model'] == 'another-text-model' and again['reasoningEffort'] == 'low' and again['temperature'] == 0.2
    audit_config(client, revision=1, clearKey=True, apiKey=None)
    assert client.get(PATH).json()['data']['hasKey']
    assert client.post(PATH + '/copy-review-credentials', json={'revision': 3}).status_code == 409


def test_upgrade_copies_existing_credentials_once_without_changing_audit(client):
    before = audit_config(client)
    settings = client.app.state.settings
    with connection(settings.database_path) as db:
        db.execute('DROP TABLE decomposition_model_settings')
        db.execute("DELETE FROM schema_migrations WHERE name='W_0010_decomposition_model.sql'")
    migrate(settings.database_path)
    view = client.get(PATH).json()['data']
    assert view['model'] == 'qwen3.8-flash' and view['hasKey'] and view['revision'] == 1
    assert client.get('/api/v1/settings/models').json()['data'] == before
    audit_config(client, endpoint='https://new-audit.example.com/v1', revision=1)
    migrate(settings.database_path)
    assert client.get(PATH).json()['data'] == view


def test_recommendation_uses_only_decomposition_model_and_freezes_parameters(client, monkeypatch):
    project, task = task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source = db.execute('SELECT source_version_id FROM rule_versions WHERE id=?', (task['ruleVersionId'],)).fetchone()[0]
    audit = audit_config(client); separate = decomposition_config(client)
    actual = httpx.Client; calls = []
    def provider(request):
        calls.append(1)
        assert str(request.url) == 'https://decomposition.example.com/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer sk-decomposition-secret'
        body = json.loads(request.content)
        assert body['model'] == 'qwen3.8-flash'
        assert body['reasoning_effort'] == 'xhigh' and body['temperature'] == 0.1
        assert body['response_format']['json_schema']['strict'] is True
        assert body['response_format']['json_schema']['name'] == 'brief_rule_candidates'
        block = json.loads(body['messages'][1]['content'])['blocks'][0]
        report = {'rules': [{'text': '品牌名称准确', 'criterion': '对照Brief核对品牌名称', 'sourceBlockId': block['id'], 'sourceQuote': block['text'], 'importance': 'must'}], 'warnings': []}
        return httpx.Response(200, json={'id': 'decomposition-request', 'choices': [{'message': {'content': json.dumps(report)}}]})
    monkeypatch.setattr(standards.httpx, 'Client', lambda **kw: actual(transport=httpx.MockTransport(provider), **kw))
    url = f"/api/v1/projects/{project['id']}/standard-recommendations"; key = K()
    body = {'sourceVersionId': source, 'scope': 'script', 'configRevision': separate['revision'], 'allowRealCall': True}
    first = client.post(url, json=body, headers={'Idempotency-Key': key})
    assert first.status_code == 200, first.text
    value = first.json()['data']; assert value['status'] == 'succeeded', value
    assert value['snapshot']['modelRole'] == 'decomposition'
    assert value['snapshot']['model'] == 'qwen3.8-flash'
    assert value['snapshot']['requestOptions']['reasoningEffort'] == 'xhigh'
    assert 'sk-decomposition-secret' not in json.dumps(value)
    assert client.post(url, json=body, headers={'Idempotency-Key': key}).json()['data']['id'] == value['id']
    assert calls == [1]
    assert client.get('/api/v1/settings/models').json()['data'] == audit
    # An unrelated audit revision must not invalidate the decomposition revision.
    audit_config(client, revision=1, model='new-audit', clearKey=True, apiKey=None)
    assert client.post(url, json=body, headers={'Idempotency-Key': K()}).json()['data']['status'] == 'succeeded'
    assert calls == [1, 1]


def test_missing_decomposition_key_never_falls_back_to_audit(client, monkeypatch):
    project, task = task_with_rule(client); audit_config(client)
    with connection(client.app.state.settings.database_path) as db:
        source = db.execute('SELECT source_version_id FROM rule_versions WHERE id=?', (task['ruleVersionId'],)).fetchone()[0]
    monkeypatch.setattr(standards, 'call_recommendation', lambda *a: (_ for _ in ()).throw(AssertionError('No fallback call')))
    response = client.post(f"/api/v1/projects/{project['id']}/standard-recommendations", json={'sourceVersionId': source, 'scope': 'script', 'configRevision': 1, 'allowRealCall': True}, headers={'Idempotency-Key': K()})
    assert response.status_code == 409 and response.json()['error']['code'] == 'DECOMPOSITION_MODEL_KEY_MISSING'
