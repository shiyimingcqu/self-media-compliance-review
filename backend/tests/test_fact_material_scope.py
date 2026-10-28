import copy
import json
import pytest

from app.core.database import connection
from app.modules.workflow import standards
from app.modules.workflow.brief_vision import VisualPage
from app.modules.workflow.fact_scope import scoped_facts, filter_scoped_candidates
from app.modules.workflow.vision_facts import verify_batch_facts, merge_facts
from app.modules.workflow.script_citations import source_catalog
from test_project_brief import upload
from test_script_versions import K


TEXT='文案：不可拉踩竞品。图片与视频：不得出现其他饮料。视频：产品前15秒露出。脚本：安排产品特写。'


def scope(materials,stage='execution',section='图片与视频'):
    return {'materials':materials,'stage':stage,'section':section,'sectionQuote':section,'region':'右侧合并单元格'}


def facts_fixture():
    return {'brand':{'value':'测试品牌','blockId':'b','quote':'文案'},'freeform':[
        {'value':'不得出现其他饮料','quote':'不得出现其他饮料','blockId':'b','applicability':scope(['image','video'])},
        {'value':'安排产品特写','quote':'安排产品特写','blockId':'b','applicability':scope(['script'],'planning','脚本')} ]}


def test_vlm_verifies_page_label_and_keeps_same_text_with_distinct_scopes():
    raw={'freeform':[{'value':'不得出现其他饮料','quote':'不得出现其他饮料','pageNo':20,'source':'text','applicability':scope(['video'])},
                     {'value':'不得出现其他饮料','quote':'不得出现其他饮料','pageNo':20,'source':'text','applicability':scope(['image'])},
                     {'value':'产品前15秒露出','quote':'产品前15秒露出','pageNo':20,'source':'text','applicability':scope(['script'],'planning','不存在的标题')}]}
    original=copy.deepcopy(raw)
    page=VisualPage(pageNo=20,text=TEXT,visualDescription='表格分区',readability='readable')
    batch=verify_batch_facts(raw,[page])
    merged=merge_facts('run',[page.model_dump(by_alias=True)],[batch])
    assert raw==original
    assert len(merged['freeform'])==3
    assert merged['freeform'][0]['applicability']['materials']==['video']
    assert merged['freeform'][1]['applicability']['materials']==['image']
    assert merged['freeform'][2]['applicability']['materials']==['unknown']
    assert 'video' in json.dumps(merged)


@pytest.mark.parametrize('target,expected',[('script',['安排产品特写']),('video',['不得出现其他饮料']),('image',['不得出现其他饮料'])])
def test_scope_selection_and_mixed_citation_rejection(target,expected):
    facts=facts_fixture();original=copy.deepcopy(facts)
    selected,excluded=scoped_facts(facts,target)
    assert [i['value'] for i in selected['freeform']]==expected
    assert excluded and facts==original
    catalog=source_catalog([{'id':'b','text':TEXT}],facts)
    refs=[r['ref'] for r in catalog if r['quote'] in ('不得出现其他饮料','安排产品特写')]
    raw={'rules':[{'text':'把不同范围混在一条','sourceRefs':refs}],'warnings':[]}
    filtered,rejected=filter_scoped_candidates(raw,facts,target,catalog)
    assert not filtered['rules'] and rejected[0]['candidate']==raw['rules'][0]


def test_unknown_metadata_preserves_source_facts_and_legacy_is_not_relabelled():
    facts=facts_fixture()
    facts['freeform'][0]['applicability']=scope(['unknown'],'unknown')
    selected,excluded=scoped_facts(facts,'script')
    assert not excluded
    assert len(selected['freeform'])==2
    assert selected['freeform'][0]['applicability']['materials']==['unknown']
    del facts['freeform'][0]['applicability']
    assert len(scoped_facts(facts,'script')[0]['freeform'])==2


@pytest.mark.parametrize('applicability,target', [
    (scope(['video'],'unknown'),'script'),
    (scope(['unknown'],'execution'),'script'),
    (scope(['unknown'],'publishing'),'video'),
    (scope(['image','unknown'],'unknown'),'video'),
])
def test_partially_unknown_metadata_still_enforces_explicit_boundaries(applicability,target):
    facts={'freeform':[{'value':'明确范围的要求','applicability':applicability}]}
    selected,excluded=scoped_facts(facts,target)
    assert not selected['freeform'] and len(excluded)==1


def test_new_vlm_missing_scope_is_unknown_without_relabelling_legacy():
    raw={'freeform':[{'value':'安排产品特写','quote':'安排产品特写','pageNo':20,'source':'text'}]}
    page=VisualPage(pageNo=20,text=TEXT,visualDescription='分区表格',readability='readable')
    assert verify_batch_facts(raw,[page]).freeform[0].applicability is None
    assert verify_batch_facts(raw,[page],require_scope=True).freeform[0].applicability.materials==['unknown']


def test_generation_filters_wrong_material_before_baseline_merge_single_call(client,monkeypatch):
    project=client.post('/api/v1/projects',json={'name':'分区测试','brandName':'测试品牌','platforms':['xiaohongshu']},headers={'Idempotency-Key':K()}).json()['data']
    uploaded,_=upload(client,project['id'],TEXT)
    source=uploaded.json()['data']['brief']['sourceVersionId']
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'test-model','revision':0,'apiKey':'test-fixture-key'})
    with connection(client.app.state.settings.database_path) as db:
        blocks,_=standards.source_blocks(db,project['id'],source)
    bid=next(b['id'] for b in blocks if '不得出现其他饮料' in b['text'])
    facts=facts_fixture()
    facts['brand']['blockId']=bid
    for item in facts['freeform']:item['blockId']=bid
    for group,value,quote in [('products','产品','产品'),('forbiddenTerms','不可拉踩竞品','不可拉踩竞品'),('thresholds','前15秒','产品前15秒露出')]:
        facts[group]=[{'value':value,'quote':quote,'blockId':bid,'applicability':scope(['unknown'],'unknown')}]
    from app.modules.workflow import brief_facts
    monkeypatch.setattr(brief_facts,'latest_facts_run',lambda *_:{'facts':facts,'complete':True,'pendingCount':0})
    calls=[];raw=[]
    def model(config,key,template,blocks,uncovered,facts=None,source_refs=None):
        calls.append(1)
        assert [i['value'] for i in facts['freeform']]==['安排产品特写']
        assert all(len(facts[group])==1 for group in ('products','forbiddenTerms','thresholds'))
        assert any(r['quote']=='不可拉踩竞品' for r in source_refs)
        assert not any('不得出现其他饮料' in r['quote'] for r in source_refs)
        ref=next(r['ref'] for r in source_catalog(None,facts_fixture() | {'freeform':[dict(facts_fixture()['freeform'][0],blockId=bid)]},available_blocks=[{'id':bid,'text':TEXT}]) if r['quote']=='不得出现其他饮料')
        report={'rules':[{'text':'脚本道具不得出现其他饮料','criterion':'错误跨材料候选','importance':'must','category':'品牌安全','sourceRefs':[ref]}],'warnings':[]}
        raw.append(json.dumps(report,ensure_ascii=False))
        return {'report':report,'modelResponse':raw[-1],'providerRequestIds':['fixed-only']}
    monkeypatch.setattr(standards,'call_recommendation',model)
    body={'sourceVersionId':source,'scope':'script','configRevision':1,'allowRealCall':True}
    path=f"/api/v1/projects/{project['id']}/standard-recommendations";key=K()
    response=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    run=response.json()['data']
    assert run['status']=='succeeded',run
    assert len(run['result']['report']['rules'])==8
    assert run['result']['excludedCandidates'][0]['text']=='脚本道具不得出现其他饮料'
    assert run['result']['modelResponse']==raw[0]
    assert run['snapshot']['excludedFacts'][0]['applicability']['materials']==['image','video']
    client.post(path,json=body,headers={'Idempotency-Key':key})
    assert calls==[1]


import pytest as _legacy_pytest

@_legacy_pytest.fixture(autouse=True)
def legacy_vision_contract(monkeypatch):
    from app.modules.workflow import brief_vision
    monkeypatch.setattr(brief_vision, 'PIPELINE_VERSION', None)
