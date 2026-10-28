import json

import httpx
import pytest

from app.core.database import connection
from app.modules.workflow import router as workflow
from app.modules.workflow.script_output import OutputError, output_contract, parse_output, request_options
from test_numbered_multimodal_script import setup, upload, workbook, report, submit
from test_script_versions import K


@pytest.mark.parametrize('defect,expected', [('unrelated', 'succeeded'), ('missing', 'failed'), ('unknown_rule', 'failed'), ('unknown_image', 'failed')])
def test_reference_image_may_have_no_related_rule_but_must_still_be_reviewed(client, monkeypatch, defect, expected):
    task = setup(client); file_id = upload(client, workbook()); calls = []
    def model(config, secret, snapshot):
        calls.append(1)
        value = report(snapshot)
        value['image_checks'][0].update(rule_ids=[], description='此图仅为剧情风格参考，没有相关冻结规则。')
        for finding in value['findings']: finding['image_ids'] = []
        if defect == 'missing': value['image_checks'] = []
        elif defect == 'unknown_rule': value['image_checks'][0]['rule_ids'] = ['invented-rule']
        elif defect == 'unknown_image': value['image_checks'][0]['image_id'] = 'invented-image'
        return {'report': value, 'modelResponse': json.dumps(value, ensure_ascii=False)}
    monkeypatch.setattr(workflow, 'call_script_model', model)
    response = submit(client, task, file_id)
    assert response.status_code == 201, response.text
    review = response.json()['data']['autoReview']
    assert review['status'] == expected, review
    if defect == 'unrelated':
        assert review['result']['report']['image_checks'][0]['rule_ids'] == []
        assert len(review['result']['report']['findings']) == 2
    else:
        assert review['errorCode'] == ('RULE_REF_INVALID' if defect=='unknown_rule' else 'EVIDENCE_MISSING')
        assert not review['acceptance']['passed']
    assert calls == [1]


@pytest.mark.parametrize('defect,code', [
    ('json','OUTPUT_INVALID_JSON'), ('empty','OUTPUT_EMPTY'),
    ('truncated','OUTPUT_TRUNCATED'), ('schema','OUTPUT_SCHEMA_INVALID'),
    ('rule','RULE_REF_INVALID'), ('image','EVIDENCE_MISSING'),
    ('repair','OUTPUT_INVALID_JSON'), ('quote_ref','EVIDENCE_MISSING'),
])
def test_output_failures_preserve_evidence_and_never_retry_or_pass(client,monkeypatch,defect,code):
    task=setup(client);file_id=upload(client,workbook());calls=[];original=[]
    actual_client=httpx.Client
    validate=workflow.validate_script_report

    def checked(value,snapshot,**kw):
        # A complete raw response has been committed before validation starts.
        with connection(client.app.state.settings.database_path) as db:
            row=db.execute('SELECT result_json FROM workflow_script_reviews WHERE task_id=? ORDER BY rowid DESC LIMIT 1',(task,)).fetchone()
        saved=json.loads(row[0])
        assert saved['modelResponse']==original[0]
        assert saved['rawProviderResponse'] and saved['promptHash']
        assert saved['attemptCount']==1
        return validate(value,snapshot,**kw)

    def provider(request):
        calls.append(1);body=json.loads(request.content)
        assert body['response_format']=={'type':'json_object'}
        assert body['max_tokens']==12000
        snapshot=json.loads(body['messages'][1]['content'][0]['text'])
        value=report(snapshot)
        if defect=='schema':value['findings'][0]['description']={}
        if defect=='rule':value['findings'].pop()
        if defect=='image':value['findings'][0]['image_ids']=['fake']
        if defect=='quote_ref':value['findings'][0]['quote_refs']=['invented-source']
        if defect=='repair':value['findings'][0]['description']='或"22g大粉量萃取"等关键参数'
        raw=json.dumps(value,ensure_ascii=False)
        if defect=='repair':raw=raw.replace('\\"22g大粉量萃取\\"','"22g大粉量萃取"')
        if defect=='json':raw='{broken'
        if defect=='empty':raw=''
        original.append(raw)
        return httpx.Response(200,json={'id':'test-request-id','model':'model-version-test',
            'choices':[{'finish_reason':'length' if defect=='truncated' else 'stop','message':{'content':raw}}]})

    monkeypatch.setattr(workflow.httpx,'Client',lambda **kw:actual_client(transport=httpx.MockTransport(provider),**kw))
    monkeypatch.setattr(workflow,'validate_script_report',checked)
    key=K();review=submit(client,task,file_id,key).json()['data']['autoReview']
    assert review['status']=='failed' and review['errorCode']==code,review
    assert not review['acceptance']['passed']
    result=review['result']
    assert result['modelResponse']==original[0]
    assert result['returnedModel']=='model-version-test' and result['providerRequestIds']==['test-request-id']
    assert submit(client,task,file_id,key).json()['data']['autoReview']['id']==review['id']
    assert len(calls)==1
    if defect=='json':
        assert result['diagnostics']['offset']==1
        assert result['diagnostics']['context']=='{broken'
        assert result['diagnostics']['errorType']=='OUTPUT_INVALID_JSON'
    if defect=='schema':
        assert result['diagnostics']['schemaErrors'][0]['path']=='findings.0.description'
        assert result['coverage']['validatedRules']==1
        assert any(f['verdict']=='pending' for f in result['partialReport']['findings'])
        assert result['parsedOutput']['findings'][0]['description']=={}
    if defect=='repair':
        assert result['recovery']['repaired'] and result['recovery']['requiresManualReview']
        assert result['recovery']['providerResubmitted'] is False
        assert result['report']['findings'][0]['description']=='或"22g大粉量萃取"等关键参数'
        assert client.get('/api/v1/workflow/review-queue').json()['data']==[]
    if defect=='truncated':
        assert not result.get('recovery')
        assert result['diagnostics']['finishReason']=='length'


@pytest.mark.parametrize('mode',['json_schema','tool'])
def test_structured_contract_and_reference_only_output(client,monkeypatch,mode):
    monkeypatch.setenv('REVIEW_SCRIPT_OUTPUT_MODE',mode)
    task=setup(client);file_id=upload(client,workbook());actual_client=httpx.Client
    def provider(request):
        body=json.loads(request.content)
        schema=(body['response_format']['json_schema']['schema'] if mode=='json_schema' else body['tools'][0]['function']['parameters'])
        fields=schema['$defs']['ScriptFinding']['properties']
        assert 'quote' not in fields and 'quote_segments' not in fields
        assert set(fields)==set(schema['$defs']['ScriptFinding']['required'])
        assert 'enum' in fields['verdict']
        snapshot=json.loads(body['messages'][1]['content'][0]['text'])
        value=report(snapshot)
        value['findings'][0]['quote_refs']=[snapshot['quoteSources'][0]['id']]
        for f in value['findings']:f.pop('quote',None)
        message={'content':json.dumps(value)}
        if mode=='tool':
            assert body['tool_choice']['function']['name']=='submit_script_report'
            message={'content':None,'tool_calls':[{'function':{'name':'submit_script_report','arguments':json.dumps(value)}}]}
        return httpx.Response(200,json={'choices':[{'message':message,'finish_reason':'tool_calls' if mode=='tool' else 'stop'}]})
    monkeypatch.setattr(workflow.httpx,'Client',lambda **kw:actual_client(transport=httpx.MockTransport(provider),**kw))
    review=submit(client,task,file_id).json()['data']['autoReview']
    assert review['status']=='succeeded',review
    assert review['result']['report']['findings'][0]['quote']=='品牌A产品展示'


def test_native_modes_are_selected_without_paid_capability_probes(monkeypatch):
    monkeypatch.delenv('REVIEW_SCRIPT_OUTPUT_MODE',raising=False)
    assert request_options('qwen3.8-omni-flash')['outputMode']=='json_object'
    assert request_options('qwen3.8-flash')['outputMode']=='json_schema'
    monkeypatch.setenv('REVIEW_SCRIPT_OUTPUT_MODE','tool')
    assert request_options('custom-model')['outputMode']=='tool'


def test_truncation_wins_even_if_json_looks_complete():
    with pytest.raises(OutputError) as error:parse_output('{}','length')
    assert error.value.code=='OUTPUT_TRUNCATED'


def test_local_repair_keeps_original_and_blocks_approval(client,monkeypatch):
    task=setup(client);file_id=upload(client,workbook())
    def model(c,k,s):
        value=report(s);value['findings'][0]['description']='或"22g大粉量萃取"等关键参数'
        raw=json.dumps(value,ensure_ascii=False).replace('\\"22g大粉量萃取\\"','"22g大粉量萃取"')
        return {'modelResponse':raw,'outputError':{'code':'OUTPUT_INVALID_JSON','message':'invalid JSON'}}
    monkeypatch.setattr(workflow,'call_script_model',model)
    review=submit(client,task,file_id).json()['data']['autoReview']
    monkeypatch.setattr(workflow,'call_script_model',lambda *a:pytest.fail('No paid recovery calls'))
    key=K();path=f"/api/v1/workflow/script-reviews/{review['id']}/revalidate"
    recovered=client.post(path,headers={'Idempotency-Key':key})
    assert recovered.status_code==200,recovered.text
    value=recovered.json()['data']
    assert value['errorCode']=='NEEDS_MANUAL_REVIEW' and not value['acceptance']['passed']
    assert value['result']['modelResponse']==review['result']['modelResponse']
    assert client.post(path,headers={'Idempotency-Key':key}).json()['data']==value
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='saved_response_revalidated'",(review['id'],)).fetchone()[0]==1
