"""Two-stage video review with fixed model responses; never uses a provider."""
import copy
import json
from types import SimpleNamespace

import pytest

from app.modules.video_model.evidence import localized as pipeline
from app.modules.workflow.prompts import prompt
from app.modules.workflow.video_components import ComponentPlan, freeze_plan


def row(rid='r1', verdict='compliant', windows=None, **extra):
    return dict(rule_id=rid,verdict=verdict,observation_type='observed',description='固定观察',
                coverage_complete=True,windows=windows or [],components=[],**extra)


def window(start=3,end=4):
    return {'start':start,'end':end,'reason':'核对画面中产品文字'}


def reply(rows, stage='localization'):
    return {'id':'fixed-'+stage,'usage':{'total_tokens':10},'choices':[{'message':{'tool_calls':[
        {'id':'submit-fixed','type':'function','function':{'name':'submit_'+stage,'arguments':json.dumps({'findings':rows})}}]}}]}


def obs(start,end,**changes):
    return dict(start=start,end=end,matched=True,certain=True,whole_visible=True,logo_clear=True,occluded=False,
                orientation='normal',channel='visual',text='',term='',description='产品真实可见',**changes)


@pytest.fixture
def session(tmp_path,monkeypatch):
    source=tmp_path/'original.mp4';source.write_bytes(b'original-full-video')
    target=SimpleNamespace(video=source,output_dir=tmp_path,ffmpeg='ffmpeg',duration_ms=100_000,has_audio=True,
        sha256='original-hash',rules={'r1':{'id':'r1','text':'检查产品','importance':'must','ruleNumber':8}},
        script='批准脚本',references=[],artifacts=[],calls=[],component_results={},evidence_references=[])
    target.metadata=lambda:{'durationMs':target.duration_ms,'sourceSha256':target.sha256,'hasAudio':True}
    target.rule_source=lambda rid:target.rules[rid]
    target.save_manifest=lambda:None
    def render(session,batch):
        path=tmp_path/(batch['id']+'.mp4');path.write_bytes(batch['id'].encode());return path
    monkeypatch.setattr(pipeline,'render_internal_clip',render)
    return target


def run(session,responses):
    pending=list(responses);calls=[];states=[]
    def complete(messages,tools):
        calls.append(copy.deepcopy(messages));r=pending.pop(0)
        if isinstance(r,Exception):raise r
        return r
    result=pipeline.run_localized(session,complete,video_input=session.video,
        prompts={'localize':prompt('video-localize'),'refine':prompt('video-refine')},progress=states.append)
    assert not pending
    return result,calls,states


def test_all_pass_only_one_request(session):
    result,calls,states=run(session,[reply([row()])])
    assert len(calls)==1 and result['report']['findings'][0]['verdict']=='compliant'
    assert result['report']['findings'][0]['rule_number']==8
    assert not result['artifacts'] and result['pipeline']['totalBatches']==0
    assert states[-1]['stage']=='completed'


def test_related_rules_share_one_independent_clip_and_false_positive_is_removed(session):
    session.rules['r2']={'id':'r2','text':'检查字幕','importance':'suggested'}
    first=[row('r1','non_compliant',[window()]),row('r2','needs_confirmation',[window(4,5)])]
    result,calls,states=run(session,[reply(first),reply([row('r1'),row('r2')],'refinement')])
    assert len(calls)==2
    assert len(calls[1])==2 and all(m['role']!='tool' for m in calls[1])
    assert calls[0][1]['content'][0]!=calls[1][1]['content'][0]
    payload=json.loads(calls[1][1]['content'][1]['text'])
    assert len(payload['rules'])==2 and payload['sourceOffsetSeconds']==1
    assert 'non_compliant' not in json.dumps(payload)
    assert all(f['verdict']=='compliant' for f in result['report']['findings'])
    assert len(result['artifacts'])==1
    assert result['report']['findings'][0]['evidence_ids']==result['report']['findings'][1]['evidence_ids']
    assert states[1]['localization']['findings']['r1']['verdict']=='non_compliant'


def test_confirmed_problem_uses_original_offset_and_shared_evidence(session):
    session.rules['r2']={'id':'r2','text':'检查字幕'}
    result,_,_=run(session,[reply([row('r1','non_compliant',[window()]),row('r2','needs_confirmation',[window()])]),
                            reply([row('r1','non_compliant',[window(2,3)]),row('r2','non_compliant',[window(2,3)])],'refinement')])
    findings=result['report']['findings']
    assert all(f['verdict']=='non_compliant' for f in findings)
    assert len(result['artifacts'])==1 and findings[0]['evidence_ids']==findings[1]['evidence_ids']
    artifact=result['artifacts'][0]
    assert artifact['startMs']==1000 and artifact['endMs']==6000
    assert artifact['hasAudio'] and artifact['sourceSha256']==session.sha256
    assert len(result['evidenceReferences'])==2
    # 精判窗口统一换算成原片绝对时间（片段偏移 1 秒 + 片段内 2 秒）
    assert result['pipeline']['batches'][0]['findings']['r1']['windows'][0]['start']==3


def test_absence_and_unlocated_are_not_cropped(session):
    absent=row(verdict='non_compliant');absent['observation_type']='absence'
    result,calls,_=run(session,[reply([absent])])
    assert len(calls)==1 and not result['artifacts']
    assert result['report']['findings'][0]['verdict']=='non_compliant'
    assert '未经过片段精判' in result['report']['findings'][0]['description']
    result,calls,_=run(session,[reply([row(verdict='needs_confirmation')])])
    assert len(calls)==1 and result['report']['findings'][0]['verdict']=='needs_confirmation'


def test_one_batch_failure_does_not_stop_remaining_batches(session):
    first=row(verdict='non_compliant',windows=[window(),window(50,51)])
    result,calls,_=run(session,[reply([first]),TimeoutError('unknown billing'),reply([row()],'refinement')])
    assert len(calls)==3
    assert [b['status'] for b in result['pipeline']['batches']]==['failed','succeeded']
    assert result['report']['findings'][0]['verdict']=='needs_confirmation'


def test_more_than_four_requests_are_allowed(session):
    first=row(verdict='needs_confirmation',windows=[window(i,i+1) for i in (3,20,40,60,80)])
    result,calls,_=run(session,[reply([first])]+[reply([row()],'refinement') for _ in range(5)])
    assert len(calls)==6 and result['pipeline']['completedBatches']==5


@pytest.mark.parametrize('bad', [window(-1,2),window(2,1),window(99,101),{'start':'00:03','end':4,'reason':'核对'}])
def test_bad_windows_are_not_clamped_or_used(session,bad):
    result,calls,_=run(session,[reply([row(verdict='non_compliant',windows=[bad])])])
    assert len(calls)==1 and result['report']['findings'][0]['verdict']=='needs_confirmation'
    assert result['pipeline']['localization']['errors']['r1']


def test_long_window_split_and_no_gaps_or_oversized_clips():
    finding=pipeline.LocatedFinding.model_validate(row(verdict='needs_confirmation',windows=[window(0,90)]))
    batches=pipeline.plan_batches({'r1':finding},100_000)
    assert batches[0]['startMs']==0 and batches[-1]['endMs']==92_000
    assert all(0<b['endMs']-b['startMs']<=30_000 for b in batches)
    assert all(b['startMs']==a['endMs']-2000 for a,b in zip(batches,batches[1:]))


def test_missing_refinement_rule_is_pending_but_other_rules_survive(session):
    session.rules['r2']={'id':'r2','text':'另一条'}
    result,_,_=run(session,[reply([row('r1','needs_confirmation',[window()]),row('r2','needs_confirmation',[window()])]),reply([row('r1')],'refinement')])
    assert [f['verdict'] for f in result['report']['findings']]==['compliant','needs_confirmation']


def test_interval_replacement_preserves_unreviewed_regions():
    values=[{'start_ms':0,'end_ms':10000,'matched':True}]
    result=pipeline.replace_range(values,3000,5000,[])
    assert result==[{'start_ms':0,'end_ms':3000,'matched':True},{'start_ms':5000,'end_ms':10000,'matched':True}]


def add_duration_component(session,kind='exposure_duration',minimum=8):
    params={'referenceImageIds':['ref1'],'minTotalSeconds':minimum,'minContinuousSeconds':None,
            'minOccurrences':2,'occurrenceGapSeconds':1,'allowOcclusion':True}
    session.rules['r1']['componentPlan']=freeze_plan(ComponentPlan.model_validate({'nodes':[{'id':'node1','kind':kind,'parameters':params}]}))


def component_row(observations,**kwargs):
    result=row(**kwargs)
    result['components']=[{'component_id':'node1','coverage_complete':True,'observations':observations,'notes':'固定观察'}]
    return result


def test_component_refinement_recomputes_and_preserves_outside_intervals(session):
    add_duration_component(session,minimum=9)
    first=component_row([obs(0,3),obs(5,7),obs(50,54)],verdict='needs_confirmation',windows=[window(5,7)])
    refined=component_row([obs(2,5)],verdict='compliant')  # original 5–8 seconds
    result,_,_=run(session,[reply([first]),reply([refined],'refinement')])
    component=result['componentResults'][0]['components'][0]
    assert component['metrics']['totalMs']==10_000
    assert [50_000,54_000] in component['intervals']
    assert result['report']['findings'][0]['verdict']=='compliant'
    from app.modules.workflow.router import video_component_acceptance
    stored={'status':'succeeded','snapshot_json':json.dumps({'pipelineVersion':pipeline.VERSION,'rules':list(session.rules.values())}),'result_json':json.dumps(result)}
    assert video_component_acceptance(stored)['passed'] is True


def test_partial_coverage_cannot_pass_quantitative_component(session):
    add_duration_component(session,minimum=2)
    first=component_row([obs(0,10)],verdict='compliant');first['components'][0]['coverage_complete']=False
    # Derived component uncertainty locates the already-observed interval.
    result,_,_=run(session,[reply([first]),reply([component_row([obs(0,10)],verdict='compliant')],'refinement')])
    assert result['componentResults'][0]['verdict']=='needs_confirmation'
    assert result['report']['findings'][0]['verdict']=='needs_confirmation'
    from app.modules.workflow.router import video_component_acceptance
    stored={'status':'succeeded','snapshot_json':json.dumps({'pipelineVersion':pipeline.VERSION,'rules':list(session.rules.values())}),'result_json':json.dumps(result)}
    # Human-review eligibility is a separate policy; AI must remain unresolved.
    assert video_component_acceptance(stored)['reviewCount'] == 1


def test_component_count_does_not_double_count_overlapping_observations(session):
    add_duration_component(session,kind='exposure_count')
    first=component_row([obs(1,5),obs(4,8),obs(20,22)],verdict='compliant')
    result,calls,_=run(session,[reply([first])])
    assert len(calls)==1
    assert result['componentResults'][0]['components'][0]['metrics']['count']==2


def test_crop_failure_leaves_rule_pending_without_calling_model_again(session,monkeypatch):
    monkeypatch.setattr(pipeline,'render_internal_clip',lambda *_:(_ for _ in ()).throw(ValueError('crop failed')))
    result,calls,_=run(session,[reply([row(verdict='non_compliant',windows=[window()])])])
    assert len(calls)==1 and result['report']['findings'][0]['verdict']=='needs_confirmation'


def test_first_stage_rejects_unknown_or_duplicate_rules(session):
    with pytest.raises(ValueError):run(session,[reply([row('r2')])])
    with pytest.raises(ValueError):run(session,[reply([row(),row()])])


def test_first_pass_keeps_supporting_clip_without_refinement(session):
    result,calls,_=run(session,[reply([row(windows=[window(40,42)])])])
    assert len(calls)==1 and result['pipeline']['totalBatches']==0
    finding=result['report']['findings'][0]
    assert finding['verdict']=='compliant' and len(finding['evidence_ids'])==1
    clip=result['artifacts'][0]
    assert clip['startMs']==38000 and clip['endMs']==44000
    assert clip['sourceSha256']==session.sha256 and clip['hasAudio'] is True


def test_initial_passes_share_supporting_clip(session):
    session.rules['r2']={'id':'r2','text':'另一通过项','importance':'must'}
    result,calls,_=run(session,[reply([row(windows=[window(3,4)]),row('r2',windows=[window(4,5)])])])
    assert len(calls)==1 and len(result['artifacts'])==1
    assert result['report']['findings'][0]['evidence_ids']==result['report']['findings'][1]['evidence_ids']


def test_global_absence_pass_has_scope_record_instead_of_arbitrary_clip(session):
    passed=row();passed['observation_type']='absence'
    result,calls,_=run(session,[reply([passed])])
    finding=result['report']['findings'][0]
    assert len(calls)==1 and not result['artifacts']
    assert finding['evidence_basis']['status']=='scope_record'
    assert finding['evidence_basis']['startMs']==0 and finding['evidence_basis']['endMs']==session.duration_ms


def test_pass_evidence_render_failure_is_visible_and_never_resubmits(session,monkeypatch):
    monkeypatch.setattr(pipeline,'render_internal_clip',lambda *_:(_ for _ in ()).throw(ValueError('crop failed')))
    result,calls,_=run(session,[reply([row(windows=[window()])])])
    assert len(calls)==1 and not result['artifacts']
    assert result['report']['findings'][0]['evidence_note']


def test_refined_component_observations_use_original_timeline(session):
    add_duration_component(session,minimum=2)
    first=component_row([obs(40,41)],verdict='needs_confirmation',windows=[window(40,41)])
    refined=component_row([obs(2,4)],verdict='compliant')
    result,calls,_=run(session,[reply([first]),reply([refined],'refinement')])
    assert json.loads(calls[1][1]['content'][1]['text'])['sourceOffsetSeconds']==38
    assert result['componentResults'][0]['components'][0]['intervals']==[[40_000,42_000]]


def test_second_stage_unknown_and_invalid_timestamps_never_pass(session):
    for refined in (row(verdict='needs_confirmation'),row(windows=[window(99,100)])):
        session.component_results={};session.artifacts=[]
        result,_,_=run(session,[reply([row(verdict='non_compliant',windows=[window()])]),reply([refined],'refinement')])
        assert result['report']['findings'][0]['verdict']=='needs_confirmation'


def test_neutral_source_payload_uses_only_relevant_reference_images(session):
    add_duration_component(session,minimum=2)
    session.references=[{'id':'ref1','data':b'png1'},{'id':'unrelated','data':b'png2'}]
    first=component_row([obs(40,41)],verdict='needs_confirmation',windows=[window(40,41)])
    refined=component_row([obs(2,4)],verdict='compliant')
    _,calls,_=run(session,[reply([first]),reply([refined],'refinement')])
    assert sum(c['type']=='image_url' for c in calls[0][1]['content'])==2
    assert sum(c['type']=='image_url' for c in calls[1][1]['content'])==1


def test_restart_marker_preserves_completed_batches_and_observations():
    original={'stage':'refining','completedBatches':1,'localization':{'findings':{'r1':{'verdict':'compliant'}}},
              'batches':[{'id':'b1','status':'succeeded'},{'id':'b2','status':'processing'},{'id':'b3','status':'queued'}]}
    result=pipeline.interrupt_pipeline(original)
    assert [b['status'] for b in result['batches']]==['succeeded','interrupted','interrupted']
    assert result['localization']==original['localization'] and result['completedBatches']==1
    assert original['stage']=='refining'


def test_refinement_accepts_absolute_windows_and_relative_windows(session):
    """精判片段里模型可能给相对时间，也可能直接给原片时间，两种都要换算对。"""
    from app.modules.video_model.evidence.localized import Window, normalize_window
    # 契约上精判返回片段内相对时间，优先按相对解释
    assert normalize_window(Window(start=2, end=3, reason='片段内相对'), 5000, 1000) == (3000, 4000)
    # 相对解释超出片段时，回退成原片绝对时间（真实失败案例：片段 93–119.3 秒，模型给了 97.5–100.5）
    assert normalize_window(Window(start=97.5, end=100.5, reason='原片绝对时间'), 26300, 93000) == (97500, 100500)
    # 两种解释都落在片段外 → 无效
    assert normalize_window(Window(start=8, end=9, reason='超出片段'), 5000, 1000) is None
    assert normalize_window(Window(start=1, end=1.2, reason='整片首轮'), 5000, 0) == (1000, 1200)


def test_compliant_with_windows_is_kept_and_marked_when_coverage_incomplete(session):
    """首轮判通过但没声明全片覆盖：保留结论，附"已核对片段"说明，不再降成待确认。"""
    partial=row('r1','compliant',[window(0,1)])
    partial['coverage_complete']=False
    result,_,_=run(session,[reply([partial])])
    finding=result['report']['findings'][0]
    assert finding['verdict']=='compliant'
    assert '已核对片段' in finding['coverage_note']


def test_compliant_without_windows_still_degrades_when_coverage_missing(session):
    partial=row('r1','compliant',[])
    partial['coverage_complete']=False
    result,_,_=run(session,[reply([partial])])
    assert result['report']['findings'][0]['verdict']=='needs_confirmation'


def test_confirmed_violation_stops_further_refinement_of_same_rule(session):
    """同一规则已在某个片段确认违规后，后续片段不再重复精判，也不把"这段没看到"拼进结论。"""
    first=[row('r1','non_compliant',[window(10,12),window(60,62)])]
    refine=[row('r1','non_compliant',[window(10.5,12)])]
    result,calls,states=run(session,[reply(first),reply(refine,'refinement')])
    assert len(calls)==2  # 第二个批次整批跳过，没有再发请求
    finding=result['report']['findings'][0]
    assert finding['verdict']=='non_compliant'
    assert '固定观察' in finding['description']
    batches=result['pipeline']['batches']
    assert batches[0]['status']=='succeeded' and batches[1]['status']=='skipped'


def test_script_match_rule_stays_whole_video_only(session):
    """脚本一致性规则只做整片判断，不排进逐段精判批次。"""
    session.rules['r7']={'id':'r7','text':'与已通过脚本基本匹配','importance':'must','ruleNumber':7,'baselineKey':'script_match'}
    first=[row('r1','compliant',[]),row('r7','non_compliant',[window(10,12)])]
    result,calls,_=run(session,[reply(first)])
    assert len(calls)==1  # 没有任何精判请求
    finding=next(f for f in result['report']['findings'] if f['rule_id']=='r7')
    assert finding['verdict']=='non_compliant' and '未经过片段精判' in finding['description']
    assert result['pipeline']['totalBatches']==0
