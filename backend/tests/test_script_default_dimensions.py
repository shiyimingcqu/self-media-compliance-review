from app.modules.workflow.prompts import prompt
from app.modules.workflow.script_defaults import SCRIPT_DIMENSIONS, default_script_rules
from app.modules.workflow.standards import StandardRule
from test_separate_standards import project, payload
from test_script_versions import K


def test_default_template_and_paragraph_share_eight_dimensions(client):
    expected = [row[0] for row in SCRIPT_DIMENSIONS]
    templates = client.get('/api/v1/workflow/standard-templates').json()['data']
    rules = templates[0]['scriptRules']
    assert len(rules) == 8
    assert [r['category'] for r in rules] == expected
    assert [r['category'] for r in rules if r['importance'] == 'suggested'] == ['知识产权', '口碑风险', '品牌调性匹配']
    for rule in rules:
        StandardRule.model_validate(rule)
        assert rule['origin'] == 'template'
        assert not rule['sourceQuote'] and not rule['sourceBlockId']
    defaults = client.get('/api/v1/workflow/script-rules/defaults').json()['data']
    assert all(category in defaults['rulesText'] for category in expected)
    for template in templates[1:]:
        assert template['scriptRules'][:8] == rules


def test_eight_rules_can_be_published_without_changing_saved_rules(client):
    p = project(client)
    body = payload(client)
    body['scriptRules'][0]['text'] = '品牌自己确认的广告表达要求'
    response = client.post(f"/api/v1/projects/{p['id']}/publish-task", json=body, headers={'Idempotency-Key': K()})
    assert response.status_code == 201, response.text
    task_id = response.json()['data']['id']
    before = client.get(f'/api/v1/tasks/{task_id}').json()['data']
    # 读取新模板只产生草稿输入，不能覆盖品牌已冻结的内容。
    client.get('/api/v1/workflow/standard-templates')
    client.get('/api/v1/workflow/script-rules/defaults')
    after = client.get(f'/api/v1/tasks/{task_id}').json()['data']
    assert before == after
    assert default_script_rules()[0]['text'] != body['scriptRules'][0]['text']


def test_review_and_both_generation_paths_use_shared_prompt_contract():
    for key in ('script-review', 'brief-script-standards', 'brief-script-rules-text'):
        content = prompt(key)['content']
        assert '【默认脚本审核：八个维度】' in content
        assert '不输出 Markdown 表格' in content
        assert '不追加隐藏门槛' in content or '不是给旧规则追加隐藏门槛' in content
        assert '实际发布未发生' in content
        assert 'source_block_id 与 source_quote 留空' in content
        if key != 'script-review':
            assert '【通用脚本模板与判据】' in content
            assert all(row[2] in content for row in SCRIPT_DIMENSIONS)
