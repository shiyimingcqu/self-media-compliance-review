import json
import uuid

import httpx
import pytest

from app.core.database import connection
from app.modules.video_model.evidence import console_service as service

KEY = "sk-test-not-a-real-provider-key"
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def save(client, **changes):
    return client.put('/api/v1/settings/models', json={"endpoint": ENDPOINT, "model": "qwen3.8-omni-flash",
        "revision": 0, "apiKey": KEY, **changes})


def test_key_encrypted_not_echoed_retain_clear_and_revision(client):
    initial = client.get('/api/v1/settings/models').json()['data']
    assert not initial['hasKey'] and initial['revision'] == 0
    response = save(client)
    assert response.status_code == 200
    assert KEY not in response.text
    assert response.json()['data']['hasKey']
    with connection(client.app.state.settings.database_path) as db:
        encrypted = db.execute('SELECT encrypted_key FROM evidence_model_settings').fetchone()[0]
        assert encrypted and KEY not in encrypted
    assert save(client).status_code == 409
    retained = save(client, revision=1, apiKey=None)
    assert retained.json()['data']['hasKey']
    cleared = client.put('/api/v1/settings/models', json={'endpoint': ENDPOINT, 'revision': 2, 'clearKey': True})
    assert not cleared.json()['data']['hasKey']


@pytest.mark.parametrize('endpoint', ['http://127.0.0.1', 'https://127.0.0.1/compatible-mode/v1',
                                     ENDPOINT + '?key=secret', 'https://user:password@dashscope.aliyuncs.com/compatible-mode/v1'])
def test_disallowed_endpoints_and_external_origin(client, endpoint):
    assert save(client, endpoint=endpoint).status_code == 400
    assert client.get('/api/v1/settings/models', headers={'Origin': 'https://evil.example'}).status_code == 403


def test_explicit_confirmation_idempotency_and_mock_connection(client, monkeypatch):
    save(client)
    invocations = []
    def handler(request):
        invocations.append(request)
        assert request.headers['authorization'] == 'Bearer ' + KEY
        return httpx.Response(200, json={'id': 'provider-test-1', 'usage': {'total_tokens': 2},
                                       'choices': [{'message': {'content': 'OK'}}]})
    original = service.execute_run
    monkeypatch.setattr(service, 'execute_run', lambda settings, identity, frozen: original(settings, identity, frozen, transport=httpx.MockTransport(handler)))
    payload = {'kind': 'connection', 'configRevision': 1, 'allowRealCall': False}
    headers = {'Idempotency-Key': uuid.uuid4().hex}
    assert client.post('/api/v1/evidence-runs', json=payload, headers=headers).status_code == 400
    assert invocations == []
    payload['allowRealCall'] = True
    first = client.post('/api/v1/evidence-runs', json=payload, headers=headers)
    assert first.status_code == 202, first.text
    identity = first.json()['data']['id']
    # 同步等到本地工作线程结束（无网络），再验证端点回放不再提交。
    client.app.state.evidence_executor.submit(lambda: None).result(timeout=10)
    second = client.post('/api/v1/evidence-runs', json=payload, headers=headers)
    assert second.json()['data']['id'] == identity
    detail = client.get('/api/v1/evidence-runs/' + identity).json()['data']
    assert detail['status'] == 'succeeded'
    assert detail['result']['providerRequestIds'] == ['provider-test-1']
    assert not detail['affectsWorkflow']
    assert len(invocations) == 1
    assert KEY not in json.dumps(detail)


def test_unknown_timeout_no_retry_and_restart_no_resubmit(client, monkeypatch):
    save(client)
    attempts = []
    def handler(request):
        attempts.append(1)
        raise httpx.ReadTimeout('test', request=request)
    original = service.execute_run
    monkeypatch.setattr(service, 'execute_run', lambda s, i, f: original(s, i, f, transport=httpx.MockTransport(handler)))
    r = client.post('/api/v1/evidence-runs', json={'kind':'connection','configRevision':1,'allowRealCall':True}, headers={'Idempotency-Key':uuid.uuid4().hex})
    client.app.state.evidence_executor.submit(lambda: None).result(timeout=10)
    data = client.get('/api/v1/evidence-runs/' + r.json()['data']['id']).json()['data']
    assert data['status'] == 'interrupted'
    assert data['errorCode'] == 'PROVIDER_SUBMISSION_UNKNOWN'
    assert len(attempts) == 1


def test_no_key_does_not_enqueue(client):
    r = client.post('/api/v1/evidence-runs', json={'kind':'connection','configRevision':1,'allowRealCall':True}, headers={'Idempotency-Key':uuid.uuid4().hex})
    assert r.status_code == 409
    assert r.json()['error']['code'] == 'MODEL_KEY_MISSING'
    assert client.get('/api/v1/evidence-runs').json()['data'] == []


def test_user_supplied_compatible_endpoint(client):
    endpoint = 'https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1'
    r = save(client, endpoint=endpoint)
    assert r.status_code == 200
    assert r.json()['data']['endpoint'] == endpoint
    assert client.get('/api/v1/evidence-runs').json()['data'] == []


def test_actual_video_upload_localized_refinement_and_download(client, monkeypatch, tmp_path):
    import subprocess
    import imageio_ffmpeg
    from app.modules.video_model.evidence import asr
    monkeypatch.setattr(asr,'transcribe',lambda *args,**kwargs:asr.unavailable('固定本地测试',source_sha256=kwargs['source_sha256']))
    video = tmp_path / 'input.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=160x120:d=2', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                    '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)], check=True, timeout=20)
    saved = save(client)
    assert saved.status_code == 200
    with video.open('rb') as stream:
        upload = client.post('/api/v1/files', files={'file': ('input.mp4', stream, 'video/mp4')},
                             data={'purpose': 'video'}, headers={'Idempotency-Key': uuid.uuid4().hex})
    assert upload.status_code == 201, upload.text
    attempts = []
    def handler(request):
        body = json.loads(request.content)
        attempts.append(body)
        # 视频审核同样要显式钉温度，否则同一视频同一规则多次结果会漂移。
        assert body['temperature'] == 0
        assert body['messages'][1]['content'][0]['video_url']['url'].startswith('data:video/mp4;base64,')
        from test_localized_video import reply, row, window
        stage='localization' if len(attempts)==1 else 'refinement'
        assert body['tool_choice']['function']['name']=='submit_'+stage
        assert len(body['messages'])==2 and body['parallel_tool_calls'] is False
        if len(attempts)==2:
            active=service.list_runs(client.app.state.settings,None)[0]
            assert active.result['pipeline']['stage']=='refining'
            assert active.result['pipeline']['localization']['findings']
            assert len(active.result['modelResponses'])==1
        message=reply([row('capability-test-rule','non_compliant',[window(.3,.7)])],stage)['choices'][0]['message']
        return httpx.Response(200, json={'id': f'provider-{len(attempts)}', 'usage': {'total_tokens': 10}, 'choices':[{'message': message}]})
    original = service.execute_run
    monkeypatch.setattr(service, 'execute_run', lambda s, i, f: original(s, i, f, transport=httpx.MockTransport(handler)))
    start = client.post('/api/v1/evidence-runs', json={'kind':'video','fileId':upload.json()['data']['id'],
                        'configRevision':1,'allowRealCall':True}, headers={'Idempotency-Key':uuid.uuid4().hex})
    assert start.status_code == 202, start.text
    identity = start.json()['data']['id']
    client.app.state.evidence_executor.submit(lambda: None).result(timeout=30)
    data = client.get('/api/v1/evidence-runs/' + identity).json()['data']
    assert data['status'] == 'succeeded', data
    assert data['result']['conclusion'] == 'incomplete'
    assert data['result']['media']['hasAudio']
    # 本地 ASR 默认关闭：口播交给模型听原声 + 画面字幕，不再生成转写证据
    assert '本地 ASR 已关闭' in (data['result'].get('audioTrack') or {}).get('reason', '')
    assert len(data['result']['artifacts']) == 1 and len(attempts) == 2
    assert data['snapshot']['pipelineVersion']=='localized_refinement_v1'
    assert len(data['result']['stageCalls'])==2 and len(data['result']['modelResponses'])==2
    assert data['result']['pipeline']['stage']=='completed'
    assert data['result']['report']['findings'][0]['verdict']=='non_compliant'
    for artifact in data['result']['artifacts']:
        response = client.get(f"/api/v1/evidence-runs/{identity}/artifacts/{artifact['id']}")
        assert response.status_code == 200 and len(response.content) > 100
    assert client.get(f'/api/v1/evidence-runs/{identity}/artifacts/not-real').status_code == 404


def test_custom_model_and_legacy_limit_do_not_impose_send_cap(client):
    r = save(client, endpoint='https://models.example.com/custom/v1/', model='team/video-model-v2', maxInlineBytes=25_000_000)
    assert r.status_code == 200, r.text
    data = client.get('/api/v1/settings/models').json()['data']
    assert data['model'] == 'team/video-model-v2'
    assert data['endpoint'] == 'https://models.example.com/custom/v1'
    assert data['maxInlineBytes'] is None
    from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
    identity, frozen = service.prepare_run(client.app.state.settings, EvidenceRunInput(kind='connection', config_revision=1, allow_real_call=True), uuid.uuid4().hex)
    assert service.get_run(client.app.state.settings, identity).snapshot['maxInlineBytes'] is None
    frozen['key'] = ''
    assert save(client, revision=1, maxInlineBytes=200_000_001).status_code == 200
    assert save(client, revision=1, maxInlineBytes=0).status_code == 400


def test_agent_default_accepts_over_7mb_without_vendor_call(tmp_path, monkeypatch):
    from app.modules.video_model.evidence import agent
    from types import SimpleNamespace
    source = tmp_path / 'over7mb.mp4'
    source.write_bytes(b'x' * 8_000_000)
    monkeypatch.setattr(agent, 'build_tools', lambda session: [])
    session = SimpleNamespace(video=source, rules={'r': {'id': 'r', 'text': '检查产品'}}, script='', references=[], artifacts=[], calls=[], sha256='fixed', component_results={},
                              metadata=lambda: {}, save_manifest=lambda: None)
    calls = []
    def fixed(messages, definitions):
        calls.append(1)
        assert len(messages[1]['content'][0]['video_url']['url']) > 8_000_000
        return {'id': 'fixed-over7mb', 'choices': [{'message': {'content': json.dumps({'summary': '固定响应', 'findings': [{'id': 'i', 'rule_id': 'r', 'verdict': 'compliant', 'description': '固定符合', 'evidence_ids': []}]})}}]}
    result = agent.run_agent(session, fixed)
    assert calls == [1] and result['report']['findings'][0]['verdict'] == 'compliant'


def test_provider_413_is_reported_as_service_size_rejection(client):
    assert save(client).status_code == 200
    from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
    identity, frozen = service.prepare_run(client.app.state.settings, EvidenceRunInput(kind='connection', config_revision=1, allow_real_call=True), uuid.uuid4().hex)
    service.execute_run(client.app.state.settings, identity, frozen, transport=httpx.MockTransport(lambda request: httpx.Response(413)))
    run = service.get_run(client.app.state.settings, identity)
    assert run.error_code == 'PROVIDER_HTTP_413'
    assert '请求体过大' in run.error_message and 'Key' not in run.error_message


def test_components_can_be_switched_off_for_model_judgement(monkeypatch):
    """REVIEW_VIDEO_COMPONENTS=off：不下发组件工具，规则交由模型直接判读。"""
    from app.modules.video_model.evidence import component_runtime
    monkeypatch.setenv('REVIEW_VIDEO_COMPONENTS', 'off')
    assert component_runtime.components_enabled() is False
    assert component_runtime.component_tools(object()) == []
    monkeypatch.setenv('REVIEW_VIDEO_COMPONENTS', 'on')
    assert component_runtime.components_enabled() is True


def prepare_small_video(client,tmp_path):
    import subprocess
    import imageio_ffmpeg
    from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
    source=tmp_path/'small.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','color=s=160x120:d=10',
                    '-c:v','libx264',str(source)],check=True,timeout=20)
    assert save(client).status_code==200
    asset=client.post('/api/v1/files',files={'file':('small.mp4',source.read_bytes(),'video/mp4')},
                      data={'purpose':'video'},headers={'Idempotency-Key':uuid.uuid4().hex}).json()['data']
    return service.prepare_run(client.app.state.settings,EvidenceRunInput(kind='video',file_id=asset['id'],config_revision=1,allow_real_call=True),uuid.uuid4().hex)


def test_failed_refinement_is_persisted_and_remaining_batch_completes(client,tmp_path):
    from test_localized_video import reply,row,window
    identity,trusted=prepare_small_video(client,tmp_path)
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json=reply([row('capability-test-rule','needs_confirmation',[window(1,2),window(8,9)])]))
        if len(calls)==2:raise httpx.ReadTimeout('unknown submission',request=request)
        return httpx.Response(200,json=reply([row('capability-test-rule')],'refinement'))
    service.execute_run(client.app.state.settings,identity,trusted,transport=httpx.MockTransport(handler))
    run=service.get_run(client.app.state.settings,identity)
    assert run.status=='succeeded',run.error_message
    assert len(calls)==3
    assert [c['status'] for c in run.result['stageCalls']]==['returned','submission_unknown','returned']
    assert len(run.result['providerRequestIds'])==2 and len(run.result['modelResponses'])==2
    assert run.result['report']['findings'][0]['verdict']=='needs_confirmation'
    assert [b['status'] for b in run.result['pipeline']['batches']]==['failed','succeeded']
    assert KEY not in json.dumps(run.result)


def test_historical_snapshot_still_uses_legacy_agent(client,tmp_path):
    identity,trusted=prepare_small_video(client,tmp_path)
    with connection(client.app.state.settings.database_path) as db:
        snapshot=json.loads(db.execute('SELECT snapshot_json FROM evidence_runs WHERE id=?',(identity,)).fetchone()[0])
        snapshot.pop('pipelineVersion')
        db.execute('UPDATE evidence_runs SET snapshot_json=? WHERE id=?',(json.dumps(snapshot),identity))
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'legacy-fixed','choices':[{'message':{'content':json.dumps({'summary':'历史调用',
            'findings':[{'id':'i','rule_id':'capability-test-rule','verdict':'compliant','description':'历史通过','evidence_ids':[]}]})}}]})
    service.execute_run(client.app.state.settings,identity,trusted,transport=httpx.MockTransport(handler))
    run=service.get_run(client.app.state.settings,identity)
    assert run.status=='succeeded' and 'pipeline' not in run.result
    assert len(calls)==1 and not any(t['function']['name'].startswith('submit_') for t in calls[0]['tools'])


def test_first_round_pass_retains_downloadable_evidence_without_another_model_call(client,tmp_path):
    from test_localized_video import reply,row,window
    identity,trusted=prepare_small_video(client,tmp_path)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=reply([row('capability-test-rule',windows=[window(2,4)])]))
    service.execute_run(client.app.state.settings,identity,trusted,transport=httpx.MockTransport(handler))
    run=service.get_run(client.app.state.settings,identity)
    assert run.status=='succeeded',run.error_message
    assert len(calls)==1 and run.result['pipeline']['totalBatches']==0
    finding=run.result['report']['findings'][0]
    assert finding['verdict']=='compliant' and finding['evidence_ids']
    artifact=run.result['artifacts'][0]
    assert artifact['id']==finding['evidence_ids'][0]
    assert artifact['startMs']==0 and artifact['endMs']==6000
    assert artifact['sourceSha256']==run.snapshot['sourceSha256']
    response=client.get(f'/api/v1/evidence-runs/{identity}/artifacts/{artifact["id"]}')
    assert response.status_code==200 and response.headers['content-type']=='video/mp4' and len(response.content)>100


def test_restart_retains_localization_and_completed_batches_without_resubmission(client,tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient
    identity,trusted=prepare_small_video(client,tmp_path)
    partial={'pipeline':{'stage':'refining','completedBatches':1,'totalBatches':2,
                         'localization':{'findings':{'capability-test-rule':{'verdict':'needs_confirmation'}}},
                         'batches':[{'id':'batch-1','status':'succeeded'},{'id':'batch-2','status':'processing'}]},
             'stageCalls':[{'id':'c1','status':'processing'}],'modelResponses':[{'requestId':'fixed','response':'saved'}]}
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE evidence_runs SET status='processing',result_json=? WHERE id=?",(json.dumps(partial),identity))
    with TestClient(create_app(client.app.state.settings)) as restarted:
        restored=restarted.get('/api/v1/evidence-runs/'+identity).json()['data']
        assert restored['status']=='interrupted'
        assert restored['result']['pipeline']['batches'][0]['status']=='succeeded'
        assert restored['result']['pipeline']['batches'][1]['status']=='interrupted'
        assert restored['result']['modelResponses']==partial['modelResponses']
        assert restored['result']['stageCalls'][0]['status']=='submission_unknown'
    trusted['key']=''
