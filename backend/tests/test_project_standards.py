import json
from copy import deepcopy
from uuid import uuid4

from app.core.database import connection
from app.modules.workflow import standards, router as workflow
from test_separate_standards import project, payload
from test_script_versions import K


def rules(client, revision=0):
    body = payload(client)
    return {k: v for k, v in body.items() if k not in ('creatorName', 'platform', 'campaignPhase')} | {'revision': revision}


def add(client, project_id, name):
    response = client.post(f'/api/v1/projects/{project_id}/creators', json={'creatorName': name, 'platform': 'douyin'}, headers={'Idempotency-Key': K()})
    assert response.status_code == 201, response.text
    return response.json()['data']['id']


def state(client, task_id):
    return client.get(f'/api/v1/tasks/{task_id}/workflow').json()['data']


def test_image_rules_saved_separately_and_preserved_for_older_clients(client):
    p = project(client)['id']; path = f'/api/v1/projects/{p}/standards/confirm'
    body = rules(client)
    body['imageRules'] = [{'text': '封面完整展示产品', 'criterion': '产品主体及标识完整清晰', 'importance': 'must'}]
    assert client.post(path, json=body, headers={'Idempotency-Key': K()}).status_code == 200
    view = client.get(f'/api/v1/projects/{p}/standards').json()['data']
    assert view['image']['rules'][0]['text'] == '封面完整展示产品'
    assert view['image']['rules'][0]['reviewStages'] == ['image']
    assert view['script']['rules'][0]['text'] != view['image']['rules'][0]['text']
    original_image_id = view['image']['id']
    older = rules(client, revision=1)
    assert client.post(path, json=older, headers={'Idempotency-Key': K()}).status_code == 200
    view = client.get(f'/api/v1/projects/{p}/standards').json()['data']
    assert view['image']['id'] == original_image_id
    body['revision'] = 2; body['imageRules'] = []
    assert client.post(path, json=body, headers={'Idempotency-Key': K()}).status_code == 400


def test_project_rules_shared_by_creators_and_updates_invalidate_all(client, monkeypatch):
    p = project(client)['id']; path = f'/api/v1/projects/{p}/standards/confirm'
    response = client.post(f'/api/v1/projects/{p}/creators', json={'creatorName': '甲', 'platform': 'douyin'}, headers={'Idempotency-Key': K()})
    assert response.status_code == 409
    body = rules(client); key = K()
    response = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 200, response.text
    assert client.get(f'/api/v1/projects/{p}/tasks').json()['total'] == 0
    a, b = add(client, p, '甲'), add(client, p, '乙')
    assert state(client, a)['standardSets'] == state(client, b)['standardSets']
    client.put('/api/v1/settings/models', json={'endpoint': 'https://models.example.com/v1', 'model': 'mock', 'revision': 0, 'apiKey': 'sk-test-only'})
    snapshots = []
    def model(config, key, snapshot):
        snapshots.append(snapshot)
        return {'report': {'summary': '模拟通过', 'mandatory_checks': [{'rule_id': r['id'], 'requirement': r['text'], 'verdict': 'compliant', 'quote': '', 'reason': '逐条检查'} for r in snapshot['rules'] if r['importance'] == 'must'], 'findings': [{'rule_id': r['id'], 'verdict': 'compliant', 'quote': '', 'description': '符合', 'suggestion': ''} for r in snapshot['rules']]}}
    monkeypatch.setattr(workflow, 'call_script_model', model)
    for task in (a, b):
        response = client.post(f'/api/v1/tasks/{task}/scripts/submit-and-review', json={'inputType': 'text', 'text': '品牌产品介绍与体验。', 'configRevision': 1, 'allowRealCall': True}, headers={'Idempotency-Key': K()})
        assert response.status_code == 201, response.text
        view = state(client, task)
        assert view['brandReviewAllowed'], view
        response = client.post(f'/api/v1/tasks/{task}/workflow/decisions', json={'scope': 'script', 'targetId': view['scriptReview']['id'], 'contextKey': view['contextKey'], 'decision': 'approved', 'note': '品牌确认', 'reviewedMaterial': True}, headers={'Idempotency-Key': K()})
        assert response.status_code == 200, response.text
        assert state(client, task)['videoAllowed']
    assert snapshots[0]['rules'] == snapshots[1]['rules']
    before = {t: state(client, t) for t in (a, b)}
    changed = deepcopy(body); changed['revision'] = 1; changed['scriptRules'][0]['criterion'] = '更新后的项目品牌要求'
    assert client.post(path, json=changed, headers={'Idempotency-Key': K()}).status_code == 200
    assert client.post(path, json=body, headers={'Idempotency-Key': K()}).status_code == 409
    # Retrying an old successful request is a replay, never another revision.
    assert client.post(path, json=body, headers={'Idempotency-Key': key}).json()['data']['revision'] == 1
    for t in (a, b):
        after = state(client, t)
        assert after['videoAllowed'] and not after['brandReviewAllowed']
        assert after['contextKey'] != before[t]['contextKey']
        assert after['standardSets']['script']['revision'] == 2
        assert after['standardSets']['script']['rules'][0]['criterion'] == '更新后的项目品牌要求'
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM task_standard_sets').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM project_standard_sets WHERE project_id=?', (p,)).fetchone()[0] == 4
        assert db.execute('SELECT COUNT(*) FROM workflow_decisions WHERE task_id IN (?,?)', (a, b)).fetchone()[0] == 2
    legacy = payload(client); legacy['scriptRules'][0]['criterion'] = '试图为一个达人设置不同要求'
    assert client.post(f'/api/v1/projects/{p}/publish-task', json=legacy, headers={'Idempotency-Key': K()}).status_code == 409


def test_legacy_migration_only_adopts_identical_rules(client):
    for conflicting in (False, True):
        p = client.post('/api/v1/projects', json={'name': '迁移项目' + str(conflicting), 'brandName': '品牌', 'platforms': ['douyin']}, headers={'Idempotency-Key': K()}).json()['data']['id']; body = rules(client)
        assert client.post(f'/api/v1/projects/{p}/standards/confirm', json=body, headers={'Idempotency-Key': K()}).status_code == 200
        tasks = [add(client, p, name) for name in ('甲', '乙')]
        with connection(client.app.state.settings.database_path) as db:
            pair = standards.project_sets_for(db, p)
            for index, task in enumerate(tasks):
                for scope in ('script', 'video'):
                    legacy_rules = deepcopy(pair[scope]['rules'])
                    # Legacy rule identities differ per creator, content stays the same.
                    for rule in legacy_rules: rule['id'] = uuid4().hex
                    if conflicting and index == 1 and scope == 'script': legacy_rules[0]['criterion'] = '不同规则'
                    db.execute('INSERT INTO task_standard_sets VALUES (?,?,?,?,?,?,?,?,?,?)', (uuid4().hex, task, scope, json.dumps(legacy_rules), None, 'douyin', 'general', '历史品牌确认', 'brand', pair[scope]['confirmed_at']))
            db.execute('DELETE FROM project_standard_sets WHERE project_id=?', (p,))
            standards.adopt_legacy_project_standards(db)
            view = standards.project_standards_view(db, p)
            assert view['ready'] is (not conflicting)
            assert len(view['legacyCandidates']) == (2 if conflicting else 0)
            assert bool(standards.sets_for(db, tasks[0])['script']) is (not conflicting)
            assert db.execute('SELECT COUNT(*) FROM task_standard_sets WHERE task_id IN (?,?)', tasks).fetchone()[0] == 4
