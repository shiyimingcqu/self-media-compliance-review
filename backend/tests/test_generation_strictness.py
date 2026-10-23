import pytest
from app.core.database import connection
from app.modules.workflow import standards
from app.modules.workflow.prompts import recommendation_prompt
from test_script_versions import task_with_rule, K


@pytest.mark.parametrize('scope', ['video', 'image'])
def test_generation_levels_change_prompt_and_snapshot_without_auto_confirmation(client, monkeypatch, scope):
    project, task = task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source = db.execute('SELECT source_version_id FROM rule_versions WHERE id=?', (task['ruleVersionId'],)).fetchone()[0]
    assert client.put('/api/v1/settings/models/decomposition', json={
        'endpoint': 'https://models.example.com/v1', 'model': 'model1', 'revision': 0, 'apiKey': 'sk-test-only',
    }).status_code == 200
    calls = []

    def model(config, secret, template, blocks, uncovered):
        calls.append(template)
        return {'report': {'rules': [{'text': '品牌名称准确', 'criterion': '核对品牌名称',
            'importance': 'must', 'sourceBlockId': blocks[0]['id'], 'sourceQuote': blocks[0]['text']}], 'warnings': []}}

    monkeypatch.setattr(standards, 'call_recommendation', model)
    url = f"/api/v1/projects/{project['id']}/standard-recommendations"
    body = {'sourceVersionId': source, 'scope': scope, 'configRevision': 1, 'allowRealCall': True}
    key = K()
    default = client.post(url, json=body, headers={'Idempotency-Key': key}).json()['data']
    assert default['snapshot']['strictness'] == 'lenient'
    same = client.post(url, json={**body, 'strictness': 'lenient'}, headers={'Idempotency-Key': key})
    assert same.json()['data']['id'] == default['id']
    conflict = client.post(url, json={**body, 'strictness': 'standard'}, headers={'Idempotency-Key': key})
    assert conflict.status_code == 409
    for level in ('standard', 'strict'):
        value = client.post(url, json={**body, 'strictness': level}, headers={'Idempotency-Key': K()}).json()['data']
        assert value['status'] == 'succeeded'
        assert value['snapshot']['strictness'] == level
        assert value['snapshot']['prompt']['profile']['key'] == 'rule-generation-' + level
    assert len(calls) == 3
    assert len({p['version'] for p in calls}) == 3
    assert client.post(url, json={**body, 'strictness': 'unknown'}, headers={'Idempotency-Key': K()}).status_code == 400
    assert len(calls) == 3
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM task_standard_sets WHERE task_id=?', (task['id'],)).fetchone()[0] == 0


def test_every_profile_retains_shared_boundaries_for_script_video_and_paragraph():
    for key in ('brief-script-standards', 'brief-video-standards', 'brief-image-standards', 'brief-script-rules-text'):
        for level in ('lenient', 'standard', 'strict'):
            template = recommendation_prompt(key, level)
            assert '明确数值' in template['content']
            assert '不可因档位被删除或放宽' in template['content']
            assert '不能升级为must' in template['content']
