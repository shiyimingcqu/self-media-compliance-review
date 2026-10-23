"""规则作用域与自包含：判断阶段只依据规则，Brief 只用于生成规则。"""
import json

from app.core.database import connection
from app.modules.workflow import router as workflow
from test_script_versions import task_with_rule, submit, K
from test_separate_standards import project, payload


def confirm(client, project_id, body, revision=0):
    body = {k: v for k, v in body.items() if k not in ('creatorName', 'platform', 'campaignPhase')}
    return client.post(f"/api/v1/projects/{project_id}/standards/confirm",
                       json={**body, 'revision': revision}, headers={'Idempotency-Key': K()})


def test_external_reference_rule_is_reported_at_confirmation(client):
    p = project(client); body = payload(client)
    body['scriptRules'] = [
        {'text': '产品名称正确', 'criterion': '对照品牌提供信息检查台词中的产品名', 'category': '品牌信息', 'importance': 'must'},
        {'text': '镜头安排', 'criterion': '脚本应有产品展示镜头和位置', 'category': '镜头安排', 'importance': 'must'},
    ]
    r = confirm(client, p['id'], body)
    assert r.status_code == 200, r.text
    warnings = r.json()['data']['standardWarnings']['script']
    assert len(warnings) == 1 and warnings[0]['ruleNumber'] == 1
    assert '对照品牌提供' in warnings[0]['reason']
    view = client.get(f"/api/v1/projects/{p['id']}/standards").json()['data']
    assert len(view['standardWarnings']['script']) == 1


def test_params_make_rule_self_contained_and_are_frozen(client):
    p = project(client); body = payload(client)
    body['scriptRules'] = [
        {'text': '产品名称正确', 'criterion': '台词中的产品名必须是芝士浓奶铁', 'category': '品牌信息', 'importance': 'must',
         'params': {'names': ['芝士浓奶铁'], 'terms': [], 'thresholds': []}},
        {'text': '镜头安排', 'criterion': '脚本应有产品展示镜头和位置', 'category': '镜头安排', 'importance': 'must'},
    ]
    r = confirm(client, p['id'], body)
    assert r.status_code == 200, r.text
    assert r.json()['data']['standardWarnings']['script'] == []
    view = client.get(f"/api/v1/projects/{p['id']}/standards").json()['data']
    frozen = view['script']['rules']
    assert frozen[0]['params']['names'] == ['芝士浓奶铁']
    assert view['standardWarnings']['script'] == []


def test_params_check_reports_literal_presence():
    rules = [{'id': 'r1', 'params': {'names': ['芝士浓奶铁'], 'terms': ['提神醒脑'],
                                     'thresholds': [{'key': 'minTotalSeconds', 'value': 5}]}}]
    facts = workflow.params_check(rules, '台词：麦咖啡芝士浓奶铁，但不是冰奶铁')
    by_value = {(f['kind'], f['value']): f for f in facts}
    assert by_value[('name', '芝士浓奶铁')]['present'] is True
    assert by_value[('term', '提神醒脑')]['present'] is False
    assert by_value[('threshold', 'minTotalSeconds')]['expected'] == 5


def test_script_review_input_excludes_brief_and_carries_params_check(client, monkeypatch):
    p, task = task_with_rule(client)
    client.put('/api/v1/settings/models', json={'endpoint': 'https://models.example.com/v1',
                                                'model': 'test-model', 'revision': 0, 'apiKey': 'sk-local-test-only'})
    template = client.get('/api/v1/workflow/standard-templates').json()['data'][0]
    template['scriptRules'][0]['params'] = {'names': ['芝士浓奶铁'], 'terms': [], 'thresholds': []}
    revision = client.get(f"/api/v1/tasks/{task['id']}").json()['data']['revision']
    confirmed = client.post(f"/api/v1/tasks/{task['id']}/standards/confirm",
                            json={'scriptRules': template['scriptRules'], 'videoRules': template['videoRules'],
                                  'scriptConfirmed': True, 'videoConfirmed': True,
                                  'confirmationNote': '品牌确认', 'taskRevision': revision},
                            headers={'Idempotency-Key': K()})
    assert confirmed.status_code == 200, confirmed.text
    assert submit(client, task['id'], {'inputType': 'text', 'text': '开场口播麦咖啡冰奶铁，展示包装。'}).status_code == 201
    view = client.get(f"/api/v1/tasks/{task['id']}/workflow").json()['data']
    captured = {}

    def model(config, key, snapshot):
        captured['snapshot'] = snapshot
        return {'report': {'summary': '模拟', 'findings': [
            {'rule_id': r['id'], 'verdict': 'compliant', 'quote': '', 'description': '已安排', 'suggestion': ''}
            for r in snapshot['rules']]}, 'providerRequestIds': ['mock'], 'usage': []}

    monkeypatch.setattr(workflow, 'call_script_model', model)
    r = client.post(f"/api/v1/tasks/{task['id']}/workflow/script-reviews",
                    json={'scriptVersionId': view['currentScript']['scriptVersionId'], 'contextKey': view['contextKey'],
                          'configRevision': 1, 'allowRealCall': True}, headers={'Idempotency-Key': K()})
    assert r.status_code == 200, r.text
    snapshot = captured['snapshot']
    # Brief 全文不再进入判断输入；只保留验收所需的覆盖元数据。
    assert 'brief' not in snapshot
    assert snapshot['briefCoverage']['uncoveredSources'] == []
    facts = {(f['kind'], f['value']): f['present'] for f in snapshot['paramsCheck']}
    assert facts[('name', '芝士浓奶铁')] is False  # 脚本写的是「冰奶铁」，逐字核对如实反映

def test_template_rules_are_written_in_brand_language(client):
    """默认规则给品牌看，判据里不能出现 params.names 这类程序内部键名。"""
    templates = client.get('/api/v1/workflow/standard-templates').json()['data']
    assert len(templates) == 3
    for template in templates:
        for rule in template['scriptRules'] + template['videoRules']:
            for field in ('text', 'criterion'):
                value = rule[field]
                assert 'params' not in value.lower(), f"{template['name']}「{rule['text']}」的{field}出现了内部键名：{value}"
                assert '.names' not in value and '.terms' not in value and '.thresholds' not in value


def test_script_recommendation_drops_video_layer_fields():
    """模型若带回 layer/组件，脚本规则直接丢弃并提示，否则保存会被判三层配置只适用于视频。"""
    from app.modules.workflow import standards
    value = {'rules': [{'text': '产品名正确', 'criterion': '台词须逐字包含「芝士浓奶铁」', 'category': '品牌信息',
                        'importance': 'must', 'source_block_id': 'b1', 'source_quote': '产品全名：芝士浓奶铁。',
                        'layer': 'brief', 'enabled': True, 'baselineKey': 'product', 'presetKey': 'product_duration'}], 'warnings': []}
    blocks = [{'id': 'b1', 'pageNo': 1, 'text': '产品全名：芝士浓奶铁。'}]
    report = standards.validate_recommendation(value, blocks, 'script')
    rule = report.rules[0]
    assert rule.layer is None and rule.baseline_key is None and rule.preset_key is None and rule.component_plan is None
    notes=[warning for warning in report.warnings if '三层配置' in warning]
    assert notes and notes[0].startswith(standards.SYSTEM_NOTE_PREFIX)
    video = standards.validate_recommendation(value, blocks, 'video')
    assert video.rules[0].layer == 'brief' and video.rules[0].baseline_key == 'product'
