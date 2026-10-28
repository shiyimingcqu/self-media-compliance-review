import json

import pytest

from app.core.database import connection
from test_creator_discussions import seed
from test_script_versions import submit
from test_workflow import decide, state


def pending_review(client, monkeypatch):
    _, task, rid, _ = seed(client, monkeypatch)
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT result_json FROM workflow_script_reviews WHERE id=?', (rid,)).fetchone()
        result = json.loads(row['result_json'])
        for finding in result['report']['findings']:
            finding['verdict'] = 'needs_confirmation'
        db.execute('UPDATE workflow_script_reviews SET result_json=? WHERE id=?', (json.dumps(result), rid))
    view = state(client, task)
    ids = [c['rule_id'] for c in view['scriptAcceptance']['checks']]
    return task, rid, view, ids


def test_pending_rules_need_explicit_complete_confirmation_and_survive_refresh(client, monkeypatch):
    task, rid, view, ids = pending_review(client, monkeypatch)
    assert ids and not view['brandReviewAllowed']
    assert decide(client, task, view).status_code == 409
    assert decide(client, task, view, confirmedRuleIds=ids[:-1]).status_code == 409
    assert decide(client, task, view, confirmedRuleIds=ids + ['another-review-rule']).status_code == 409
    assert decide(client, task, view, confirmedRuleIds=ids, reviewedMaterial=False).status_code == 400
    response = decide(client, task, view, confirmedRuleIds=ids)
    assert response.status_code == 200, response.text
    refreshed = state(client, task)
    assert refreshed['scriptApproval']['id'] == response.json()['data']['id']
    assert refreshed['scriptAcceptance']['passed'] and refreshed['stage'] == 'video'
    assert all(c['verdict'] == 'compliant' for c in refreshed['scriptAcceptance']['checks'])
    # The saved AI report remains unchanged by the brand's decision.
    assert all(f['verdict'] == 'needs_confirmation' for f in refreshed['scriptReview']['result']['report']['findings'])
    assert submit(client, task['id'], {'inputType': 'text', 'text': '新版本需独立审核'}).status_code == 201
    assert not state(client, task)['scriptApproval']
    assert decide(client, task, view, confirmedRuleIds=ids).status_code == 409


@pytest.mark.parametrize('defect', ['failed_rule', 'missing_check', 'incomplete_media', 'partial_report'])
def test_confirmations_cannot_bypass_failed_or_incomplete_reviews(client, monkeypatch, defect):
    task, rid, view, ids = pending_review(client, monkeypatch)
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute('SELECT * FROM workflow_script_reviews WHERE id=?', (rid,)).fetchone()
        result, snapshot = json.loads(row['result_json']), json.loads(row['snapshot_json'])
        if defect == 'failed_rule':
            result['report']['findings'][0]['verdict'] = 'non_compliant'
        elif defect == 'missing_check':
            result['report']['findings'] = []
        elif defect == 'incomplete_media':
            snapshot['multimodal'] = {'incomplete': True}
        else:
            result['partialReport'] = result['report']
        db.execute('UPDATE workflow_script_reviews SET result_json=?,snapshot_json=? WHERE id=?', (json.dumps(result), json.dumps(snapshot), rid))
    assert decide(client, task, view, confirmedRuleIds=ids).status_code == 409
    assert not state(client, task)['scriptApproval']
