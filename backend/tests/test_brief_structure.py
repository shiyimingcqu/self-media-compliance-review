"""Spatial fixtures validate coverage plumbing, not live VLM recognition accuracy."""
import copy

from app.modules.workflow.brief_vision import VisualPage, checked_result
from app.modules.workflow.vision_facts import verify_batch_facts, merge_facts
from test_brief_vision import prepare, wait, report
from test_script_versions import K


COLOR='不可产品画面调色饱和度低，使食物无食欲'
VIDEO='视频前15s需清晰且完整露出产品'
IMAGE='产品露出不少于4张'


def page_fixture():
    def region(identity,materials,quote):
        label='图片与视频' if len(materials)>1 else '视频' if materials==['video'] else '图片'
        return {'id':identity,'rowHeader':label,'columnHeader':'不要做' if quote==COLOR else '请遵守',
                'role':'requirement','applicability':{'materials':materials,'stage':'execution','section':label,'sectionQuote':label,'region':'表格单元格'},
                'items':[{'quote':quote,'value':quote,'group':'freeform'}]}
    return VisualPage(pageNo=20,text='测试品牌\n• '+COLOR+'\n• '+VIDEO+'\n• '+IMAGE,
                      visualDescription='表格左侧纵向分图片、视频；右侧图片与视频合并区域列出不要做。',
                      readability='readable',structureComplete=True,regions=[
                          region('merged',['image','video'],COLOR),region('video',['video'],VIDEO),region('image',['image'],IMAGE)])


def validate(page):
    raw={'brands':[{'value':'测试品牌','quote':'测试品牌','pageNo':20,'source':'text'}]}
    batch=verify_batch_facts(raw,[page],require_scope=True,require_structure=True)
    diagnostics={}
    facts=merge_facts('frozen-run',[page.model_dump(by_alias=True)],[batch],diagnostics)
    return facts,diagnostics


def test_transcribed_but_omitted_region_requirement_reaches_fact_card_with_material_scope():
    page=page_fixture();before=copy.deepcopy(page)
    facts,result=validate(page)
    assert page==before
    assert len(facts['freeform'])==3
    color=next(item for item in facts['freeform'] if item['value']==COLOR)
    assert color['quote']==COLOR and color['blockId']=='vision:frozen-run:20:text'
    assert color['applicability']['materials']==['image','video']
    assert result['factsComplete'] and result['factsCoverage']['complete']
    from app.modules.workflow.fact_scope import scoped_facts
    assert not scoped_facts(facts,'script')[0]['freeform']
    assert [item['value'] for item in scoped_facts(facts,'video')[0]['freeform']]==[COLOR,VIDEO]
    assert [item['value'] for item in scoped_facts(facts,'image')[0]['freeform']]==[COLOR,IMAGE]


def test_missing_region_item_is_flagged_by_transcribed_bullet_not_silently_complete():
    page=page_fixture();page.regions=page.regions[1:]
    facts,result=validate(page)
    assert not result['factsComplete'] and not result['factsCoverage']['complete']
    assert COLOR in result['factsCoverage']['pages'][0]['unmappedItems'][0]
    assert not any(item['value']==COLOR for item in facts['freeform'])
    projected=checked_result({'report':{'pages':[page.model_dump(by_alias=True)]},**result})
    assert not projected['coverageComplete']
    assert projected['report']['pages'][0]['readability']=='partial'
    assert '区域要求覆盖待核对' in projected['report']['pages'][0]['uncertainties'][0]


def test_unverifiable_region_quote_remains_pending_instead_of_becoming_a_fact():
    page=page_fixture();page.regions[0].items[0].quote='原图没有的更强禁令'
    facts,result=validate(page)
    assert result['pendingFacts'][0]['value']==COLOR
    assert not result['factsComplete']
    assert all(item['value']!=COLOR for item in facts['freeform'])


def test_reference_page_does_not_generate_a_mandatory_fact():
    page=VisualPage(pageNo=20,text='测试品牌\n• 结合卖点进行手绘',visualDescription='优秀示例，创意向',
                    readability='readable',structureComplete=True,regions=[{
                        'id':'reference','rowHeader':'创意向','columnHeader':'优秀示例','role':'reference',
                        'items':[{'quote':'结合卖点进行手绘','value':'必须使用手绘','group':'freeform'}]}])
    facts,result=validate(page)
    assert not facts['freeform']
    assert result['factsCoverage']['complete']


def test_missing_structure_and_empty_requirement_cell_cannot_claim_full_coverage():
    page=page_fixture();page.regions[0].items=[];page.structure_complete=False
    _,result=validate(page)
    assert not result['factsComplete']
    assert len(result['factsCoverage']['pages'][0]['issues'])>=2


def test_region_capacity_overflow_is_preserved_as_pending():
    page=page_fixture()
    from app.modules.workflow.vision_facts import PageFacts,PageFact,reconcile_regions
    facts=PageFacts(freeform=[PageFact(value=str(i),quote=str(i),pageNo=1,source='text') for i in range(30)])
    reconcile_regions(facts,[page])
    assert len(facts.freeform)==30 and len(facts._pending)==3
    assert not facts._coverage[0]['complete']


def test_invalid_region_schema_retains_transcription_and_marks_it_for_review():
    from app.modules.workflow.brief_vision import parse_visual_report
    raw={'pages':[page_fixture().model_dump(by_alias=True)]}
    raw['pages'][0]['regions'][0]['role']='invalid'
    before=copy.deepcopy(raw)
    parsed=parse_visual_report(raw)
    assert raw==before
    assert parsed.pages[0].text==page_fixture().text
    assert not parsed.pages[0].structure_complete and not parsed.pages[0].regions
    assert parsed.pages[0].uncertainties


def test_bare_page_array_preserves_regions_without_inventing_missing_summary():
    from app.modules.workflow.brief_vision import parse_visual_report
    raw=[page_fixture().model_dump(by_alias=True)]
    # A context classification outside the enum must not discard requirements.
    raw[0]['regions'].append({'id':'context','role':'context','items':[
        {'value':'测试品牌','quote':'测试品牌','group':'background'}]})
    before=copy.deepcopy(raw)
    parsed=parse_visual_report(raw)
    assert raw==before and len(parsed.pages[0].regions)==4
    facts=verify_batch_facts(None,parsed.pages,require_scope=True,require_structure=True)
    assert len(facts.freeform)==3
    assert not facts.brands and not facts._coverage[0]['complete']
    assert '未返回事实汇总' in facts._coverage[0]['issues'][-1]


def test_array_response_completes_page_recognition_but_keeps_facts_incomplete(client,monkeypatch):
    from app.modules.workflow import brief_vision
    project,_,body=prepare(client)
    output=report()['pages']
    for page in output:
        page.update(structureComplete=True,regions=[{'id':'ban','role':'requirement',
            'items':[{'value':'禁止镜像','quote':'禁止镜像','group':'freeform'}]}])
    monkeypatch.setattr(brief_vision,'call_vision',lambda *args:{'report':output})
    body['extractFacts']=True
    assert client.post(f'/api/v1/projects/{project}/brief/vision',json=body,headers={'Idempotency-Key':K()}).status_code==202
    ready=wait(client,project);result=ready['visualRun']['result']
    assert ready['visualRun']['status']=='succeeded'
    assert len(ready['recognizedPages'])==2 and len(result['facts']['freeform'])==1
    assert not result['factsComplete'] and not result['factsCoverage']['complete']


def test_schema_error_explains_field_instead_of_claiming_page_mismatch(client,monkeypatch):
    from app.modules.workflow import brief_vision
    project,_,body=prepare(client)
    output=report();output['pages'][0]['readability']='unexpected'
    monkeypatch.setattr(brief_vision,'call_vision',lambda *args:{'report':output})
    assert client.post(f'/api/v1/projects/{project}/brief/vision',json=body,headers={'Idempotency-Key':K()}).status_code==202
    failed=wait(client,project)['visualRun']
    assert failed['errorCode']=='BRIEF_VISION_SCHEMA_INVALID'
    assert 'readability' in failed['errorMessage']
    assert failed['result']['validationErrors'][0]['location']==['pages',0,'readability']


def test_cross_page_capacity_never_drops_verified_requirements_or_claims_complete():
    pages=[];batches=[]
    for n in (1,2):
        values=[f'要求{n}-{i}' for i in range(20)]
        page=VisualPage(pageNo=n,text='测试品牌 '+','.join(values),visualDescription='正文',readability='readable',
                        structureComplete=True,regions=[{'id':'body','role':'context'}])
        raw={'brands':[{'value':'测试品牌','quote':'测试品牌','pageNo':n,'source':'text'}],
             'freeform':[{'value':value,'quote':value,'pageNo':n,'source':'text'} for value in values]}
        pages.append(page.model_dump(by_alias=True))
        batches.append(verify_batch_facts(raw,[page],require_structure=True))
    result={};facts=merge_facts('run',pages,batches,result)
    assert len(facts['freeform'])==30 and len(result['pendingFacts'])==10
    assert not result['factsComplete'] and not result['factsCoverage']['complete']


def test_explicit_rerecognition_bypasses_cache_but_idempotency_prevents_duplicate_calls(client,monkeypatch):
    from app.modules.workflow import brief_vision
    project,_,body=prepare(client);calls=[]
    def model(*args):
        calls.append(1)
        return {'report':report(),'providerRequestIds':['fixture']}
    monkeypatch.setattr(brief_vision,'call_vision',model)
    path=f'/api/v1/projects/{project}/brief/vision'
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).status_code==202
    old=wait(client,project)['visualRun']
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).json()['data']['id']==old['id']
    key=K();body['forceReprocess']=True
    response=client.post(path,json=body,headers={'Idempotency-Key':key})
    new=wait(client,project)['visualRun']
    assert response.status_code==202 and new['id']!=old['id']
    assert client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']['id']==new['id']
    assert len(calls)==2


def test_failed_retry_keeps_successful_region_details_with_matching_source(client,monkeypatch):
    import httpx
    from app.modules.workflow import brief_vision
    project,_,body=prepare(client)
    output=report()
    output['pages'][0]['regions']=[{'id':'shared-ban','rowHeader':'图片／视频','columnHeader':'不要做×',
                                  'role':'requirement','items':[{'value':'禁止镜像','quote':'禁止镜像'}]}]
    output['pages'][0]['structureComplete']=True
    monkeypatch.setattr(brief_vision,'call_vision',lambda *args:{'report':output})
    path=f'/api/v1/projects/{project}/brief/vision'
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).status_code==202
    successful=wait(client,project)
    def timeout(*args):raise httpx.ReadTimeout('fixture timeout')
    monkeypatch.setattr(brief_vision,'call_vision',timeout)
    assert client.post(path,json={**body,'forceReprocess':True},headers={'Idempotency-Key':K()}).status_code==202
    failed=wait(client,project)
    assert failed['visualRun']['status']=='failed'
    assert failed['visionRunId']==successful['visionRunId']
    assert failed['recognizedPages']==successful['recognizedPages']
    assert failed['recognizedPages'][0]['regions'][0]['rowHeader']=='图片／视频'


import pytest as _legacy_pytest

@_legacy_pytest.fixture(autouse=True)
def legacy_vision_contract(monkeypatch):
    from app.modules.workflow import brief_vision
    monkeypatch.setattr(brief_vision, 'PIPELINE_VERSION', None)
