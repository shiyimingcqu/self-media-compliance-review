from app.core.database import connection
from app.modules.workflow import router as workflow
from app.modules.workflow import standards
from test_separate_standards import project, payload
from test_script_versions import task_with_rule, K


def test_script_submit_automatically_reviews_paragraph_and_replay_never_bills_twice(client,monkeypatch):
    p=project(client);body=payload(client)
    body['scriptRules']=[{'text':'品牌脚本审核详细规则','criterion':'品牌必须准确；不得作无依据的效果承诺。','category':'脚本审核规则','importance':'must'}]
    task=client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':K()}).json()['data']
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'model','revision':0,'apiKey':'sk-local-test'})
    calls=[]
    def model(config,key,snapshot):
        calls.append(1)
        assert snapshot['paragraphMode']
        assert '第1条' in snapshot['reviewRulesText'] and '品牌必须准确；不得作无依据的效果承诺。' in snapshot['reviewRulesText']
        assert snapshot['prompt']['key']=='script-review'
        rule=snapshot['rules'][0]['id']
        return {'report':{'summary':'有两处问题，请修改','findings':[
            {'rule_id':rule,'verdict':'needs_confirmation','quote':'品牌A','description':'核实品牌名','suggestion':'使用品牌全称'},
            {'rule_id':rule,'verdict':'non_compliant','quote':'保证有效','description':'承诺缺少依据','suggestion':'删除保证表述'}]},'providerRequestIds':['mock-script'],'usage':[]}
    monkeypatch.setattr(workflow,'call_script_model',model)
    path=f"/api/v1/tasks/{task['id']}/scripts/submit-and-review"
    input={'inputType':'text','text':'品牌A，保证有效。','configRevision':1,'allowRealCall':False}
    assert client.post(path,json=input,headers={'Idempotency-Key':K()}).status_code==400
    assert calls==[]
    input['allowRealCall']=True;key=K()
    first=client.post(path,json=input,headers={'Idempotency-Key':key})
    assert first.status_code==201,first.text
    data=first.json()['data']
    assert data['script']['ordinal']==1
    assert data['autoReview']['status']=='succeeded'
    assert len(data['autoReview']['result']['report']['findings'])==2
    replay=client.post(path,json=input,headers={'Idempotency-Key':key})
    assert replay.json()['data']['autoReview']['id']==data['autoReview']['id']
    assert len(calls)==1
    # A later model configuration/current script cannot turn a transport retry into a new charge.
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'model2','revision':1})
    client.post(f"/api/v1/tasks/{task['id']}/scripts",json={'inputType':'text','text':'新脚本'},headers={'Idempotency-Key':K()})
    again=client.post(path,json=input,headers={'Idempotency-Key':key})
    assert again.json()['data']['script']['ordinal']==1
    assert again.json()['data']['autoReview']['id']==data['autoReview']['id']
    assert len(calls)==1
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM script_versions WHERE task_id=?',(task['id'],)).fetchone()[0]==2


def test_brief_generates_one_editable_rules_paragraph(client,monkeypatch):
    p,task=task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source=db.execute('SELECT source_version_id FROM rule_versions WHERE id=?',(task['ruleVersionId'],)).fetchone()[0]
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://models.example.com/v1','model':'model','revision':0,'apiKey':'sk-local-test'})
    def model(config,key,template,blocks,uncovered):
        assert template['key']=='brief-script-rules-text'
        return {'report':{'rulesText':'检查脚本是否安排口播品牌名，缺失时指出并给出补充建议。','warnings':[]},'providerRequestIds':['mock-brief'],'usage':[]}
    monkeypatch.setattr(standards,'call_recommendation',model)
    r=client.post(f"/api/v1/projects/{p['id']}/standard-recommendations",json={'sourceVersionId':source,'scope':'script','configRevision':1,'allowRealCall':True,'outputFormat':'paragraph'},headers={'Idempotency-Key':K()})
    assert r.status_code==200,r.text
    assert r.json()['data']['status']=='succeeded'
    assert r.json()['data']['result']['report']['rulesText'].startswith('检查脚本')
    default=client.get('/api/v1/workflow/script-rules/defaults').json()['data']
    assert default['rulesText'] and default['systemPrompt']['key']=='script-review'
