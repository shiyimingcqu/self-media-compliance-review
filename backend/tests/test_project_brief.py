from app.core.database import connection
from app.modules.workflow import router as workflow
from app.modules.workflow.project_brief import adopt_confirmed_project_briefs
from test_project_standards import add, rules, state
from test_separate_standards import project
from test_script_versions import K


def upload(client, p, text, revision=0, key=None):
    file = client.post('/api/v1/files', files={'file': ('brief.txt', text.encode(), 'text/plain')}, data={'purpose': 'brief'}, headers={'Idempotency-Key': K()}).json()['data']
    body = {'fileId': file['id'], 'title': '品牌项目 Brief', 'revision': revision}
    response = client.post(f'/api/v1/projects/{p}/brief', json=body, headers={'Idempotency-Key': key or K()})
    return response, body


def test_one_current_brief_versions_idempotency_and_failed_parse_preserves_current(client):
    p = project(client)['id']; path = f'/api/v1/projects/{p}/brief'
    assert client.get(path).json()['data'] == {'projectId': p, 'revision': 0, 'brief': None}
    key = K(); response, body = upload(client, p, '产品名称为芝士浓奶铁。', key=key)
    assert response.status_code == 200, response.text
    first = response.json()['data']['brief']
    assert first['blocks'][0]['text'] == '产品名称为芝士浓奶铁。'
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).json()['data']['brief']['sourceVersionId'] == first['sourceVersionId']
    response, _ = upload(client, p, '新版卖点：浓郁芝士奶香。', revision=0)
    assert response.status_code == 409
    assert client.get(path).json()['data']['revision'] == 1
    response, _ = upload(client, p, '新版卖点：浓郁芝士奶香。', revision=1)
    assert response.status_code == 200, response.text
    second = client.get(path).json()['data']['brief']
    assert second['revision'] == 2 and second['sourceVersionId'] != first['sourceVersionId']
    response, _ = upload(client, p, '   ', revision=2)
    assert response.status_code == 200 and response.json()['data']['registration']['extractionStatus'] == 'invalid'
    assert client.get(path).json()['data']['brief']['sourceVersionId'] == second['sourceVersionId']
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM project_current_briefs WHERE project_id=?', (p,)).fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM brief_source_versions WHERE id IN (?,?)', (first['sourceVersionId'], second['sourceVersionId'])).fetchone()[0] == 2
    confirmed = rules(client); confirmed['sourceVersionId'] = first['sourceVersionId']
    assert client.post(f'/api/v1/projects/{p}/standards/confirm', json=confirmed, headers={'Idempotency-Key': K()}).status_code == 200
    with connection(client.app.state.settings.database_path) as db:
        # Existing current choice always wins, even when older rules cite another source.
        adopt_confirmed_project_briefs(db)
        assert db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?', (p,)).fetchone()[0] == second['sourceVersionId']
        db.execute('DELETE FROM project_current_briefs WHERE project_id=?', (p,))
        adopt_confirmed_project_briefs(db)
        assert db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?', (p,)).fetchone()[0] == first['sourceVersionId']


def test_script_receives_brief_and_replacement_invalidates_all_creators(client, monkeypatch):
    p = project(client)['id']
    client.post(f'/api/v1/projects/{p}/standards/confirm', json=rules(client), headers={'Idempotency-Key': K()})
    a, b = add(client, p, '甲'), add(client, p, '乙')
    assert upload(client, p, '品牌 Brief：产品名称为芝士浓奶铁。')[0].status_code == 200
    client.put('/api/v1/settings/models', json={'endpoint': 'https://models.example.com/v1', 'model': 'mock', 'revision': 0, 'apiKey': 'sk-test-only'})
    captured = []
    def model(config, key, snapshot):
        captured.append(snapshot)
        return {'report': {'summary': '已核对原文与规则', 'findings': [{'rule_id': r['id'], 'verdict': 'compliant', 'quote': '', 'description': '符合', 'suggestion': ''} for r in snapshot['rules']]}}
    monkeypatch.setattr(workflow, 'call_script_model', model)
    response = client.post(f'/api/v1/tasks/{a}/scripts/submit-and-review', json={'inputType': 'text', 'text': '芝士浓奶铁产品介绍。', 'configRevision': 1, 'allowRealCall': True}, headers={'Idempotency-Key': K()})
    assert response.status_code == 201, response.text
    # 判断阶段只依据规则：Brief 全文不再进入模型输入，只保留验收所需的覆盖元数据。
    assert 'brief' not in captured[0]
    assert captured[0]['briefCoverage']['uncoveredSources'] == []
    view = state(client, a)
    assert view['brandReviewAllowed']
    response = client.post(f'/api/v1/tasks/{a}/workflow/decisions', json={'scope': 'script', 'targetId': view['scriptReview']['id'], 'contextKey': view['contextKey'], 'decision': 'approved', 'note': '品牌确认', 'reviewedMaterial': True}, headers={'Idempotency-Key': K()})
    assert response.status_code == 200 and state(client, a)['videoAllowed']
    before = {t: state(client, t)['contextKey'] for t in (a, b)}
    assert upload(client, p, '新版 Brief：不允许提及醒脑功效。', revision=1)[0].status_code == 200
    for t in (a, b):
        assert state(client, t)['contextKey'] != before[t]
        assert state(client, t)['videoAllowed']
    # Historical model input stays immutable: the frozen brief coverage still points at the old source.
    old = client.get(f'/api/v1/tasks/{a}/workflow').json()['data']['scriptReviews'][0]
    assert 'brief' not in old['snapshot']
    assert old['snapshot']['briefCoverage']['sourceVersionId'] == captured[0]['briefCoverage']['sourceVersionId']
