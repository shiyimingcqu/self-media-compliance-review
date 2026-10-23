from app.core.database import connection
from app.modules.workflow import router as workflow
from test_separate_standards import project,payload
from test_script_versions import K


def test_creator_mandatory_gate_before_brand_review_and_new_version_invalidation(client,monkeypatch):
    p=project(client);body=payload(client)
    body['scriptRules']=[{'text':'脚本规则','criterion':'必须提及品牌；不得保证效果。','category':'脚本审核规则','importance':'must'}]
    task=client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':K()}).json()['data']
    task_id=task['id']
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'model','revision':0,'apiKey':'sk-test-local'})
    verdict='non_compliant';calls=[]
    def model(config,key,snapshot):
        calls.append(1);rid=snapshot['rules'][0]['id']
        checks=[] if verdict=='missing' else [{'rule_id':rid,'requirement':'不承诺保证有效','verdict':verdict,'quote':'','reason':'根据脚本内容核验'}]
        return {'report':{'summary':'模拟验收','mandatory_checks':checks,'findings':[{'rule_id':rid,'verdict':'needs_confirmation','quote':'','description':'建议开场更自然（非必须项）','suggestion':'可优化语气'}]}}
    monkeypatch.setattr(workflow,'call_script_model',model)
    def submit():
        r=client.post(f'/api/v1/tasks/{task_id}/scripts/submit-and-review',json={'inputType':'text','text':'品牌介绍与产品体验。','configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':K()})
        assert r.status_code==201,r.text
        return r.json()['data']
    def state():
        return client.get(f'/api/v1/tasks/{task_id}/workflow').json()['data']
    def decision(view):
        return client.post(f'/api/v1/tasks/{task_id}/workflow/decisions',json={'scope':'script','targetId':view['scriptReview']['id'],'contextKey':view['contextKey'],'decision':'approved','note':'品牌核对通过','reviewedMaterial':True},headers={'Idempotency-Key':K()})
    first=submit();view=state()
    assert first['autoReview']['status']=='succeeded'
    assert not view['scriptAcceptance']['passed'] and not view['brandReviewAllowed']
    assert client.get('/api/v1/workflow/review-queue').json()['data']==[]
    rejected=decision(view)
    assert rejected.status_code==409 and rejected.json()['error']['code']=='MANDATORY_CHECKS_NOT_PASSED'
    verdict='needs_confirmation';submit()
    assert not state()['brandReviewAllowed']
    assert client.get('/api/v1/workflow/review-queue').json()['data']==[]
    verdict='compliant';submit();view=state()
    assert view['scriptAcceptance']['passed'] and view['brandReviewAllowed']
    queue=client.get('/api/v1/workflow/review-queue').json()['data']
    assert len(queue)==1 and queue[0]['id']==task_id
    assert decision(view).status_code==200
    assert state()['videoAllowed']
    creator=client.get('/api/v1/creator/tasks').json()['data'][0]
    assert creator['brandApproved'] and creator['creator_name']==body['creatorName']
    verdict='missing';submit();view=state()
    assert not view['scriptAcceptance']['passed'] and view['scriptAcceptance']['status']=='incomplete'
    assert view['videoAllowed'] and not view['brandReviewAllowed']
    assert client.get('/api/v1/workflow/review-queue').json()['data']==[]
    assert decision(view).status_code==409
    assert len(calls)==4
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM workflow_decisions WHERE task_id=?',(task_id,)).fetchone()[0]==1
