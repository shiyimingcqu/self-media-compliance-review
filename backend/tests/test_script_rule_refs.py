import copy
import json

import httpx
import pytest

from app.core.database import connection
from app.modules.workflow import router as workflow
from app.modules.workflow.script_output import output_contract
from app.modules.workflow.script_rule_refs import (freeze_references, normalize_references,
                                                   recover_legacy_reference)
from test_numbered_multimodal_script import setup, upload, workbook, report, submit
from test_script_versions import K


def frozen():
    return {'rules':[{'id':'script-'+char*32,'ruleNumber':n,'importance':'must','text':'核对产品'}
                     for n,char in enumerate(('a','b'),1)],'paragraphMode':False,'script':'产品A',
            'multimodal':{'images':[{'id':'image-real','ref':'i1'}]},
            'quoteSources':[{'id':'q1','text':'产品A'}]}


def wire_report():
    return {'summary':'固定结果','findings':[
        {'rule_id':ref,'verdict':'compliant','description':'引用R1只是说明文字，不能替换这段话',
         'quote_refs':['q1'],'image_ids':['i1']} for ref in ('R1','R2')],
        'image_checks':[{'image_id':'i1','description':'固定图片说明','rule_ids':['R1','R2']}]}


def test_short_refs_map_findings_mandatory_and_image_links_without_changing_observation():
    snapshot=frozen();freeze_references(snapshot);value=wire_report();original=copy.deepcopy(value)
    value['mandatory_checks']=[{'rule_id':'R1','verdict':'compliant','reason':'已核对'}]
    saved={};normalized=normalize_references(value,snapshot,saved)
    result=workflow.validate_script_report(normalized,snapshot)
    assert [f['rule_id'] for f in result['findings']]==[r['id'] for r in snapshot['rules']]
    assert [f['rule_number'] for f in result['findings']]==[1,2]
    assert result['image_checks'][0]['rule_ids']==[r['id'] for r in snapshot['rules']]
    assert result['mandatory_checks'][0]['rule_id']==snapshot['rules'][0]['id']
    assert result['findings'][0]['description']==original['findings'][0]['description']
    assert value['findings']==original['findings']
    assert saved['ruleReferenceMapping']['repaired'] is False


@pytest.mark.parametrize('mode',['json_schema','tool'])
def test_short_contract_has_only_r_enums_and_server_owned_numbers(mode):
    snapshot=frozen();freeze_references(snapshot)
    contract=output_contract(workflow.ScriptReport.model_json_schema(),mode,snapshot)
    schema=contract['response_format']['json_schema']['schema'] if mode=='json_schema' else contract['tools'][0]['function']['parameters']
    for name in ('ScriptFinding','MandatoryCheck'):
        properties=schema['$defs'][name]['properties']
        assert properties['rule_id']['enum']==['R1','R2']
        assert 'rule_number' not in properties
    assert schema['$defs']['ScriptImageCheck']['properties']['rule_ids']['items']['enum']==['R1','R2']
    assert schema['properties']['findings']['minItems']==schema['properties']['findings']['maxItems']==2
    assert all(rule['id'] not in json.dumps(schema) for rule in snapshot['rules'])


def test_actual_json_object_request_never_sends_real_rule_ids(monkeypatch):
    snapshot=frozen();freeze_references(snapshot)
    snapshot.update(prompt={'content':'固定提示词'},reviewRulesText='第1条核对产品',
                    paramsCheck=[{'ruleId':snapshot['rules'][0]['id'],'present':True}])
    actual=httpx.Client;calls=[]
    def provider(request):
        body=json.loads(request.content);calls.append(body)
        text=json.dumps(body,ensure_ascii=False)
        assert all(rule['id'] not in text for rule in snapshot['rules'])
        material=json.loads(body['messages'][1]['content'][0]['text'])
        assert [r['id'] for r in material['rules']]==['R1','R2']
        assert material['paramsCheck'][0]['ruleId']=='R1'
        assert body['response_format']=={'type':'json_object'}
        return httpx.Response(200,json={'id':'fixed','choices':[{'finish_reason':'stop','message':{'content':json.dumps(wire_report())}}]})
    monkeypatch.setattr(workflow.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    value=workflow.call_script_model({'endpoint':'https://example.com/v1','model':'qwen3.8-omni-flash'},'fake',snapshot)
    assert len(calls)==1 and value['report']['findings'][0]['rule_id']=='R1'
    assert workflow.validate_script_report(value['report'],snapshot)['findings'][0]['rule_id']==snapshot['rules'][0]['id']


@pytest.mark.parametrize('defect,types,validated',[
    ('missing',['missing'],1),('duplicate',['missing','duplicate'],0),
    ('unknown',['missing','unknown'],1),('number',['number_mismatch'],1)])
def test_reference_defects_have_separate_diagnostics_and_safe_partial_results(client,monkeypatch,defect,types,validated):
    task=setup(client);file_id=upload(client,workbook());actual=httpx.Client;calls=[];raws=[]
    def provider(request):
        calls.append(1);body=json.loads(request.content);material=json.loads(body['messages'][1]['content'][0]['text'])
        value=report(material)
        for finding in value['findings']:finding.pop('rule_number',None)
        if defect=='missing':value['findings'].pop()
        elif defect=='duplicate':
            value['findings'][1]['rule_id']='R1';value['findings'][1]['verdict']='non_compliant'
        elif defect=='unknown':value['findings'][1]['rule_id']='R99'
        elif defect=='number':value['findings'][1]['rule_number']=1
        raw=json.dumps(value);raws.append(raw)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':raw}}]})
    monkeypatch.setattr(workflow.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    key=K();review=submit(client,task,file_id,key).json()['data']['autoReview']
    assert review['errorCode']=='RULE_REF_INVALID' and review['status']=='failed'
    result=review['result'];assert result['diagnostics']['issueTypes']==types
    assert result['coverage']['validatedRules']==validated
    assert len(result['partialReport']['findings'])==2
    assert sum(f['verdict']=='pending' for f in result['partialReport']['findings'])==2-validated
    assert result['partialReport']['image_checks']  # 有效图片说明也保留
    assert result['modelResponse']==raws[0]
    assert not review['acceptance']['passed']
    assert submit(client,task,file_id,key).json()['data']['autoReview']['id']==review['id']
    assert len(calls)==1


def legacy_value(snapshot):
    value=wire_report()
    for number,finding in enumerate(value['findings'],1):
        finding['rule_id']=snapshot['rules'][number-1]['id'];finding['rule_number']=number
    value['image_checks'][0]['rule_ids']=[r['id'] for r in snapshot['rules']]
    return value


def test_unique_legacy_malformed_id_recovers_by_number_not_fuzzy_matching():
    snapshot=frozen();value=legacy_value(snapshot);target=snapshot['rules'][1]['id'];bad=target+'5'
    value['findings'][1]['rule_id']=bad;value['image_checks'][0]['rule_ids'][1]=bad
    original=copy.deepcopy(value);fixed,recovery=recover_legacy_reference(value,snapshot)
    assert fixed['findings'][1]['rule_id']==target and fixed['image_checks'][0]['rule_ids'][1]==target
    assert recovery['requiresManualReview'] and recovery['bindings'][0]['ruleNumber']==2
    assert workflow.validate_script_report(fixed,snapshot)
    assert value==original


@pytest.mark.parametrize('defect',['foreign_uuid','wrong_number','duplicate','two_unknown','new_protocol','paragraph'])
def test_ambiguous_or_conflicting_legacy_references_are_never_guessed(defect):
    snapshot=frozen();value=legacy_value(snapshot);target=snapshot['rules'][1]['id']
    value['findings'][1]['rule_id']=target+'5'
    if defect=='foreign_uuid':value['findings'][1]['rule_id']='script-'+'c'*32
    elif defect=='wrong_number':value['findings'][1]['rule_number']=1
    elif defect=='duplicate':value['findings'][1]['rule_id']=value['findings'][0]['rule_id']
    elif defect=='two_unknown':value['findings'][0]['rule_id']=snapshot['rules'][0]['id']+'5'
    elif defect=='new_protocol':freeze_references(snapshot)
    elif defect=='paragraph':snapshot['paragraphMode']=True
    original=copy.deepcopy(value);fixed,recovery=recover_legacy_reference(value,snapshot)
    assert recovery is None and fixed==original


def test_local_legacy_recovery_keeps_original_reply_and_never_grants_pass(client,monkeypatch):
    task=setup(client);file_id=upload(client,workbook());calls=[]
    def model(config,secret,snapshot):
        calls.append(1);value=report(snapshot);value['findings'][1]['rule_id']+='5'
        return {'report':value,'modelResponse':json.dumps(value)}
    monkeypatch.setattr(workflow,'call_script_model',model)
    review=submit(client,task,file_id).json()['data']['autoReview']
    assert review['errorCode']=='RULE_REF_INVALID'
    # 历史快照没有新协议；其原始回复和编号均保留。
    with connection(client.app.state.settings.database_path) as db:
        row=db.execute('SELECT snapshot_json FROM workflow_script_reviews WHERE id=?',(review['id'],)).fetchone()
        snapshot=json.loads(row[0]);snapshot.pop('ruleReferenceVersion');snapshot.pop('ruleReferenceMap')
        db.execute('UPDATE workflow_script_reviews SET snapshot_json=? WHERE id=?',(json.dumps(snapshot),review['id']))
    def forbidden(*args,**kwargs):raise AssertionError('恢复不允许重调模型')
    monkeypatch.setattr(workflow,'call_script_model',forbidden)
    url=f"/api/v1/workflow/script-reviews/{review['id']}/revalidate";key=K()
    response=client.post(url,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    fixed=response.json()['data'];result=fixed['result']
    assert fixed['status']=='failed' and fixed['errorCode']=='NEEDS_MANUAL_REVIEW'
    assert not fixed['acceptance']['passed']
    assert result['modelResponse']==review['result']['modelResponse']
    assert result['recovery']['method']=='unique_missing_rule_number_v1'
    assert result['coverage']['validatedRules']==2 and not result.get('partialReport')
    assert result['revalidation']['previousDiagnostics']['issueTypes']==['missing','unknown']
    assert client.post(url,headers={'Idempotency-Key':key}).json()['data']==fixed
    assert calls==[1]
