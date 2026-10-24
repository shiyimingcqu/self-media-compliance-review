import json

import pytest

from app.core.database import connection
from app.modules.workflow import router as workflow
from test_numbered_multimodal_script import setup, upload, workbook, report, submit
from test_script_versions import K


@pytest.mark.parametrize('envelope', ['single', 'multiple', 'empty', 'nested', 'scalar', 'invalid_rule', 'invalid_schema', 'invalid_image'])
def test_array_report_normalization_keeps_full_validation(client, monkeypatch, envelope):
    task = setup(client); file_id = upload(client, workbook()); calls = []; originals = []
    def model(config, secret, snapshot):
        calls.append(1)
        value = report(snapshot)
        if envelope == 'invalid_rule': value['findings'][0]['rule_id'] = 'invented-rule'
        if envelope == 'invalid_schema': value['findings'][0]['description'] = {}
        if envelope == 'invalid_image': value['image_checks'][0]['image_id'] = 'invented-image'
        wrapped = {'multiple': [value, value], 'empty': [], 'nested': [[value]], 'scalar': ['text']}.get(envelope, [value])
        raw = json.dumps(wrapped, ensure_ascii=False); originals.append(raw)
        return {'report': wrapped, 'modelResponse': raw, 'finishReason': 'stop'}
    monkeypatch.setattr(workflow, 'call_script_model', model)
    review = submit(client, task, file_id).json()['data']['autoReview']
    assert review['result']['modelResponse'] == originals[0]
    assert len(calls) == 1
    if envelope == 'single':
        assert review['status'] == 'succeeded', review
        assert review['result']['normalization']['method'] == 'single_report_array_v1'
        assert not review['result'].get('recovery')
    else:
        assert review['status'] == 'failed'
        assert not review['acceptance']['passed']
        if envelope in ('multiple', 'empty', 'nested', 'scalar'):
            assert review['errorCode'] == 'OUTPUT_SCHEMA_INVALID'
            assert 'normalization' not in review['result']


def test_retained_single_report_array_can_be_revalidated_without_provider(client, monkeypatch):
    task = setup(client); file_id = upload(client, workbook())
    def model(config, secret, snapshot):
        value = report(snapshot)
        return {'report': value, 'modelResponse': json.dumps([value]), 'finishReason': 'stop'}
    monkeypatch.setattr(workflow, 'call_script_model', model)
    review = submit(client, task, file_id).json()['data']['autoReview']
    with connection(client.app.state.settings.database_path) as db:
        saved = db.execute('SELECT result_json FROM workflow_script_reviews WHERE id=?', (review['id'],)).fetchone()[0]
        db.execute("UPDATE workflow_script_reviews SET status='failed',error_code='OUTPUT_SCHEMA_INVALID' WHERE id=?", (review['id'],))
    def no_model(*args): pytest.fail('Saved response must never be resubmitted')
    monkeypatch.setattr(workflow, 'call_script_model', no_model)
    key = K(); path = f"/api/v1/workflow/script-reviews/{review['id']}/revalidate"
    first = client.post(path, headers={'Idempotency-Key': key})
    assert first.status_code == 200, first.text
    restored = first.json()['data']
    assert restored['status'] == 'succeeded'
    assert restored['result']['normalization']['providerResubmitted'] is False
    assert restored['result']['modelResponse'] == json.loads(saved)['modelResponse']
    assert client.post(path, headers={'Idempotency-Key': key}).json()['data'] == restored
