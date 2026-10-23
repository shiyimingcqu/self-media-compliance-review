import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.core.database import connection
from app.modules.workflow import router as workflow
from test_separate_standards import project, payload
from test_script_versions import K
from test_numbered_multimodal_script import report


def setup(client):
    p = project(client); body = payload(client)
    body['imageRules'] = [
        {'text': '封面品牌正确', 'criterion': '核对封面品牌与产品', 'importance': 'must', 'category': '封面'},
        {'text': '正文文案合规', 'criterion': '核对标题正文和配图中的宣传用语', 'importance': 'must', 'category': '文案'},
    ]
    r = client.post(f"/api/v1/projects/{p['id']}/publish-task", json=body, headers={'Idempotency-Key': K()})
    assert r.status_code == 201, r.text
    task = r.json()['data']['id']
    r = client.put('/api/v1/settings/models', json={'endpoint': 'https://models.example.com/v1', 'model': 'omni-test', 'revision': 0, 'apiKey': 'sk-test-only'})
    assert r.status_code == 200
    return p, task, body


def png(color='red'):
    buffer = io.BytesIO(); Image.new('RGB', (160, 120), color).save(buffer, 'PNG'); return buffer.getvalue()


def upload(client, task, color='red', key=None):
    r = client.post(f'/api/v1/tasks/{task}/image-assets', files={'file': ('封面.png', png(color), 'image/png')}, headers={'Idempotency-Key': key or K()})
    assert r.status_code == 201, r.text
    return r.json()['data']


def state(client, task):
    r = client.get(f'/api/v1/tasks/{task}/image-posts'); assert r.status_code == 200, r.text
    return r.json()['data']


def save(client, task, assets, **kw):
    body = {'title': '品牌A图文标题', 'body': '品牌A产品展示，分享真实体验。#品牌A', 'images': [{'assetId': a['id'], 'caption': f'第{i}张配文'} for i, a in enumerate(assets, 1)], **kw}
    r = client.post(f'/api/v1/tasks/{task}/image-posts', json=body, headers={'Idempotency-Key': K()})
    assert r.status_code == 201, r.text
    return r.json()['data']


def review(client, task, key=None):
    view = state(client, task)
    r = client.post(f'/api/v1/tasks/{task}/image-posts/reviews', json={'versionId': view['currentVersion']['id'], 'contextKey': view['contextKey'], 'configRevision': 1, 'allowRealCall': True}, headers={'Idempotency-Key': key or K()})
    assert r.status_code == 201, r.text
    return r.json()['data']


def decision(client, task, view, verdict='approved', **kw):
    return client.post(f'/api/v1/tasks/{task}/image-posts/decisions', json={'reviewId': view['review']['id'], 'contextKey': view['contextKey'], 'decision': verdict, 'note': '已核对标题正文及全部图片', 'reviewedMaterial': True, **kw}, headers={'Idempotency-Key': K()})


def test_complete_image_post_workflow_and_independent_script_gate(client, monkeypatch):
    p, task, rules = setup(client); assets = [upload(client, task, color) for color in ('red', 'green')]
    saved = save(client, task, assets)
    calls = []; actual = httpx.Client
    def provider(request):
        calls.append(1); body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        assert '图文成品审核助手' in body['messages'][0]['content']
        parts = body['messages'][1]['content']; material = json.loads(parts[0]['text'])
        assert len(material['multimodal']['images']) == 2
        assert material['multimodal']['images'][0]['locations'] == ['第1张 · 封面']
        urls = [x['image_url']['url'] for x in parts if x['type'] == 'image_url']
        with Image.open(io.BytesIO(base64.b64decode(urls[0].split(',')[1]))) as im:assert im.getpixel((0, 0)) == (255, 0, 0)
        with Image.open(io.BytesIO(base64.b64decode(urls[1].split(',')[1]))) as im:assert im.getpixel((0, 0)) == (0, 128, 0)
        with connection(client.app.state.settings.database_path) as db:
            snapshot = db.execute('SELECT snapshot_json FROM image_post_reviews WHERE task_id=?', (task,)).fetchone()[0]
        assert 'base64' not in snapshot
        value = report(material); value['findings'][0]['quote_refs'] = ['q1']
        return httpx.Response(200, json={'id': 'image-provider-id', 'model': 'test-version', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]})
    monkeypatch.setattr(workflow.httpx, 'Client', lambda **kw: actual(transport=httpx.MockTransport(provider), **kw))
    key = K(); first = review(client, task, key)
    assert first['status'] == 'succeeded' and first['acceptance']['passed']
    assert first['result']['report']['findings'][0]['quote'].startswith('标题：')
    assert review(client, task, key)['id'] == first['id'] and len(calls) == 1
    queue = client.get('/api/v1/workflow/review-queue').json()['data']
    assert len(queue) == 1 and queue[0]['scope'] == 'image'
    view = state(client, task)
    assert client.get(f'/api/v1/tasks/{task}/workflow').json()['data']['videoAllowed']
    assert decision(client, task, view, reviewedMaterial=False).status_code == 400
    assert decision(client, task, view).status_code == 200
    assert state(client, task)['brandApproved']
    assert client.get('/api/v1/workflow/review-queue').json()['data'] == []
    new = save(client, task, list(reversed(assets)), baseVersionId=saved['version']['id'], body='新的图文正文')
    assert new['version']['ordinal'] == 2 and new['version']['images'][0]['id'] == assets[1]['id']
    assert not state(client, task)['brandApproved'] and state(client, task)['review'] is None
    assert decision(client, task, view).status_code == 409
    assert state(client, task)['versions'][1]['body'] == saved['version']['body']
    assert client.get(f"/api/v1/tasks/{task}/image-assets/{assets[0]['id']}/original").content == png('red')


def test_image_versions_and_assets_are_idempotent_and_task_scoped(client):
    p, task, rules = setup(client); key = K()
    asset = upload(client, task, key=key)
    assert upload(client, task, key=key)['id'] == asset['id']
    r = client.post(f'/api/v1/tasks/{task}/image-assets', files={'file': ('封面.png', png('blue'), 'image/png')}, headers={'Idempotency-Key': key})
    assert r.status_code == 409
    body = {**rules, 'creatorName': '另一位达人'}
    other = client.post(f"/api/v1/projects/{p['id']}/publish-task", json=body, headers={'Idempotency-Key': K()}).json()['data']['id']
    assert client.get(f"/api/v1/tasks/{other}/image-assets/{asset['id']}/content").status_code == 404
    r = client.post(f'/api/v1/tasks/{other}/image-posts', json={'title': '图文', 'body': '测试', 'images': [{'assetId': asset['id']}]}, headers={'Idempotency-Key': K()})
    assert r.status_code == 404
    value = {'title': '图文标题', 'body': '测试文案', 'images': [{'assetId': asset['id']}]}; key = K()
    first = client.post(f'/api/v1/tasks/{task}/image-posts', json=value, headers={'Idempotency-Key': key})
    again = client.post(f'/api/v1/tasks/{task}/image-posts', json=value, headers={'Idempotency-Key': key})
    assert first.json()['data'] == again.json()['data']
    assert len(state(client, task)['versions']) == 1
    conflict = client.post(f'/api/v1/tasks/{task}/image-posts', json={**value, 'body': '改动'}, headers={'Idempotency-Key': key})
    assert conflict.status_code == 409
    # A stale editor cannot silently create a competing new latest version.
    assert client.post(f'/api/v1/tasks/{task}/image-posts', json=value, headers={'Idempotency-Key': K()}).status_code == 409


@pytest.mark.parametrize('defect,code', [('schema', 'OUTPUT_SCHEMA_INVALID'), ('json', 'OUTPUT_INVALID_JSON'), ('truncated', 'OUTPUT_TRUNCATED'), ('image', 'EVIDENCE_MISSING'), ('quote', 'EVIDENCE_MISSING')])
def test_bad_image_reports_preserve_raw_and_never_approve(client, monkeypatch, defect, code):
    _, task, _ = setup(client); save(client, task, [upload(client, task)])
    actual = httpx.Client; calls = []
    def provider(request):
        calls.append(1); material = json.loads(json.loads(request.content)['messages'][1]['content'][0]['text']); value = report(material)
        if defect == 'schema':value['findings'][0]['description'] = {}
        if defect == 'image':value['image_checks'] = []
        if defect == 'quote':value['findings'][0]['quote_refs'] = ['fake']
        raw = '{broken' if defect == 'json' else json.dumps(value)
        return httpx.Response(200, json={'id': 'bad-image-id', 'choices': [{'finish_reason': 'length' if defect == 'truncated' else 'stop', 'message': {'content': raw}}]})
    monkeypatch.setattr(workflow.httpx, 'Client', lambda **kw: actual(transport=httpx.MockTransport(provider), **kw))
    key = K(); r = review(client, task, key)
    assert r['status'] == 'failed' and r['errorCode'] == code, r
    assert r['result']['modelResponse'] and r['result']['rawProviderResponse']
    assert not r['acceptance']['passed']
    assert decision(client, task, state(client, task)).status_code == 409
    assert review(client, task, key)['id'] == r['id'] and len(calls) == 1
    if defect == 'schema':assert r['result']['coverage']['validatedRules'] == 1


def test_rule_changes_and_brand_return_invalidate_image_approval(client, monkeypatch):
    p, task, body = setup(client); save(client, task, [upload(client, task)])
    monkeypatch.setattr(workflow, 'call_script_model', lambda c, k, s: {'report': report(s)})
    review(client, task); before = state(client, task)
    assert decision(client, task, before, 'returned', note='请重新制作封面').status_code == 200
    assert state(client, task)['decision']['note'] == '请重新制作封面'
    review(client, task); before = state(client, task)
    assert state(client, task)['decision'] is None
    assert decision(client, task, before).status_code == 200
    fields = {k: body[k] for k in ('scriptRules', 'videoRules', 'imageRules', 'scriptConfirmed', 'videoConfirmed', 'confirmationNote')}
    r = client.post(f"/api/v1/projects/{p['id']}/standards/confirm", json={**fields, 'revision': 1}, headers={'Idempotency-Key': K()})
    assert r.status_code == 200, r.text
    after = state(client, task)
    assert after['review'] is None and not after['brandApproved']
    assert decision(client, task, before).status_code == 409


def test_invalid_images_archive_and_tampering_are_blocked(client, monkeypatch):
    p, task, _ = setup(client)
    r = client.post(f'/api/v1/tasks/{task}/image-assets', files={'file': ('fake.png', b'not an image', 'image/png')}, headers={'Idempotency-Key': K()})
    assert r.status_code == 415
    asset = upload(client, task); saved = save(client, task, [asset]); view = state(client, task)
    path = client.app.state.settings.data_dir / 'image-post-assets' / (asset['id'] + '.png')
    path.write_bytes(b'changed')
    monkeypatch.setattr(workflow, 'call_script_model', lambda *a: pytest.fail('Tampered media must not reach model'))
    r = client.post(f'/api/v1/tasks/{task}/image-posts/reviews', json={'versionId': saved['version']['id'], 'contextKey': view['contextKey'], 'configRevision': 1, 'allowRealCall': True}, headers={'Idempotency-Key': K()})
    assert r.status_code == 409 and r.json()['error']['code'] == 'IMAGE_ASSET_CHANGED'
    with connection(client.app.state.settings.database_path) as db:db.execute("UPDATE projects SET status='archived' WHERE id=?", (p['id'],))
    r = client.post(f'/api/v1/tasks/{task}/image-posts', json={'title': '新标题', 'body': '新正文', 'images': [{'assetId': asset['id']}], 'baseVersionId': saved['version']['id']}, headers={'Idempotency-Key': K()})
    assert r.status_code == 409
    assert state(client, task)['writable'] is False
