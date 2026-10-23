import io
import json
import time
from uuid import uuid4

import pytest
from PIL import Image

from app.core.database import connection
from app.modules.workflow import brief_import, brief_processing
from app.modules.foundation.service import now


def key(): return uuid4().hex


def upload(client, name='抖音_麦咖啡_芝士浓奶铁_Brief.txt', pdf=False):
    if pdf:
        output = io.BytesIO(); Image.new('RGB', (60, 40), 'white').save(output, format='PDF')
        data = output.getvalue(); name = name.replace('.txt', '.pdf'); mime = 'application/pdf'
    else: data = '麦咖啡 芝士浓奶铁 抖音\n麦当劳 McDonald’s McCafé'.encode(); mime = 'text/plain'
    r = client.post('/api/v1/files', files={'file': (name, data, mime)}, data={'purpose': 'brief'}, headers={'Idempotency-Key': key()})
    assert r.status_code == 201, r.text
    return r.json()['data']


def metadata():
    return {'projectName': '芝士浓奶铁投放', 'brandName': '麦咖啡', 'productNames': ['芝士浓奶铁'], 'platforms': ['douyin'],
            'evidence': [{'field': field, 'value': value, 'source': 'filename', 'quote': quote} for field, value, quote in
                [('brandName', '麦咖啡', '麦咖啡'), ('productNames', '芝士浓奶铁', '芝士浓奶铁'), ('platforms', 'douyin', '抖音')]], 'warnings': []}


def setup(client, monkeypatch, response=None):
    r = client.put('/api/v1/settings/models', json={'endpoint': 'https://models.example.com/v1', 'model': 'omni-test', 'revision': 0, 'apiKey': 'sk-test-only'})
    assert r.status_code == 200, r.text
    calls = []
    def call(config, secret, filename, preview, images):
        calls.append({'filename': filename, 'preview': preview, 'images': images})
        return {'modelResponse': json.dumps(response if response is not None else metadata(), ensure_ascii=False), 'providerRequestId': 'mock-import'}
    monkeypatch.setattr(brief_import, 'call_metadata', call)
    # Verify queue handoff without ever contacting real providers.
    monkeypatch.setattr(brief_processing, 'submit', lambda request, identity: None)
    return calls


def wait(client, identity):
    for _ in range(160):
        r = client.get('/api/v1/brief-project-imports/' + identity)
        assert r.status_code == 200, r.text
        data = r.json()['data']
        if data['status'] not in ('queued', 'processing'): return data
        time.sleep(.025)
    raise AssertionError('import never completed')


@pytest.mark.parametrize('pdf', [False, True])
def test_filename_context_auto_creates_project_and_attaches_brief_atomically(client, monkeypatch, pdf):
    calls = setup(client, monkeypatch)
    file = upload(client, pdf=pdf); k = key(); body = {'fileId': file['id'], 'allowRealCall': True}
    r = client.post('/api/v1/brief-project-imports', json=body, headers={'Idempotency-Key': k})
    assert r.status_code == 202, r.text
    data = wait(client, r.json()['data']['id'])
    assert data['status'] == 'succeeded', data
    p = client.get('/api/v1/projects/' + data['projectId']).json()['data']
    assert (p['brand']['name'], p['productNames'], p['platforms']) == ('麦咖啡', ['芝士浓奶铁'], ['douyin'])
    brief = client.get(f"/api/v1/projects/{p['id']}/brief").json()['data']['brief']
    assert brief['fileId'] == file['id'] and brief['processing']['status'] == 'queued'
    assert calls[0]['filename'] == file['originalName']
    assert bool(calls[0]['images']) is pdf
    assert data['snapshot']['fileSha256'] == file['sha256']
    assert data['result']['modelResponse']
    assert len(calls) == 1
    replay = client.post('/api/v1/brief-project-imports', json=body, headers={'Idempotency-Key': k})
    assert replay.json()['data']['projectId'] == p['id'] and len(calls) == 1
    again = client.post(f"/api/v1/brief-project-imports/{data['id']}/complete", json={'project': data['result']['project']})
    assert again.json()['data']['projectId'] == p['id']
    assert client.get('/api/v1/projects').json()['total'] == 1
    with connection(client.app.state.settings.database_path) as db:
        context = brief_import.document_context(db, p['id'], brief['sourceVersionId'])
        assert context['filename'] == file['originalName'] and context['productNames'] == ['芝士浓奶铁']
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_unknown_platform_is_not_defaulted_and_can_be_completed_without_model(client, monkeypatch):
    response = metadata(); response['platforms'] = []
    calls = setup(client, monkeypatch, response)
    file = upload(client)
    r = client.post('/api/v1/brief-project-imports', json={'fileId': file['id'], 'allowRealCall': True}, headers={'Idempotency-Key': key()})
    data = wait(client, r.json()['data']['id'])
    assert data['status'] == 'needs_input' and not data['projectId']
    assert client.get('/api/v1/projects').json()['total'] == 0
    body = {'project': {'name': '人工确认项目', 'brandName': '麦咖啡', 'productNames': ['芝士浓奶铁'], 'platforms': ['douyin']}}
    saved = client.post(f"/api/v1/brief-project-imports/{data['id']}/complete", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()['data']['status'] == 'succeeded' and len(calls) == 1


def test_bad_citations_do_not_create_project_and_conflicts_can_be_resolved(client, monkeypatch):
    response = metadata(); response['evidence'][0]['quote'] = '原件没有的引文'
    calls = setup(client, monkeypatch, response)
    file = upload(client)
    r = client.post('/api/v1/brief-project-imports', json={'fileId': file['id'], 'allowRealCall': True}, headers={'Idempotency-Key': key()})
    data = wait(client, r.json()['data']['id'])
    assert data['status'] == 'needs_input' and data['result']['metadata']['brandName'] == ''
    assert client.get('/api/v1/projects').json()['total'] == 0
    project = {'name': '重复项目', 'brandName': '麦咖啡', 'platforms': ['douyin'], 'productNames': ['芝士浓奶铁']}
    assert client.post('/api/v1/projects', json=project, headers={'Idempotency-Key': key()}).status_code == 201
    url = f"/api/v1/brief-project-imports/{data['id']}/complete"
    assert client.post(url, json={'project': project}).status_code == 409
    assert client.get('/api/v1/projects').json()['total'] == 1
    project['name'] = '另一个项目'
    assert client.post(url, json={'project': project}).status_code == 200
    assert len(calls) == 1


def test_invalid_json_is_preserved_no_retry_and_no_empty_project(client, monkeypatch):
    calls = setup(client, monkeypatch)
    def bad(*args): calls.append('bad'); return {'modelResponse': '{unescaped"', 'providerRequestId': 'raw-kept'}
    monkeypatch.setattr(brief_import, 'call_metadata', bad)
    file = upload(client)
    r = client.post('/api/v1/brief-project-imports', json={'fileId': file['id'], 'allowRealCall': True}, headers={'Idempotency-Key': key()})
    data = wait(client, r.json()['data']['id'])
    assert data['status'] == 'failed' and data['result']['modelResponse'] == '{unescaped"'
    assert data['result']['providerRequestId'] == 'raw-kept' and len(calls) == 1
    assert client.get('/api/v1/projects').json()['total'] == 0
    recent = client.get('/api/v1/brief-project-imports').json()['data'][0]
    assert recent['id'] == data['id'] and recent['result'] is None


def test_import_requires_model_and_explicit_billing_consent(client):
    file = upload(client)
    r = client.post('/api/v1/brief-project-imports', json={'fileId': file['id']}, headers={'Idempotency-Key': key()})
    assert r.status_code == 400
    r = client.post('/api/v1/brief-project-imports', json={'fileId': file['id'], 'allowRealCall': True}, headers={'Idempotency-Key': key()})
    assert r.status_code == 409 and r.json()['error']['code'] == 'MODEL_KEY_MISSING'


def test_project_and_brief_are_rolled_back_when_registration_fails(client, monkeypatch):
    from app.modules.brief_script import service_registration
    from app.core.errors import ApiError
    setup(client, monkeypatch)
    def fail(*args, **kwargs): raise ApiError(400, 'TEST_REGISTRATION_FAIL', '登记失败')
    monkeypatch.setattr(service_registration, 'create_brief', fail)
    file = upload(client)
    r = client.post('/api/v1/brief-project-imports', json={'fileId': file['id'], 'allowRealCall': True}, headers={'Idempotency-Key': key()})
    data = wait(client, r.json()['data']['id'])
    assert data['status'] == 'needs_input'
    assert client.get('/api/v1/projects').json()['total'] == 0
    assert client.get('/api/v1/brands').json()['data'] == []


def test_verified_metadata_does_not_accept_product_not_in_quoted_source():
    value = metadata(); value['evidence'][1]['value'] = '捏造的产品'; value['productNames'] = ['捏造的产品']
    assert brief_import.verified_metadata(value, '抖音_麦咖啡_芝士浓奶铁_Brief.txt', '').product_names == []
