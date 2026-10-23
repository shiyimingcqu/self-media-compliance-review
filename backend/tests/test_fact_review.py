import copy
import json

from app.core.database import connection
from app.modules.workflow import brief_vision,brief_facts
from test_brief_vision import prepare,wait
from test_brief_processing import visual_facts,TEXT
from test_project_standards import K


def setup_card(client,monkeypatch):
    p,brief,body=prepare(client);body['extractFacts']=True
    text=TEXT+'\n麦咖啡 McCafé 麦当劳 McDonald’s\n只需连续引文'
    def model(*args):
        facts=visual_facts()
        facts['brands']=[{'value':v,'pageNo':1,'source':'text','quote':v} for v in ['麦咖啡','McCafé','麦当劳','McDonald’s']]
        facts['freeform']=[{'value':'需要修改的事实','pageNo':1,'source':'text','quote':'只需……引文'}]
        return {'report':{'pages':[{'pageNo':n,'text':text,'visualDescription':'包装参考图','readability':'readable','uncertainties':[]} for n in (1,2)],'warnings':[],'facts':facts},'providerRequestIds':['mock-only-vlm']}
    monkeypatch.setattr(brief_vision,'call_vision',model)
    client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    run=wait(client,p)['visualRun'];view=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']
    return p,brief,run,view


def test_human_brand_entities_and_aliases_are_versioned_source_checked_and_idempotent(client,monkeypatch):
    p,brief,visual,view=setup_card(client,monkeypatch)
    old=copy.deepcopy(view);pending=view['lastRun']['result']['pendingFacts']
    index={item['value']:i for i,item in enumerate(pending)}
    decisions=[{'itemIndex':index['麦咖啡'],'action':'brand_primary'},
               {'itemIndex':index['McCafé'],'action':'brand_alias','targetBrand':'麦咖啡'},
               {'itemIndex':index['麦当劳'],'action':'brand_related'},
               {'itemIndex':index['McDonald’s'],'action':'brand_alias','targetBrand':'麦当劳'}]
    body={'sourceVersionId':brief['sourceVersionId'],'baseRunId':view['lastRun']['id'],'decisions':decisions}
    path=f'/api/v1/projects/{p}/brief/facts/review';key=K()
    response=client.post(path,json=body,headers={'Idempotency-Key':key});assert response.status_code==200,response.text
    reviewed=response.json()['data'];facts=reviewed['result']['facts']
    assert facts['brand']['value']=='麦咖啡'
    assert [(e['name']['value'],e['role'],[a['value'] for a in e['aliases']]) for e in facts['brandEntities']]==[('麦咖啡','primary',['McCafé']),('麦当劳','related',['McDonald’s'])]
    assert len(reviewed['result']['pendingFacts'])==1
    assert reviewed['snapshot']['billingOrigin']=='human-no-model-call'
    assert client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']['id']==reviewed['id']
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).status_code==409
    with connection(client.app.state.settings.database_path) as db:
        assert json.loads(db.execute('SELECT result_json FROM brief_facts_runs WHERE id=?',(old['lastRun']['id'],)).fetchone()['result_json'])==old['lastRun']['result']
        assert json.loads(db.execute('SELECT result_json FROM brief_visual_runs WHERE id=?',(visual['id'],)).fetchone()['result_json'])==visual['result']
        assert brief_facts.latest_facts(db,p,brief['sourceVersionId'])==facts
    # The remaining bad quote cannot be approved as-is, but can be corrected from the same page.
    newbody={'sourceVersionId':brief['sourceVersionId'],'baseRunId':reviewed['id'],'decisions':[{'itemIndex':0,'action':'accept'}]}
    assert client.post(path,json=newbody,headers={'Idempotency-Key':K()}).status_code==400
    newbody['decisions'][0].update(value='修正后的事实',quote='只需连续引文')
    fixed=client.post(path,json=newbody,headers={'Idempotency-Key':K()});assert fixed.status_code==200,fixed.text
    assert fixed.json()['data']['result']['factsComplete'] is True
    assert fixed.json()['data']['result']['pendingFacts']==[]


def test_vlm_brand_entities_group_aliases_without_treating_them_as_competitors(client,monkeypatch):
    from app.modules.workflow.vision_facts import PageFacts,verify_batch_facts,merge_facts
    from app.modules.workflow.brief_vision import VisualPage
    def fact(value):return {'value':value,'pageNo':1,'source':'text','quote':value}
    raw={'brands':[fact(v) for v in ['麦咖啡','McCafé','麦当劳','McDonald’s']],
         'brandEntities':[{'name':fact('麦咖啡'),'aliases':[fact('McCafé')],'role':'primary'},
                          {'name':fact('麦当劳'),'aliases':[fact('McDonald’s')],'role':'related'}]}
    page=VisualPage(pageNo=1,text='麦咖啡 McCafé 麦当劳 McDonald’s',visualDescription='品牌资料',readability='readable')
    verified=verify_batch_facts(raw,[page]);diagnostics={}
    facts=merge_facts('run',[page.model_dump(by_alias=True)],[verified],diagnostics)
    assert facts['brand']['value']=='麦咖啡'
    assert len(facts['brandEntities'])==2
    assert facts['competitors']==[]
    assert diagnostics['pendingFacts']==[]
