import uuid
import json

from app.core.database import connection
from app.modules.workflow import standards
from app.modules.workflow import router as workflow
from test_script_versions import task_with_rule, submit, K


def project(client):
    r=client.post('/api/v1/projects',json={'name':'独立标准项目','brandName':'测试品牌','platforms':['douyin']},headers={'Idempotency-Key':K()})
    assert r.status_code==201,r.text
    return r.json()['data']


def payload(client):
    template=client.get('/api/v1/workflow/standard-templates').json()['data'][0]
    return {'creatorName':'达人','platform':'douyin','campaignPhase':'general','scriptRules':template['scriptRules'],
            'videoRules':template['videoRules'],'scriptConfirmed':True,'videoConfirmed':True,'confirmationNote':'品牌分别确认'}


def test_publish_requires_two_confirmations_and_freezes_independent_sets(client):
    p=project(client);body=payload(client)
    bad={**body,'videoConfirmed':False}
    r=client.post(f"/api/v1/projects/{p['id']}/publish-task",json=bad,headers={'Idempotency-Key':K()})
    assert r.status_code==400,r.text
    assert client.get(f"/api/v1/projects/{p['id']}/tasks").json()['total']==0
    key=K()
    created=client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':key})
    assert created.status_code==201,created.text
    task=created.json()['data'];task_id=task['id']
    repeated=client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':key})
    assert repeated.json()['data']['id']==task_id
    view=client.get(f'/api/v1/tasks/{task_id}/workflow').json()['data']
    assert view['rulesReady'] and view['ruleVersionId'] is None
    assert view['standardSets']['script']['id']!=view['standardSets']['video']['id']
    script_ids={r['id'] for r in view['standardSets']['script']['rules']}
    video_ids={r['id'] for r in view['standardSets']['video']['rules']}
    assert script_ids.isdisjoint(video_ids)
    with connection(client.app.state.settings.database_path) as db:
        taskrow=workflow.authorize_task(db,task_id)
        assert {r['id'] for r in workflow.applicable_rules(db,taskrow,'script')}==script_ids
        assert {r['id'] for r in workflow.applicable_rules(db,taskrow,'video')}==video_ids
    detail=client.get(f'/api/v1/tasks/{task_id}').json()['data']
    assert detail['eligibility']['allowed']
    assert submit(client,task_id,{'inputType':'text','text':'品牌介绍与产品展示'}).status_code==201
    revision=client.get(f'/api/v1/tasks/{task_id}').json()['data']['revision']
    updated={k:v for k,v in body.items() if k not in ['creatorName','platform','campaignPhase']}
    updated['taskRevision']=revision
    updated['scriptRules'][0]['criterion']='品牌修改后的专属脚本口径'
    before=client.get(f'/api/v1/tasks/{task_id}/workflow').json()['data']
    r=client.post(f'/api/v1/tasks/{task_id}/standards/confirm',json=updated,headers={'Idempotency-Key':K()})
    assert r.status_code==200,r.text
    after=client.get(f'/api/v1/tasks/{task_id}/workflow').json()['data']
    assert before['contextKey']!=after['contextKey']
    assert after['standardSets']['script']['rules'][0]['criterion']=='品牌修改后的专属脚本口径'
    assert after['standardSets']['video']['rules'][0]['criterion']!=after['standardSets']['script']['rules'][0]['criterion']


def test_ai_recommendation_source_validation_idempotency_prompt_and_no_auto_publish(client,monkeypatch):
    p,task=task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source=db.execute('SELECT source_version_id FROM rule_versions WHERE id=?',(task['ruleVersionId'],)).fetchone()[0]
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://models.example.com/v1','model':'model1','revision':0,'apiKey':'sk-test-only'})
    calls=[]
    def model(config,key,template,blocks,uncovered):
        calls.append(template['key'])
        return {'report':{'rules':[{'text':'检查品牌名','criterion':'逐字核对脚本品牌名','category':'品牌','importance':'must',
            'source_block_id':blocks[0]['id'],'source_quote':blocks[0]['text']}],'warnings':[]},'providerRequestIds':['mock'],'usage':[]}
    monkeypatch.setattr(standards,'call_recommendation',model)
    body={'sourceVersionId':source,'scope':'script','configRevision':1,'allowRealCall':False}
    path=f"/api/v1/projects/{p['id']}/standard-recommendations"
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).status_code==400
    assert calls==[]
    body['allowRealCall']=True;key=K()
    r=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert r.status_code==200,r.text
    data=r.json()['data'];assert data['status']=='succeeded',data
    assert data['snapshot']['prompt']['key']=='brief-script-standards'
    assert data['result']['report']['rules'][0]['origin']=='ai'
    assert client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']['id']==data['id']
    assert calls==['brief-script-standards']
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM task_standard_sets WHERE task_id=?',(task['id'],)).fetchone()[0]==0
    def fabricated(*args):
        return {'report':{'rules':[{'text':'伪造要求','criterion':'检查','source_block_id':'fake','source_quote':'编造'}],'warnings':[]}}
    monkeypatch.setattr(standards,'call_recommendation',fabricated)
    failed=client.post(path,json={**body,'scope':'video'},headers={'Idempotency-Key':K()}).json()['data']
    assert failed['status']=='failed' and 'report' not in failed['result']
    prompts=client.get('/api/v1/workflow/prompts').json()['data']
    assert len(prompts)==13 and len({p['version'] for p in prompts})==13
    assert any(p['key']=='image-review' for p in prompts)
