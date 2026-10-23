import copy
import json

from app.core.database import connection
from app.modules.workflow import brief_vision
from app.modules.workflow.vision_facts import source_quote,verify_batch_facts,revalidate_saved_facts
from test_brief_vision import prepare,wait
from test_brief_processing import visual_facts,TEXT
from test_project_standards import K


def test_whitespace_maps_back_to_original_contiguous_source_without_changing_numbers():
    text='前文 产品：22g\n 豆子，\t大粉量萃取。后文'
    assert source_quote('产品：22g豆子，大粉量萃取。',text)=='产品：22g\n 豆子，\t大粉量萃取。'
    assert source_quote('产品：23g豆子，大粉量萃取。',text) is None
    assert source_quote('产品：22g豆子；大粉量萃取。',text) is None
    assert source_quote('产品：22g……大粉量萃取。',text) is None
    assert source_quote('   ',text) is None


def test_bad_item_is_pending_while_valid_items_and_source_kind_are_preserved():
    raw=visual_facts();raw['products'][0]['quote']='产品全名：\n芝士浓奶铁。'
    raw['freeform']=[{'value':'不能确认的要求','pageNo':1,'source':'visual','quote':'原文不存在'}]
    original=copy.deepcopy(raw)
    pages=[brief_vision.VisualPage(pageNo=1,text=TEXT,visualDescription='包装照片',readability='readable',uncertainties=[])]
    facts=verify_batch_facts(raw,pages)
    assert facts.products[0].quote=='产品全名：芝士浓奶铁。'
    assert not facts.freeform
    assert len(facts._pending)==1 and len(facts._repairs)==1
    assert raw==original
    # Text on the wrong page or in the wrong source field is not silently remapped.
    raw['products'][0].update(pageNo=2)
    assert not verify_batch_facts(raw,pages).products


def test_saved_reply_can_be_revalidated_without_provider_or_raw_evidence_mutation(client,monkeypatch):
    p,brief,body=prepare(client);body['extractFacts']=True;calls=[]
    def model(*args):
        calls.append(1);facts=visual_facts()
        facts['products'][0]['quote']='产品全名：\n芝士浓奶铁。'
        facts['freeform']=[{'value':'省略内容','pageNo':1,'source':'text','quote':'产品……禁用词'}]
        return {'report':{'pages':[{'pageNo':n,'text':TEXT,'visualDescription':'包装照片','readability':'readable','uncertainties':[]} for n in (1,2)],'facts':facts,'warnings':[]},'modelResponse':'unchanged raw reply','providerRequestIds':['only-call']}
    monkeypatch.setattr(brief_vision,'call_vision',model)
    client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    run=wait(client,p)['visualRun']
    with connection(client.app.state.settings.database_path) as db:
        # Reproduce a pre-fix record that rejected the whole card.
        result=run['result'];original_batches=copy.deepcopy(result['batches']);old_fact_id=result.pop('factsRunId')
        result.pop('facts');result['factsErrorCode']='BRIEF_FACTS_CITATION_MISMATCH'
        result['factsErrorMessage']='old strict check'
        db.execute('UPDATE brief_visual_runs SET result_json=? WHERE id=?',(json.dumps(result),run['id']))
        db.execute("UPDATE brief_facts_runs SET status='failed',snapshot_json='{}' WHERE id=?",(old_fact_id,))
    repaired=revalidate_saved_facts(client.app.state.settings,run['id'])
    assert calls==[1]
    assert repaired['factsRunId']!=old_fact_id
    assert len(repaired['pendingFacts'])==1 and len(repaired['quoteRepairs'])==1
    assert repaired['batches']==original_batches
    assert repaired['facts']['products'][0]['quote'] in TEXT
    assert revalidate_saved_facts(client.app.state.settings,run['id'])['factsRunId']==repaired['factsRunId']
    view=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']
    assert view['facts']['products'][0]['value']=='芝士浓奶铁'
    assert view['lastRun']['result']['factsComplete'] is False
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='brief_facts_revalidated'").fetchone()[0]==1
