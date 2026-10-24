import io
import json
import pytest
from PIL import Image

from app.modules.workflow.video_components import ComponentPlan, freeze_plan
from app.modules.video_model.evidence.component_runtime import ComponentObservationInput, evaluate, aggregate_rule
from app.modules.video_model.evidence.agent import AnalysisFailure, run_agent
from app.core.database import connection
from test_evidence_tools import session
from test_separate_standards import project, payload
from test_script_versions import K


def node(kind='product_presence',identity='n1',**params):
    return {'id':identity,'kind':kind,'parameters':{'referenceImageIds':['ref-a'],**params}}


def install(session,nodes,operator='all',same=False):
    plan=freeze_plan(ComponentPlan.model_validate({'nodes':nodes,'operator':operator,'sameSegment':same}))
    session.rules={'r1':{'id':'r1','ruleNumber':1,'text':'产品要求','importance':'must','componentPlan':plan}}
    return plan


def observation(start=1000,end=3000,**values):
    return {'start_ms':start,'end_ms':end,'matched':True,'certain':True,'whole_visible':True,
            'logo_clear':True,'occluded':False,'orientation':'normal','description':'按参考图核对真实可辨产品',**values}


def check(session,kind='product_presence',identity='n1',observations=None,complete=True,issue='i1'):
    return evaluate(session,kind,ComponentObservationInput.model_validate({'rule_id':'r1','component_id':identity,
        'issue_id':issue,'coverage_complete':complete,'observations':observations if observations is not None else [observation()]}))


def test_brand_threshold_and_same_segment_intersection(session):
    install(session,[node(endSeconds=2,requireWholeProduct=True),node('logo_presence','n2',endSeconds=6)],same=True)
    first=check(session,observations=[observation(1000,2000)])
    assert first['verdict']=='compliant'
    second=check(session,'logo_presence','n2',[observation(4000,5000)])
    assert second['verdict']=='compliant'
    assert second['ruleResult']['verdict']=='non_compliant'
    result=aggregate_rule(session,session.rules['r1'])
    assert result['commonIntervals']==[] and result['verdict']=='non_compliant'


def test_overlap_and_or_unknown_preserve_logic(session):
    install(session,[node(),node('logo_presence','n2')],same=True)
    check(session,observations=[observation(1000,4000)])
    check(session,'logo_presence','n2',[observation(2000,5000)])
    assert aggregate_rule(session,session.rules['r1'])['commonIntervals']==[[2000,4000]]
    session.component_results.clear()
    session.rules['r1']['componentPlan'].update(operator='any',sameSegment=False)
    check(session,observations=[],complete=False)
    check(session,'logo_presence','n2',[])
    assert aggregate_rule(session,session.rules['r1'])['verdict']=='needs_confirmation'


def test_duration_union_count_gap_and_frozen_submission(session):
    install(session,[node('exposure_duration',minContinuousSeconds=3,minTotalSeconds=5),node('exposure_count','n2',minOccurrences=2,occurrenceGapSeconds=1)])
    obs=[observation(0,2000),observation(1000,3000),observation(5000,7000)]
    duration=check(session,'exposure_duration',observations=obs)
    assert duration['metrics']=={'longestContinuousMs':3000,'totalMs':5000,
                                 'observedLongestContinuousMs':3000,'observedTotalMs':5000}
    assert duration['verdict']=='compliant'
    count=check(session,'exposure_count','n2',obs)
    assert count['metrics']['count']==2
    assert check(session,'exposure_count','n2',obs)==count
    with pytest.raises(ValueError,match='覆盖'):
        check(session,'exposure_count','n2',[])


def test_mirror_uncertain_and_absence_not_automatically_passed(session):
    install(session,[node('product_mirroring')])
    assert check(session,'product_mirroring',observations=[])['verdict']=='needs_confirmation'
    session.component_results.clear()
    assert check(session,'product_mirroring',observations=[observation(orientation='uncertain',certain=False)])['verdict']=='needs_confirmation'
    session.component_results.clear()
    assert check(session,'product_mirroring',observations=[observation(orientation='mirrored')])['verdict']=='non_compliant'


def test_voice_requires_audio_and_exact_term_coverage(session):
    install(session,[node('required_speech',terms=['芝士浓','奶浓'])])
    one=observation(channel='audio',term='芝士浓',text='这杯的芝士味很浓')
    assert check(session,'required_speech',observations=[one])['verdict']=='non_compliant'
    session.component_results.clear();session.has_audio=False
    assert check(session,'required_speech',observations=[one])['verdict']=='needs_confirmation'


def test_agent_actually_calls_component_and_receives_full_video_and_reference(session):
    install(session,[node()]);session.references=[{'id':'ref-a','name':'产品图','sha256':'x','data':b'png-test'}]
    calls=[]
    def model(messages,tools):
        assert messages[1]['content'][0]['type']=='video_url'
        assert any(part['type']=='image_url' for part in messages[1]['content'])
        assert any(t['function']['name']=='check_product_presence' for t in tools)
        calls.append(1)
        if len(calls)==1:
            arguments={'rule_id':'r1','component_id':'n1','issue_id':'i1','coverage_complete':True,'observations':[observation()]}
            return {'choices':[{'message':{'tool_calls':[{'id':'c1','type':'function','function':{'name':'check_product_presence','arguments':json.dumps(arguments)}}]}}]}
        result=json.loads(messages[-1]['content'])['data']
        assert result['verdict']=='compliant' and result['ruleResult']['verdict']=='compliant'
        return {'choices':[{'message':{'content':json.dumps({'summary':'模拟产品露出通过','findings':[{'id':'i1','rule_id':'r1','rule_number':1,'verdict':'compliant','description':'可辨认产品出现','evidence_ids':[]}]})}}]}
    result=run_agent(session,model)
    assert len(calls)==2 and result['componentResults'][0]['verdict']=='compliant'
    assert result['toolCalls'][0]['name']=='check_product_presence'
    assert not result['coverageVerified']


def test_skipping_component_cannot_fabricate_pass(session):
    install(session,[node()])
    answer={'summary':'通过','findings':[{'id':'i1','rule_id':'r1','verdict':'compliant','description':'通过'}]}
    with pytest.raises(AnalysisFailure) as error:
        run_agent(session,lambda *_:{'choices':[{'message':{'content':json.dumps(answer)}}]})
    assert error.value.code=='COMPONENT_CHECK_INCOMPLETE'


def picture(color='red'):
    out=io.BytesIO();Image.new('RGB',(80,50),color).save(out,format='PNG');return out.getvalue()


def upload(client,project_id,key=None,color='red'):
    return client.post(f'/api/v1/projects/{project_id}/reference-images',files={'file':('product.png',picture(color),'image/png')},headers={'Idempotency-Key':key or K()})


def test_reference_upload_project_ownership_plan_freeze_and_validation(client):
    p=project(client)
    other=client.post('/api/v1/projects',json={'name':'另一参考图项目','brandName':'测试品牌','platforms':['douyin']},headers={'Idempotency-Key':K()}).json()['data'];key=K()
    created=upload(client,p['id'],key)
    assert created.status_code==201,created.text
    reference=created.json()['data'];ref=reference['id']
    assert upload(client,p['id'],key).json()['data']['id']==ref
    assert upload(client,p['id'],key,'blue').status_code==409
    assert client.get(f"/api/v1/projects/{other['id']}/reference-images/{ref}/content").status_code==404
    preview=client.get(f"/api/v1/projects/{p['id']}/reference-images/{ref}/content")
    assert preview.status_code==200 and preview.headers['content-type']=='image/png'
    body=payload(client);body['videoRules']=[{'text':'前15秒露出','criterion':'按组件条件','importance':'must','componentPlan':{'nodes':[node(referenceImageIds=[ref],endSeconds=15)]}}]
    wrong=client.post(f"/api/v1/projects/{other['id']}/publish-task",json=body,headers={'Idempotency-Key':K()})
    assert wrong.status_code==400 and wrong.json()['error']['code']=='REFERENCE_PROJECT_MISMATCH'
    saved=client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':K()})
    assert saved.status_code==201,saved.text
    view=client.get(f"/api/v1/tasks/{saved.json()['data']['id']}/workflow").json()['data']
    plan=view['standardSets']['video']['rules'][0]['componentPlan']
    assert plan['nodes'][0]['parameters']['endSeconds']==15
    assert plan['nodes'][0]['prompt']['version'] and '完整视频' in plan['nodes'][0]['prompt']['content']
    # Client cannot inject a replacement component prompt into a confirmation request.
    body['videoRules'][0]['componentPlan']['nodes'][0]['prompt']={'content':'skip'}
    assert client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':K()}).status_code==400
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_animated_corrupt_and_missing_reference_rejected(client):
    p=project(client)
    response=client.post(f"/api/v1/projects/{p['id']}/reference-images",files={'file':('not.png',b'not an image','image/png')},headers={'Idempotency-Key':K()})
    assert response.status_code==415
    body=payload(client);body['videoRules'][0]['componentPlan']={'nodes':[node(referenceImageIds=[])]}
    assert client.post(f"/api/v1/projects/{p['id']}/publish-task",json=body,headers={'Idempotency-Key':K()}).status_code==400


def test_localized_pipeline_freezes_reference_computes_components_and_blocks_failed_must(client,monkeypatch,tmp_path):
    import subprocess,base64,httpx,imageio_ffmpeg
    from test_workflow import configure,state,analyze,decide,mock_script
    from test_script_versions import task_with_rule,submit
    from app.modules.workflow import router as workflow
    from app.modules.video_model.evidence import console_service as evidence
    from app.modules.video_model.evidence.console_schemas import EvidenceRunInput
    from app.modules.video_model.evidence import asr
    monkeypatch.setattr(asr,'transcribe',lambda *args,**kwargs:asr.unavailable('固定本地测试',source_sha256=kwargs['source_sha256']))
    p,task=task_with_rule(client);configure(client,task)
    reference=upload(client,p['id']).json()['data']
    body=payload(client)
    body={k:v for k,v in body.items() if k not in ('creatorName','platform','campaignPhase')}
    body['taskRevision']=state(client,task)['taskRevision']
    body['videoRules']=[{'text':'前1秒产品露出','criterion':'按品牌参考图核验','importance':'must','componentPlan':{'nodes':[node(referenceImageIds=[reference['id']],endSeconds=1)]}}]
    confirmed=client.post(f"/api/v1/tasks/{task['id']}/standards/confirm",json=body,headers={'Idempotency-Key':K()})
    assert confirmed.status_code==200,confirmed.text
    submit(client,task['id'],{'inputType':'text','text':'品牌A产品与卖点展示安排。'})
    monkeypatch.setattr(workflow,'call_script_model',mock_script)
    assert analyze(client,task,state(client,task)).json()['data']['status']=='succeeded'
    assert decide(client,task,state(client,task)).status_code==200
    source=tmp_path/'component-video.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','color=c=blue:s=160x120:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True,timeout=20)
    asset=client.post('/api/v1/files',files={'file':('video.mp4',source.read_bytes(),'video/mp4')},data={'purpose':'video'},headers={'Idempotency-Key':K()}).json()['data']
    key=K();params=EvidenceRunInput(kind='video',config_revision=1,allow_real_call=True,task_id=task['id'],file_id=asset['id'])
    identity,frozen=evidence.prepare_run(client.app.state.settings,params,key)
    initial=evidence.get_run(client.app.state.settings,identity)
    assert initial.snapshot['references'][0]['sha256']==reference['sha256']
    assert 'base64' not in json.dumps(initial.snapshot)
    rid=initial.snapshot['rules'][0]['id'];calls=[]
    def provider(request):
        payload=json.loads(request.content);messages=payload['messages'];parts=messages[1]['content']
        assert parts[0]['type']=='video_url' and any(p['type']=='image_url' for p in parts)
        image=next(p['image_url']['url'] for p in parts if p['type']=='image_url')
        assert base64.b64decode(image.split(',')[1]).startswith(b'\x89PNG')
        stage=(payload.get('tools') or [{}])[0].get('function',{}).get('name','')
        calls.append(stage or 'plain')
        if stage=='submit_component_pass':
            # 组件改为独立一次请求：只提交观察，结论由代码算。
            body={'observations':[{'rule_id':rid,'component_id':'n1','issue_id':'issue-'+rid,
                                   'coverage_complete':True,'observations':[],'notes':'完整检查未发现指定产品'}]}
            message={'tool_calls':[{'id':'c-component','type':'function',
                                    'function':{'name':'submit_component_pass','arguments':json.dumps(body)}}]}
            return httpx.Response(200,json={'id':f'mock-{len(calls)}','choices':[{'message':message}]})
        from test_localized_video import row,reply
        finding=row(rid,'non_compliant');finding['observation_type']='absence'
        message=reply([finding])['choices'][0]['message']
        return httpx.Response(200,json={'id':f'mock-{len(calls)}','choices':[{'message':message}]})
    evidence.execute_run(client.app.state.settings,identity,frozen,transport=httpx.MockTransport(provider))
    result=evidence.get_run(client.app.state.settings,identity)
    assert result.status=='succeeded',(result.status,result.error_code,result.error_message)
    assert calls==['submit_localization','submit_component_pass'] and len(result.result['artifacts'])==0
    assert result.result['pipeline']['totalBatches']==0
    assert result.result['componentResults'][0]['verdict']=='non_compliant'
    view=state(client,task)
    assert view['videoReviewReady'] and not view['videoComponentAcceptance']['passed']
    assert decide(client,task,view,scope='video').json()['error']['code']=='VIDEO_MANDATORY_CHECKS_NOT_PASSED'
    assert decide(client,task,view,scope='video',decision='returned').status_code==200
    assert evidence.prepare_run(client.app.state.settings,params,key)==(identity,None)


def _component_call(index, name, component_id, **overrides):
    args = {'rule_id': 'r1', 'component_id': component_id, 'issue_id': 'i1',
            'coverage_complete': True, 'observations': [observation()], **overrides}
    return {'id': f'c{index}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}


def _final_report(verdict='compliant'):
    return {'choices': [{'message': {'content': json.dumps({
        'summary': '模拟批量审核', 'findings': [{'id': 'i1', 'rule_id': 'r1', 'rule_number': 1,
                                                 'verdict': verdict, 'description': '按批量观察判定'}]})}}]}


def test_batch_mode_submits_every_component_in_one_tool_turn(session):
    install(session, [node(), node('logo_presence', 'n2')])
    turns = []

    def model(messages, tools):
        turns.append(len(tools))
        if len(turns) == 1:
            return {'choices': [{'message': {'tool_calls': [
                _component_call(1, 'check_product_presence', 'n1'),
                _component_call(2, 'check_logo_presence', 'n2')]}}]}
        return _final_report()

    result = run_agent(session, model, batch_observations=True)
    # 恰好两次模型调用：首轮批量提交 + 次轮报告；次轮不再提供工具。
    assert turns == [len(turns) and turns[0], 0] and len(turns) == 2
    assert result['componentResults'][0]['verdict'] == 'compliant'
    assert [c['name'] for c in result['toolCalls']] == ['check_product_presence', 'check_logo_presence']


def test_batch_mode_allows_one_repair_turn_only_after_tool_failure(session):
    install(session, [node(), node('logo_presence', 'n2')])
    turns = []

    def model(messages, tools):
        turns.append(len(tools))
        if len(turns) == 1:
            broken = _component_call(2, 'check_logo_presence', 'n2')
            broken['function']['arguments'] = json.dumps({'rule_id': 'r1', 'component_id': 'missing-node',
                                                           'issue_id': 'i1', 'coverage_complete': True,
                                                           'observations': [observation()]})
            return {'choices': [{'message': {'tool_calls': [
                _component_call(1, 'check_product_presence', 'n1'), broken]}}]}
        if len(turns) == 2:
            return {'choices': [{'message': {'tool_calls': [
                _component_call(3, 'check_logo_presence', 'n2')]}}]}
        return _final_report()

    result = run_agent(session, model, batch_observations=True)
    assert len(turns) == 3 and turns[-1] == 0
    assert [c['ok'] for c in result['toolCalls']] == [True, False, True]
    assert result['componentResults'][0]['verdict'] == 'compliant'


def test_component_evidence_is_cut_from_intervals_without_model_tool_calls(session):
    install(session, [node('exposure_duration', minContinuousSeconds=5, minTotalSeconds=10)])
    turns = []

    def model(messages, tools):
        turns.append(len(tools))
        if len(turns) == 1:
            return {'choices': [{'message': {'tool_calls': [
                _component_call(1, 'check_exposure_duration', 'n1', observations=[observation(1000, 3000)])]}}]}
        return {'choices': [{'message': {'content': json.dumps({
            'summary': '露出不足', 'findings': [{'id': 'i1', 'rule_id': 'r1', 'rule_number': 1,
                                                 'verdict': 'non_compliant', 'description': '累计露出不足',
                                                 'evidence_ids': []}]})}}]}

    result = run_agent(session, model, batch_observations=True)
    assert len(result['autoEvidenceIds']) == 1
    assert result['report']['findings'][0]['evidence_ids'] == result['autoEvidenceIds']
    assert [(a['kind'], a['startMs'], a['endMs']) for a in result['artifacts']] == [('clip', 1000, 3000)]


def test_invalid_final_report_is_rebuilt_from_complete_components(session):
    install(session, [node('exposure_duration', minContinuousSeconds=5, minTotalSeconds=10)])
    turns = []

    def model(messages, tools):
        turns.append(len(tools))
        if len(turns) == 1:
            return {'choices': [{'message': {'tool_calls': [
                _component_call(1, 'check_exposure_duration', 'n1', observations=[observation(1000, 3000)])]}}]}
        return {'choices': [{'message': {'content': '不是 JSON'}}]}

    result = run_agent(session, model, batch_observations=True)
    assert result['recovery']['method'] == 'component_results'
    assert result['recovery']['reason'] == 'FINAL_REPORT_INVALID_JSON'
    assert result['report']['findings'][0]['verdict'] == 'non_compliant'
    assert result['autoEvidenceIds']


def test_incomplete_components_still_fail_instead_of_rebuilding(session):
    install(session, [node(), node('logo_presence', 'n2')])
    turns = []

    def model(messages, tools):
        turns.append(len(tools))
        if len(turns) == 1:
            return {'choices': [{'message': {'tool_calls': [
                _component_call(1, 'check_product_presence', 'n1')]}}]}
        return {'choices': [{'message': {'content': '不是 JSON'}}]}

    with pytest.raises(AnalysisFailure) as error:
        run_agent(session, model, batch_observations=True)
    assert error.value.code == 'FINAL_REPORT_INVALID_JSON'


def test_uncertain_observation_keeps_observed_metrics(session):
    """certain=false 不再把区间清零：观察到 8.5 秒、身份未确认 → 待确认且给出可用数字。"""
    install(session,[node('exposure_duration',minTotalSeconds=5)])
    result=check(session,'exposure_duration',observations=[observation(1000,9500,certain=False,logo_clear=False)])
    assert result['verdict']=='needs_confirmation'
    assert result['metrics']['totalMs']==0 and result['metrics']['observedTotalMs']==8500
    assert '观察到约8.5秒' in result['reason']


def test_observed_below_threshold_is_non_compliant(session):
    install(session,[node('exposure_duration',minTotalSeconds=5)])
    result=check(session,'exposure_duration',observations=[observation(1000,3000,certain=False)])
    assert result['metrics']['observedTotalMs']==2000
    assert result['verdict']=='non_compliant'


def test_count_uses_observed_occurrences_when_uncertain(session):
    install(session,[node('exposure_count',minOccurrences=2,occurrenceGapSeconds=1)])
    result=check(session,'exposure_count',observations=[observation(1000,2000,certain=False),observation(6000,7000,certain=False)])
    assert result['metrics']['count']==0 and result['metrics']['observedCount']==2
    assert result['verdict']=='needs_confirmation'


def test_uncertain_component_captures_frame_for_human_confirmation(session):
    from app.modules.video_model.evidence.agent import AgentReport, Finding, attach_uncertain_frames
    install(session,[node('exposure_duration',minTotalSeconds=5)])
    check(session,'exposure_duration',observations=[observation(1000,9500,certain=False)])
    report=AgentReport(findings=[Finding(id='i1',rule_id='r1',rule_number=1,verdict='needs_confirmation',description='身份待确认')],summary='待确认')
    created=attach_uncertain_frames(session,report)
    assert created and report.findings[0].evidence_ids==created
    assert session.artifacts[-1]['kind']=='image' and session.artifacts[-1]['issueId']=='i1'
    assert attach_uncertain_frames(session,report)==[]  # 同一问题只补一张


def _acceptance_row(verdict, *, unconfigured=None):
    from app.modules.workflow.router import video_component_acceptance
    snapshot={'rules':[{'id':'r1','ruleNumber':1,'text':'必须项','importance':'must','layer':'brief','enabled':True,'videoReviewLevel':'strict'}],
              'unconfiguredRules':unconfigured or []}
    result={'report':{'findings':[{'rule_id':'r1','verdict':verdict}]},'componentResults':[]}
    return video_component_acceptance({'status':'succeeded','snapshot_json':json.dumps(snapshot),'result_json':json.dumps(result)})


def test_needs_confirmation_no_longer_blocks_brand_approval():
    """AI 待确认交人工判断，不再挡住"确认通过"。"""
    acceptance=_acceptance_row('needs_confirmation')
    assert acceptance['passed'] is True
    assert acceptance['blockingRules']==[] and acceptance['reviewCount']==1
    assert '待确认' in acceptance['message']


def test_non_compliant_still_blocks_brand_approval():
    acceptance=_acceptance_row('non_compliant')
    assert acceptance['passed'] is False
    assert [r['ruleId'] for r in acceptance['blockingRules']]==['r1']
    assert '明确不合规' in acceptance['message']


def test_unconfigured_rule_still_blocks_brand_approval():
    acceptance=_acceptance_row('compliant',unconfigured=[{'ruleId':'r1','ruleNumber':1,'text':'必须项','layer':'brief','reason':'待品牌确认产品名称'}])
    assert acceptance['passed'] is False and acceptance['configurationRequired'] is True
