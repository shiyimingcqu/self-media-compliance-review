import json

import httpx

from app.core.database import connection
from app.modules.workflow import standards
from test_script_versions import K, task_with_rule


def test_unsaved_video_draft_is_frozen_and_sent_once(client, monkeypatch):
    project, task = task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source = db.execute('SELECT source_version_id FROM rule_versions WHERE id=?', (task['ruleVersionId'],)).fetchone()[0]
    configured = client.put('/api/v1/settings/models/decomposition', json={'endpoint': 'https://example.com/v1', 'model': 'test-model', 'revision': 0, 'apiKey': 'fixture-secret-123'})
    assert configured.status_code == 200, configured.text
    existing = {'text': '前15秒内展示产品', 'criterion': '品牌允许轻微遮挡', 'enabled': False, 'origin': 'manual', 'layer': 'brief'}
    calls = []

    def model(config, secret, template, blocks, uncovered, facts=None, existing_rules=None):
        calls.append(existing_rules)
        assert existing_rules[0]['text'] == existing['text']
        assert existing_rules[0]['enabled'] is False
        assert existing_rules[0]['criterion'] == existing['criterion']
        assert '只补缺失要求' in template['content']
        return {'report': {'rules': [], 'warnings': []}, 'modelResponse': '{"rules":[],"warnings":[]}'}

    monkeypatch.setattr(standards, 'call_recommendation', model)
    key = K()
    body = {'sourceVersionId': source, 'scope': 'video', 'videoRuleMode': 'template', 'configRevision': 1, 'allowRealCall': True, 'existingVideoRules': [existing]}
    url = f"/api/v1/projects/{project['id']}/standard-recommendations"
    response = client.post(url, json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 200, response.text
    value = response.json()['data']
    assert value['status'] == 'succeeded', value
    assert value['snapshot']['existingVideoRules'][0]['text'] == existing['text']
    assert all(rule['layer'] == 'baseline' for rule in value['result']['report']['rules'])
    assert client.post(url, json=body, headers={'Idempotency-Key': key}).json()['data']['id'] == value['id']
    assert len(calls) == 1


def test_current_rules_are_user_material_not_system_instructions(monkeypatch):
    actual = httpx.Client
    seen = []

    def provider(request):
        body = json.loads(request.content)
        seen.append(body)
        user = json.loads(body['messages'][1]['content'])
        assert user['existingVideoRules'] == [{'text': '待保留的当前要求', 'enabled': False}]
        assert '待保留的当前要求' not in body['messages'][0]['content']
        return httpx.Response(200, json={'id': 'one', 'choices': [{'message': {'content': '{"rules":[],"warnings":[]}'}}]})

    monkeypatch.setattr(standards.httpx, 'Client', lambda **kw: actual(transport=httpx.MockTransport(provider), **kw))
    standards.call_recommendation({'endpoint': 'https://example.com/v1', 'model': 'omni'}, 'fixture', {'key': 'brief-video-template', 'content': '补充规则'}, [], [], existing_rules=[{'text': '待保留的当前要求', 'enabled': False}])
    assert len(seen) == 1
