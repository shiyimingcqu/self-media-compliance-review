import json

from app.core.database import connection
from test_script_versions import task_with_rule, submit, K


def seed(client):
    project, task = task_with_rule(client)
    response = submit(client, task['id'], {'inputType': 'text', 'text': '历史脚本原文：产品已经展示。'})
    assert response.status_code == 201, response.text
    version = response.json()['data']['scriptVersionId']
    rules = [{'id': 'rule-1', 'ruleNumber': 1, 'text': '产品展示', 'criterion': '旧规则：每个镜头都展示产品', 'importance': 'must'},
             {'id': 'rule-2', 'ruleNumber': 2, 'text': '构图建议', 'criterion': '画面更美观', 'importance': 'suggested'}]
    snapshot = json.dumps({'rules': rules, 'script': '历史脚本原文：产品已经展示。', 'endpoint': 'PRIVATE_ENDPOINT'})
    with connection(client.app.state.settings.database_path) as db:
        for identity, status, findings, error in [
            ('script-blocked', 'succeeded', [{'rule_id': 'rule-1', 'verdict': 'non_compliant', 'description': '缺少每镜头露出'}], None),
            ('script-technical', 'failed', [], 'OUTPUT_INVALID_JSON'),
            ('script-pending', 'succeeded', [{'rule_id': 'rule-1', 'verdict': 'needs_confirmation', 'description': '文字无法确认'}], None),
            ('script-advisory', 'succeeded', [{'rule_id': 'rule-2', 'verdict': 'non_compliant', 'description': '优化构图'}], None),
            ('script-passed', 'succeeded', [{'rule_id': 'rule-1', 'verdict': 'compliant'}], None),
        ]:
            result = json.dumps({'report': {'summary': '历史审核意见', 'findings': findings}, 'modelResponse': 'PRIVATE_RAW_OUTPUT'})
            db.execute('INSERT INTO workflow_script_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, task['id'], version, None, 'old-context', snapshot, status, result, error,
                        '调用未完成' if error else None, '2026-10-21T10:00:00Z', '2026-10-21T10:00:00Z'))
        db.execute('INSERT INTO workflow_decisions VALUES (?,?,?,?,?,?,?,?,?)',
                   ('return-decision', task['id'], 'script', 'script-passed', 'old-context', 'returned', '请调整剧情节奏', 'brand', '2026-10-21T11:00:00Z'))
        db.execute('INSERT INTO evidence_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('video-blocked', 'video', task['id'], None, 'succeeded', 1, snapshot,
                    json.dumps({'report': {'summary': '视频问题', 'findings': [{'rule_id': 'rule-1', 'verdict': 'non_compliant', 'description': '片段展示不足'}]}}),
                    None, None, '2026-10-21T12:00:00Z', '2026-10-21T12:00:00Z'))
        # Revision 2 is different; old failures must keep their frozen rule, not receive today's text.
        for scope in ('script', 'video'):
            db.execute('INSERT INTO project_standard_sets VALUES (?,?,?,?,?,?,?,?,?)',
                       (K(), project['id'], scope, json.dumps([{**rules[0], 'criterion': '新规则：产品可辨认即可'}]), None,
                        '调整规则', 'brand', '2026-10-22T10:00:00Z', 2))
    return project['id'], task['id']


def test_feedback_includes_old_failures_after_success_and_separates_technical_errors(client):
    project, task = seed(client)
    url = f'/api/v1/projects/{project}/submission-feedback'
    response = client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['total'] == 5
    assert data['counts'] == {'needs_changes': 2, 'needs_confirmation': 1, 'call_failed': 1, 'returned': 1}
    assert client.get(url + '?creatorName=不存在的达人').json()['data']['total'] == 0
    assert {item['id'] for item in data['items']} == {'script-blocked', 'script-pending', 'script-technical', 'script-passed', 'video-blocked'}
    assert all(not item['rulesCurrent'] for item in data['items'])
    assert 'PRIVATE_RAW_OUTPUT' not in response.text and 'PRIVATE_ENDPOINT' not in response.text
    technical = client.get(url + '?outcome=call_failed').json()['data']
    assert technical['total'] == 1
    assert technical['items'][0]['errorCode'] == 'OUTPUT_INVALID_JSON'
    assert technical['items'][0]['reportVerified'] is False
    page = client.get(url + '?scope=script&limit=2&offset=2').json()['data']
    assert page['total'] == 4 and len(page['items']) == 2
    history = client.get(url + '/script/script-blocked').json()['data']
    assert history['scriptText'].startswith('历史脚本原文')
    assert history['issues'][0]['criterion'] == '旧规则：每个镜头都展示产品'
    assert client.get(url + '/script/video-blocked').status_code == 404
    # Reading history does not change review outcomes or current rules.
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT status FROM workflow_script_reviews WHERE id='script-blocked'").fetchone()[0] == 'succeeded'


def test_feedback_is_project_scoped_and_keeps_archived_history_readable(client):
    project, task = seed(client)
    other, other_task = task_with_rule(client)
    other_url = f"/api/v1/projects/{other['id']}/submission-feedback"
    assert client.get(other_url).json()['data']['total'] == 0
    assert client.get(other_url + '/script/script-blocked').status_code == 404
    assert client.get(other_url + '?taskId=' + task).status_code == 404
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE projects SET status='archived' WHERE id=?", (project,))
        db.execute("UPDATE creator_tasks SET archived_at='2026-10-22T12:00:00Z' WHERE id=?", (task,))
    result = client.get(f'/api/v1/projects/{project}/submission-feedback?taskId={task}').json()['data']
    assert result['total'] == 5 and all(item['taskArchived'] for item in result['items'])


def test_video_history_layers_use_frozen_rules_and_configuration_is_not_creator_violation(client):
    project, _ = seed(client)
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute("SELECT snapshot_json FROM evidence_runs WHERE id='video-blocked'").fetchone()
        snapshot = json.loads(row['snapshot_json'])
        snapshot['rules'][0]['layer'] = 'baseline'
        snapshot['rules'][0]['baselineKey'] = 'product'
        db.execute("UPDATE evidence_runs SET snapshot_json=? WHERE id='video-blocked'", (json.dumps(snapshot),))
    url = f'/api/v1/projects/{project}/submission-feedback'
    data = client.get(url + '?layer=baseline').json()['data']
    assert data['total'] == 1 and data['items'][0]['issues'][0]['layer'] == 'baseline'
    assert data['items'][0]['issues'][0]['criterion'] == '旧规则：每个镜头都展示产品'
    assert client.get(url + '?layer=precise').json()['data']['total'] == 0
    snapshot['unconfiguredRules'] = [{'ruleId': 'rule-1', 'ruleNumber': 1, 'layer': 'baseline', 'text': '指定产品出现', 'importance': 'must', 'reason': '待品牌确认产品名称'}]
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE evidence_runs SET snapshot_json=?,result_json=? WHERE id='video-blocked'", (json.dumps(snapshot), json.dumps({'report': {'summary': '其他项已审核', 'findings': []}})))
    item = client.get(url + '?layer=baseline').json()['data']['items'][0]
    assert item['outcome'] == 'needs_confirmation' and item['configurationRequired'] is True
    assert item['issues'] == [] and item['unconfiguredRules'][0]['reason'] == '待品牌确认产品名称'
