import io
import time

import pytest
from PIL import Image

from app.core.database import connection
from app.modules.workflow import brief_processing,brief_vision,brief_facts,standards
from test_brief_facts import configure,facts_payload
from test_project_standards import K
from test_separate_standards import project

TEXT='品牌：测试品牌。产品全名：芝士浓奶铁。视频总时长不少于15秒。口播必须提到"浓郁芝士奶香"。禁用词：提神醒脑。'


def upload(client,p,pdf=False,key=None,revision=0):
    if pdf:
        buf=io.BytesIO();Image.new('RGB',(32,18),'red').save(buf,format='PDF')
        name,data,mime='brief.pdf',buf.getvalue(),'application/pdf'
    else:name,data,mime='brief.txt',TEXT.encode(),'text/plain'
    file=client.post('/api/v1/files',files={'file':(name,data,mime)},data={'purpose':'brief'},headers={'Idempotency-Key':K()}).json()['data']
    body={'fileId':file['id'],'title':'自动 Brief','revision':revision,'autoProcess':True}
    response=client.post(f'/api/v1/projects/{p}/brief',json=body,headers={'Idempotency-Key':key or K()})
    assert response.status_code==200,response.text
    return response.json()['data'],body


def wait(client,p):
    for _ in range(200):
        brief=client.get(f'/api/v1/projects/{p}/brief').json()['data']['brief']
        if brief['processing']['status'] not in brief_processing.ACTIVE:return brief
        time.sleep(.025)
    raise AssertionError('processing did not finish')


def visual_facts(page_no=1):
    data=facts_payload([{'id':'unused'}])
    data['brands']=[data.pop('brand')]
    for key in ('brands',*brief_facts.FACT_GROUPS):
        for item in data.setdefault(key,[]):
            item.pop('block_id',None);item.update(pageNo=page_no,source='text')
            item['applicability']={'materials':['shared'],'stage':'all','section':'品牌资料','sectionQuote':'品牌','region':'整页通用资料'}
            if key=='thresholds':item['applicability']={'materials':['video'],'stage':'execution','section':'视频','sectionQuote':'视频','region':'视频要求'}
    return data


def mocks(monkeypatch,fail_vision=False):
    calls=[]
    def vision(config,secret,snapshot,images):
        calls.append('vision')
        if fail_vision:return {'httpStatus':413,'report':None}
        report={'pages':[{'pageNo':page['pageNo'],'text':TEXT,'visualDescription':'品牌产品包装','readability':'readable','uncertainties':[]} for page,_ in images],'warnings':[]}
        if snapshot.get('extractFacts'):report['facts']=visual_facts(images[0][0]['pageNo'])
        return {'report':report,'providerRequestIds':['mock-vision']}
    def facts(config,secret,template,blocks,uncovered):
        calls.append('facts')
        assert any(TEXT==block['text'] for block in blocks)
        chosen=[block for block in blocks if block['text']==TEXT]
        return {'report':facts_payload(chosen),'providerRequestIds':['mock-facts']}
    monkeypatch.setattr(brief_vision,'call_vision',vision)
    monkeypatch.setattr(brief_facts,'call_facts',facts)
    return calls


@pytest.mark.parametrize('pdf',[False,True])
def test_upload_automatically_extracts_facts_and_recommendation_uses_them(client,monkeypatch,pdf):
    p=project(client)['id'];configure(client)
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'omni-test','revision':0,'apiKey':'sk-test-only'})
    calls=mocks(monkeypatch);key=K();value,body=upload(client,p,pdf,key)
    ready=wait(client,p)
    assert ready['processing']['status']=='succeeded',ready['processing']
    assert calls==(['vision'] if pdf else ['facts'])
    assert ready['processing']['factsRunId']
    if pdf:assert ready['processing']['visionRunId']
    assert client.post(f'/api/v1/projects/{p}/brief',json=body,headers={'Idempotency-Key':key}).status_code==200
    assert len(calls)==1
    facts=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']['facts']
    assert facts['products'][0]['value']=='芝士浓奶铁'
    captured=[]
    def recommend(config,secret,template,blocks,uncovered,facts=None):
        captured.append(facts)
        return {'report':{'rules':[{'text':'产品名','criterion':'产品名称为芝士浓奶铁','category':'品牌','importance':'must','source_block_id':facts['products'][0]['blockId'],'source_quote':facts['products'][0]['quote'],'params':{'names':['芝士浓奶铁'],'terms':[],'thresholds':[]}}],'warnings':[]}}
    monkeypatch.setattr(standards,'call_recommendation',recommend)
    response=client.post(f'/api/v1/projects/{p}/standard-recommendations',json={'sourceVersionId':ready['sourceVersionId'],'scope':'script','configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':K()})
    assert response.status_code==200,response.text
    assert response.json()['data']['status']=='succeeded',response.text
    from app.modules.workflow.fact_scope import scoped_facts
    assert captured[0]==scoped_facts(facts,'script')[0]
    assert response.json()['data']['snapshot']['factsUsed'] is True


def test_missing_key_blocks_before_paid_calls_then_manual_continue(client,monkeypatch):
    p=project(client)['id'];calls=mocks(monkeypatch)
    upload(client,p,pdf=True)
    ready=wait(client,p);assert ready['processing']['status']=='blocked'
    assert calls==[]
    configure(client)
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'omni-test','revision':0,'apiKey':'sk-test-only'})
    key=K();body={'sourceVersionId':ready['sourceVersionId']};url=f'/api/v1/projects/{p}/brief/process'
    assert client.post(url,json=body,headers={'Idempotency-Key':key}).status_code==202
    assert wait(client,p)['processing']['status']=='succeeded'
    assert client.post(url,json=body,headers={'Idempotency-Key':key}).status_code==202
    assert calls==['vision']


def test_failed_vision_stops_facts_and_rule_generation(client,monkeypatch):
    p=project(client)['id'];configure(client);calls=mocks(monkeypatch,fail_vision=True)
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'omni-test','revision':0,'apiKey':'sk-test-only'})
    upload(client,p,pdf=True);ready=wait(client,p)
    assert ready['processing']['status']=='failed'
    assert ready['processing']['errorCode']=='PROVIDER_HTTP_413'
    assert calls==['vision']
    response=client.post(f'/api/v1/projects/{p}/standard-recommendations',json={'sourceVersionId':ready['sourceVersionId'],'scope':'script','configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':K()})
    assert response.status_code==409
    assert response.json()['error']['code']=='BRIEF_PROCESSING_NOT_READY'


def test_replaced_queued_source_never_calls_models(client,monkeypatch):
    p=project(client)['id'];configure(client);calls=mocks(monkeypatch)
    queued=[]
    monkeypatch.setattr(brief_processing,'submit',lambda request,identity:queued.append(identity))
    old,_=upload(client,p)
    fresh,_=upload(client,p,revision=1)
    brief_processing.execute(client.app.state.settings,queued[0])
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT status FROM brief_processing_runs WHERE id=?',(queued[0],)).fetchone()['status']=='superseded'
    assert calls==[]
    brief_processing.execute(client.app.state.settings,queued[1])
    assert wait(client,p)['processing']['status']=='succeeded'
    assert calls==['facts']
    assert old['brief']['sourceVersionId']!=fresh['brief']['sourceVersionId']


def test_vlm_facts_failure_keeps_pages_and_only_explicit_retry_reissues_vlm(client,monkeypatch):
    p=project(client)['id']
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'omni-test','revision':0,'apiKey':'sk-test-only'})
    calls=mocks(monkeypatch);model=brief_vision.call_vision;bad=True
    def provider(*args):
        value=model(*args)
        value['report']['pages'][0].update(readability='partial',uncertainties=['小字模糊'])
        if bad:value['report']['facts']=None
        return value
    monkeypatch.setattr(brief_vision,'call_vision',provider)
    upload(client,p,pdf=True);ready=wait(client,p)
    assert ready['processing']['status']=='failed'
    assert ready['processing']['errorCode']=='BRIEF_FACTS_CITATION_MISMATCH'
    assert ready['visualRun']['status']=='succeeded'
    assert calls==['vision']
    bad=False
    url=f'/api/v1/projects/{p}/brief/process';body={'sourceVersionId':ready['sourceVersionId']}
    assert client.post(url,json=body,headers={'Idempotency-Key':K()}).status_code==202
    fresh=wait(client,p)
    assert fresh['processing']['status']=='succeeded'
    assert calls==['vision','vision']
    assert fresh['uncoveredSources']==['page:1']
    assert client.post(url,json=body,headers={'Idempotency-Key':K()}).status_code==202
    assert wait(client,p)['processing']['status']=='succeeded'
    assert calls==['vision','vision']  # valid VLM facts are reused even when OCR has warnings
    facts=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']['facts']
    assert any('不完整' in warning for warning in facts['warnings'])


def test_pdf_does_not_require_decomposition_credentials_or_call_language_model(client,monkeypatch):
    p=project(client)['id']
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'omni-test','revision':0,'apiKey':'sk-test-only'})
    calls=mocks(monkeypatch)
    monkeypatch.setattr(brief_facts,'call_facts',lambda *_:pytest.fail('PDF must never invoke the language facts model'))
    upload(client,p,pdf=True);ready=wait(client,p)
    assert ready['processing']['status']=='succeeded',ready['processing']
    assert calls==['vision']
    view=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']
    assert view['lastRun']['snapshot']['modelRole']=='vision'
    assert view['lastRun']['snapshot']['billingOrigin']=='shared-vision-call'
    assert view['facts']['products'][0]['blockId']==f"vision:{ready['processing']['visionRunId']}:1:text"


def test_pdf_legacy_language_endpoint_refuses_before_billing(client,monkeypatch):
    p=project(client)['id'];configure(client)
    monkeypatch.setattr(brief_processing,'submit',lambda *_:None)
    value,_=upload(client,p,pdf=True)
    monkeypatch.setattr(brief_facts,'call_facts',lambda *_:pytest.fail('Legacy PDF path must not bill language model'))
    response=client.post(f'/api/v1/projects/{p}/brief/facts',json={'sourceVersionId':value['brief']['sourceVersionId'],'configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':K()})
    assert response.status_code==409
    assert response.json()['error']['code']=='BRIEF_FACTS_USE_VLM'


import pytest as _legacy_pytest

@_legacy_pytest.fixture(autouse=True)
def legacy_vision_contract(monkeypatch):
    from app.modules.workflow import brief_vision
    monkeypatch.setattr(brief_vision, 'PIPELINE_VERSION', None)
