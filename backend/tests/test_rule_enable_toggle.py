"""脚本/图文规则的启用开关：enabled=false 的规则不送模型、不参与验收，保存时保留状态。"""

import json

from app.modules.workflow import router as workflow
from test_script_versions import task_with_rule, submit, K
from test_workflow import configure, state, analyze, mock_script


def rules_with_disabled(template, count=2):
    """保留前 count 条为 must，其余全部停用；返回 (scriptRules, videoRules)。"""
    script = [dict(r) for r in template['scriptRules']]
    for index, rule in enumerate(script):
        rule['enabled'] = index >= count
    return script, [dict(r) for r in template['videoRules']]


def test_disabled_script_rules_are_saved_and_excluded_from_review(client, monkeypatch):
    project, task = task_with_rule(client)
    configure(client, task)
    template = client.get('/api/v1/workflow/standard-templates').json()['data'][0]
    script_rules, video_rules = rules_with_disabled(template)
    assert len([r for r in script_rules if r['enabled']]) >= 2 and len([r for r in script_rules if not r['enabled']]) >= 2
    revision = client.get(f"/api/v1/tasks/{task['id']}").json()['data']['revision']
    r = client.post(f"/api/v1/tasks/{task['id']}/standards/confirm", json={
        'scriptRules': script_rules, 'videoRules': video_rules, 'scriptConfirmed': True,
        'videoConfirmed': True, 'confirmationNote': '停用部分规则后重新确认', 'taskRevision': revision},
        headers={'Idempotency-Key': K()})
    assert r.status_code == 200, r.text
    view = state(client, task)
    saved = view['standardSets']['script']['rules']
    # 工作台展示的"生效标准"只列启用项，与送模型的规则一致
    assert {r['id'] for r in view['rules'] if r['reviewStages'] == ['script']} == {r['id'] for r in saved if r.get('enabled', True)}
    # 启用是默认值：保存时省略 enabled 键；停用必须显式留 enabled=false
    assert [rule.get('enabled', True) for rule in saved] == [rule['enabled'] for rule in script_rules], '保存后启用状态必须原样保留'
    assert all('enabled' not in rule for rule in saved if rule.get('enabled', True))
    assert view['rulesReady']

    # 模型只应收到启用中的规则
    seen = {}
    def model(config, key, snapshot):
        seen['rules'] = snapshot['rules']
        return mock_script(config, key, snapshot)
    monkeypatch.setattr(workflow, 'call_script_model', model)
    script = '开场口播麦咖啡，展示产品包装，结尾推荐。'
    assert submit(client, task['id'], {'inputType': 'text', 'text': script}).status_code == 201
    view = state(client, task)
    review = analyze(client, task, view)
    assert review.status_code == 200, review.text
    enabled_ids = {rule['id'] for rule in saved if rule.get('enabled', True)}
    assert {rule['id'] for rule in seen['rules']} == enabled_ids, '停用规则不得进入模型输入'
    assert [rule['ruleNumber'] for rule in seen['rules']] == list(range(1, len(enabled_ids) + 1)), '编号按启用规则连续重排'
    data = review.json()['data']
    assert {finding['rule_id'] for finding in data['result']['report']['findings']} == enabled_ids
    assert data['acceptance']['passed'] is True and len(data['acceptance']['checks']) == len(enabled_ids)


def test_disabling_every_must_rule_blocks_save_and_readiness(client):
    project, task = task_with_rule(client)
    template = client.get('/api/v1/workflow/standard-templates').json()['data'][0]
    script_rules = [dict(r, enabled=False) if r['importance'] == 'must' else dict(r) for r in template['scriptRules']]
    revision = client.get(f"/api/v1/tasks/{task['id']}").json()['data']['revision']
    r = client.post(f"/api/v1/tasks/{task['id']}/standards/confirm", json={
        'scriptRules': script_rules, 'videoRules': [dict(x) for x in template['videoRules']],
        'scriptConfirmed': True, 'videoConfirmed': True, 'confirmationNote': '全部必须项停用',
        'taskRevision': revision}, headers={'Idempotency-Key': K()})
    assert r.status_code == 400, r.text
    assert r.json()['error']['code'] == 'MUST_STANDARD_REQUIRED'
    assert '启用' in r.json()['error']['message']

    # 停用一条建议项不影响就绪；把唯一 must 停用则项目规则页 ready=false
    script_rules = [dict(r, enabled=r['importance'] != 'must') for r in template['scriptRules']]
    r = client.post(f"/api/v1/projects/{project['id']}/standards/confirm", json={
        'revision': 0, 'scriptRules': script_rules, 'videoRules': [dict(x) for x in template['videoRules']],
        'scriptConfirmed': True, 'videoConfirmed': True, 'confirmationNote': '仅保留建议项'},
        headers={'Idempotency-Key': K()})
    assert r.status_code == 400, r.text
    assert r.json()['error']['code'] == 'MUST_STANDARD_REQUIRED'


def test_context_key_changes_when_rules_are_re_enabled(client):
    """停用/启用改变"生效规则集"，必须让旧的脚本通过决定失效。"""
    project, task = task_with_rule(client)
    configure(client, task)
    before = state(client, task)['contextKey']
    template = client.get('/api/v1/workflow/standard-templates').json()['data'][0]
    script_rules, video_rules = rules_with_disabled(template)
    revision = client.get(f"/api/v1/tasks/{task['id']}").json()['data']['revision']
    r = client.post(f"/api/v1/tasks/{task['id']}/standards/confirm", json={
        'scriptRules': script_rules, 'videoRules': video_rules, 'scriptConfirmed': True,
        'videoConfirmed': True, 'confirmationNote': '停用两条', 'taskRevision': revision},
        headers={'Idempotency-Key': K()})
    assert r.status_code == 200, r.text
    after = state(client, task)['contextKey']
    assert after != before, '规则启用状态变化应使上下文失效'
