import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app

TOKEN = 'test-gateway-secret-not-a-provider-key'
HOST = 'review.example.test'
ORIGIN = 'http://' + HOST


@pytest.fixture
def remote(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, gateway_token=TOKEN, allowed_origins=(ORIGIN,)))
    @app.middleware('http')
    async def remote_client(request, call_next):
        request.scope['client'] = ('203.0.113.10', 1234)
        return await call_next(request)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


@pytest.mark.parametrize('headers', [{}, {'X-Review-Gateway-Token':'wrong'}, {'X-Forwarded-For':'127.0.0.1'}, {'Authorization':'Basic anything'}])
def test_remote_cannot_bypass_gateway(remote, headers):
    assert remote.get('/api/v1/settings/models', headers=headers).status_code == 403


def test_gateway_allows_configuration_and_shared_brief_guard_without_calls(remote):
    headers = {'X-Review-Gateway-Token':TOKEN,'Origin':ORIGIN}
    for path in ['/api/v1/settings/models','/api/v1/settings/models/decomposition','/api/v1/evidence-runs']:
        assert remote.get(path, headers=headers).status_code == 200
    value = remote.put('/api/v1/settings/models',headers=headers,json={
        'endpoint':'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model':'qwen3.8-omni-flash','apiKey':'sk-test-fixture-only','revision':0})
    assert value.status_code == 200
    assert value.json()['data']['hasKey']
    assert TOKEN not in value.text and 'sk-test-fixture-only' not in value.text


@pytest.mark.parametrize('extra', [{'Origin':'http://evil.example'}, {'Origin':'null'}, {'Host':'evil.example'}])
def test_gateway_rejects_wrong_origin_or_host(remote, extra):
    headers = {'X-Review-Gateway-Token':TOKEN, **extra}
    assert remote.get('/api/v1/settings/models', headers=headers).status_code == 403


def test_token_disabled_by_default(client):
    assert not client.app.state.settings.gateway_token
    assert client.get('/api/v1/settings/models',headers={'X-Review-Gateway-Token':TOKEN,'Origin':ORIGIN}).status_code == 403
