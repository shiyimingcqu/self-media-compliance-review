import json
import uuid
import subprocess

import httpx
import imageio_ffmpeg
import pytest

from app.core.database import connection
from app.core.errors import ApiError
from app.modules.workflow import router as workflow
from app.modules.video_model.evidence import console_service as evidence
from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
from test_script_versions import task_with_rule, submit, K


def state(client, task):
    r=client.get(f"/api/v1/tasks/{task['id']}/workflow")
    assert r.status_code==200,r.text
    return r.json()['data']


def configure(client,task):
    r=client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'test-video-model','revision':0,'apiKey':'sk-local-test-only'})
    assert r.status_code==200,r.text
    template=client.get('/api/v1/workflow/standard-templates').json()['data'][0]
    revision=client.get('/api/v1/tasks/'+task['id']).json()['data']['revision']
    confirmed=client.post('/api/v1/tasks/'+task['id']+'/standards/confirm',json={
        'scriptRules':template['scriptRules'],'videoRules':template['videoRules'],'scriptConfirmed':True,
        'videoConfirmed':True,'confirmationNote':'品牌分别审核两套标准','taskRevision':revision},headers={'Idempotency-Key':K()})
    assert confirmed.status_code==200,confirmed.text


def analyze(client,task,view,key=None):
    return client.post(f"/api/v1/tasks/{task['id']}/workflow/script-reviews",json={
        'scriptVersionId':view['currentScript']['scriptVersionId'],'contextKey':view['contextKey'],
        'configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':key or K()})


def decide(client,task,view,scope='script',decision='approved',**changes):
    target=view['scriptReview']['id'] if scope=='script' else view['latestVideoRunId']
    return client.post(f"/api/v1/tasks/{task['id']}/workflow/decisions",json={
        'scope':scope,'targetId':target,'contextKey':view['contextKey'],'decision':decision,
        'note':'已核对完整材料与AI建议','reviewedMaterial':True,**changes},headers={'Idempotency-Key':K()})


def mock_script(config,key,snapshot):
    assert key=='sk-local-test-only'
    return {'report':{'summary':'模拟审核，品牌确认后拍摄','findings':[{'rule_id':r['id'],'verdict':'compliant',
        'quote':'','description':'脚本已安排相关内容','suggestion':'成片仍需确认'} for r in snapshot['rules']]},'providerRequestIds':['mock-script'],'usage':[]}


def test_workflow_full_script_video_human_and_new_script_invalidation(client,monkeypatch,tmp_path):
    from app.modules.video_model.evidence import asr
    monkeypatch.setattr(asr,'transcribe',lambda *args,**kwargs:asr.unavailable('固定本地测试',source_sha256=kwargs['source_sha256']))
    project,task=task_with_rule(client)
    configure(client,task)
    script='开场口播麦咖啡，展示产品包装，结尾推荐。'
    assert submit(client,task['id'],{'inputType':'text','text':script}).status_code==201
    view=state(client,task)
    assert view['stage']=='script' and view['videoAllowed']
    with connection(client.app.state.settings.database_path) as db:
        with pytest.raises(ApiError) as error:
            workflow.approved_script(db,task['id'])
        assert error.value.code=='SCRIPT_NOT_APPROVED'
    calls=[]
    def model(*args):
        calls.append(1)
        return mock_script(*args)
    monkeypatch.setattr(workflow,'call_script_model',model)
    key=K()
    first=analyze(client,task,view,key)
    assert first.status_code==200,first.text
    assert first.json()['data']['status']=='succeeded'
    assert analyze(client,task,view,key).json()['data']['id']==first.json()['data']['id']
    assert len(calls)==1
    view=state(client,task)
    queue=client.get('/api/v1/workflow/review-queue').json()['data']
    assert len(queue)==1 and queue[0]['scope']=='script'
    assert decide(client,task,view,reviewedMaterial=False).status_code==400
    assert state(client,task)['videoAllowed']
    assert decide(client,task,view).status_code==200
    view=state(client,task)
    assert view['videoAllowed'] and view['stage']=='video'
    source=tmp_path/'video.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','color=c=blue:s=160x120:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True,timeout=20)
    uploaded=client.post('/api/v1/files',files={'file':('video.mp4',source.read_bytes(),'video/mp4')},data={'purpose':'video'},headers={'Idempotency-Key':K()}).json()['data']
    run_id,frozen=evidence.prepare_run(client.app.state.settings,EvidenceRunInput(kind='video',config_revision=1,allow_real_call=True,task_id=task['id'],file_id=uploaded['id']),K())
    run=evidence.get_run(client.app.state.settings,run_id)
    assert run.snapshot['approvedScript']==script
    assert run.snapshot['scriptVersionId']==view['currentScript']['scriptVersionId']
    assert run.snapshot['scriptApprovalId']==view['scriptApproval']['id']
    assert all(r['id'].startswith('video-') for r in run.snapshot['rules'])
    assert run.snapshot['videoStandardSetId']==view['standardSets']['video']['id']
    assert run.snapshot['scriptStandardSetId']!=run.snapshot['videoStandardSetId']
    def handler(request):
        body=json.loads(request.content)
        assert script in body['messages'][1]['content'][1]['text']
        from test_localized_video import row,reply
        return httpx.Response(200,json=reply([row(r['id']) for r in frozen['rules']]))
    evidence.execute_run(client.app.state.settings,run_id,frozen,transport=httpx.MockTransport(handler))
    assert evidence.get_run(client.app.state.settings,run_id).status=='succeeded'
    view=state(client,task)
    assert view['videoReviewReady'] and view['stage']=='human'
    queue=client.get('/api/v1/workflow/review-queue').json()['data']
    assert len(queue)==1 and queue[0]['scope']=='video'
    assert decide(client,task,view,scope='video').status_code==200
    assert state(client,task)['stage']=='completed'
    assert client.get('/api/v1/workflow/review-queue').json()['data']==[]
    assert client.get(f"/api/v1/tasks/{task['id']}").json()['data']['stage']=='completed'
    assert submit(client,task['id'],{'inputType':'text','text':'第二版更改口播'}).status_code==201
    updated=state(client,task)
    assert updated['stage']=='script' and updated['videoAllowed'] and not updated['videoReviewReady']
    assert updated['finalDecision'] is None and len(updated['history'])==2
    assert decide(client,task,view,scope='video').status_code==409


def test_stale_script_review_and_fabricated_quotes_cannot_approve(client,monkeypatch):
    _,task=task_with_rule(client);configure(client,task)
    submit(client,task['id'],{'inputType':'text','text':'第一版脚本'})
    old=state(client,task)
    monkeypatch.setattr(workflow,'call_script_model',mock_script)
    assert analyze(client,task,old).json()['data']['status']=='succeeded'
    old=state(client,task)
    submit(client,task['id'],{'inputType':'text','text':'第二版脚本'})
    assert decide(client,task,old).status_code==409
    assert analyze(client,task,old).status_code==409
    def bad(*args):
        result=mock_script(*args)
        result['report']['findings'][0]['quote']='不存在的脚本原文'
        return result
    monkeypatch.setattr(workflow,'call_script_model',bad)
    view=state(client,task)
    r=analyze(client,task,view)
    assert r.json()['data']['status']=='succeeded'
    finding=r.json()['data']['result']['report']['findings'][0]
    assert finding['verdict']=='needs_confirmation'
    assert finding['evidence_status']=='unverified' and finding['quote']==''
    assert finding['unverified_quote']=='不存在的脚本原文'
    updated=state(client,task)
    assert decide(client,task,updated).status_code==409
    assert updated['videoAllowed']


def test_revalidate_endpoint_restores_format_failed_review_without_provider_call(client,monkeypatch):
    project,task=task_with_rule(client)
    configure(client,task)
    assert submit(client,task['id'],{'inputType':'text','text':'开场口播麦咖啡，展示产品包装，结尾推荐。'}).status_code==201
    view=state(client,task)
    def model(config,key,snapshot):
        value=mock_script(config,key,snapshot)
        return {**value,'modelResponse':json.dumps(value['report'])}
    monkeypatch.setattr(workflow,'call_script_model',model)
    first=analyze(client,task,view)
    assert first.status_code==200 and first.json()['data']['status']=='succeeded'
    review_id=first.json()['data']['id']
    with connection(client.app.state.settings.database_path) as db:
        row=db.execute('SELECT result_json FROM workflow_script_reviews WHERE id=?',(review_id,)).fetchone()
        value=json.loads(row[0]);value.pop('report')
        db.execute("UPDATE workflow_script_reviews SET status='failed',error_code='SCRIPT_REPORT_INVALID',result_json=? WHERE id=?",(json.dumps(value),review_id))
    def forbidden(*args):raise AssertionError('revalidation must not call the provider')
    monkeypatch.setattr(workflow,'call_script_model',forbidden)
    r=client.post(f'/api/v1/workflow/script-reviews/{review_id}/revalidate',headers={'Idempotency-Key':K()})
    assert r.status_code==200,r.text
    data=r.json()['data']
    assert data['status']=='succeeded' and data['result']['revalidation']['providerResubmitted'] is False
    key=r.request.headers['Idempotency-Key']
    again=client.post(f'/api/v1/workflow/script-reviews/{review_id}/revalidate',headers={'Idempotency-Key':key})
    assert again.status_code==200 and again.json()['data']['id']==review_id
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND action='saved_response_revalidated'",(review_id,)).fetchone()[0]==1
    other=client.post(f'/api/v1/workflow/script-reviews/{review_id}/revalidate',headers={'Idempotency-Key':K()})
    assert other.status_code==409 and other.json()['error']['code']=='REVALIDATION_NOT_AVAILABLE'


@pytest.mark.parametrize('submitted', [False, True])
def test_video_can_run_without_approved_script_and_missing_script_is_pending(client, monkeypatch, tmp_path, submitted):
    from test_project_standards import rules as project_payload
    from app.modules.workflow.video_layers import defaults
    project, task = task_with_rule(client)
    configure(client, task)
    body = project_payload(client); body['revision'] = 1
    product = {**defaults()[1], 'params': {'names': ['指定产品']}}
    body.update(videoRules=[product, {**defaults()[2], 'enabled': True}], videoReviewLevel='basic')
    confirmed = client.post(f"/api/v1/projects/{project['id']}/standards/confirm", json=body, headers={'Idempotency-Key': K()})
    assert confirmed.status_code == 200, confirmed.text
    if submitted:
        submit(client, task['id'], {'inputType': 'text', 'text': '尚未审核通过的脚本'})
    view = state(client, task)
    assert view['videoAllowed'] and view['scriptApproval'] is None
    source = tmp_path / 'unapproved.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x120:d=1', '-c:v', 'libx264', str(source)], check=True, timeout=20)
    asset = client.post('/api/v1/files', files={'file': ('unapproved.mp4', source.read_bytes(), 'video/mp4')}, data={'purpose': 'video'}, headers={'Idempotency-Key': K()}).json()['data']
    payload = EvidenceRunInput(kind='video', config_revision=1, allow_real_call=True, task_id=task['id'], file_id=asset['id'])
    run_id, trusted = evidence.prepare_run(client.app.state.settings, payload, K())
    snapshot = evidence.get_run(client.app.state.settings, run_id).snapshot
    assert snapshot['scriptApprovalId'] is None and snapshot['approvedScript'] == ''
    assert snapshot['scriptStatus'] == ('pending_approval' if submitted else 'not_submitted')
    assert snapshot['unconfiguredRules'][0]['ruleNumber'] == 2
    assert len(trusted['rules']) == 1
    def handler(request):
        from test_localized_video import row,reply
        return httpx.Response(200,json=reply([row(trusted['rules'][0]['id'])]))
    evidence.execute_run(client.app.state.settings, run_id, trusted, transport=httpx.MockTransport(handler))
    assert evidence.get_run(client.app.state.settings, run_id).status == 'succeeded'
    view = state(client, task)
    assert view['videoReviewReady'] and view['latestVideoRunId'] == run_id
    assert view['stage'] == 'human' and view['videoComponentAcceptance']['configurationRequired']
    assert decide(client, task, view, scope='video').status_code == 409
    assert decide(client, task, view, scope='video', decision='returned').status_code == 200
    submit(client, task['id'], {'inputType': 'text', 'text': '新的脚本版本'})
    assert state(client, task)['latestVideoRunId'] is None
    assert state(client, task)['videoAllowed']
    # The temporary policy has a single rollback switch; approval semantics stay honest.
    monkeypatch.setattr(workflow, 'REQUIRE_SCRIPT_APPROVAL_FOR_VIDEO', True)
    assert not state(client, task)['videoAllowed']
