import base64
import io
import json
import time

import httpx
import pytest
from PIL import Image

from app.core.database import connection
from app.modules.workflow import brief_vision as vision,standards,router as workflow
from test_separate_standards import project,payload
from test_script_versions import K


def prepare(client,page_count=2):
    p=project(client)['id'];images=[Image.new('RGB',(320,180),color) for color in (['red','green'] * ((page_count+1)//2))[:page_count]]
    buf=io.BytesIO();images[0].save(buf,format='PDF',save_all=True,append_images=images[1:])
    file=client.post('/api/v1/files',files={'file':('scanned.pdf',buf.getvalue(),'application/pdf')},data={'purpose':'brief'},headers={'Idempotency-Key':K()}).json()['data']
    registered=client.post(f'/api/v1/projects/{p}/brief',json={'fileId':file['id'],'title':'扫描Brief','revision':0},headers={'Idempotency-Key':K()})
    assert registered.status_code==200,registered.text
    brief=registered.json()['data']['brief'];assert brief['pageCount']==page_count
    client.put('/api/v1/settings/models',json={'endpoint':'https://models.example.com/v1','model':'omni-test','revision':0,'apiKey':'sk-test-only'})
    return p,brief,{'sourceVersionId':brief['sourceVersionId'],'briefRevision':1,'configRevision':1,'allowRealCall':True}


def wait(client,p):
    for _ in range(200):
        brief=client.get(f'/api/v1/projects/{p}/brief').json()['data']['brief']
        if brief['visualRun'] and brief['visualRun']['status'] not in ('queued','processing'):return brief
        time.sleep(.025)
    raise AssertionError('Vision worker did not complete')


def report(count=2,partial=False):
    return {'pages':[{'pageNo':n,'text':f'第{n}页：品牌产品名称，禁止镜像。','visualDescription':'产品与文字均清晰可见','readability':'partial' if partial and n==2 else 'readable','uncertainties':['小字模糊'] if partial and n==2 else []} for n in range(1,count+1)],'warnings':[]}


def test_real_rendered_pages_in_request_reuse_and_source_quotes(client,monkeypatch):
    p,brief,body=prepare(client);calls=[];actual=httpx.Client
    def provider(request):
        calls.append(1);value=json.loads(request.content)
        assert value['model']=='omni-test' and value['reasoning_effort']=='none'
        parts=value['messages'][1]['content'];urls=[x['image_url']['url'] for x in parts if x['type']=='image_url']
        assert len(urls)==2
        for url in urls:
            with Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))) as image:assert max(image.size)==2200
        return httpx.Response(200,json={'id':'mock-vision','usage':{'total_tokens':100},'choices':[{'message':{'content':json.dumps(report())}}]})
    monkeypatch.setattr(vision.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    path=f'/api/v1/projects/{p}/brief/vision';key=K()
    assert client.post(path,json={**body,'allowRealCall':False},headers={'Idempotency-Key':K()}).status_code==400
    started=client.post(path,json=body,headers={'Idempotency-Key':key});assert started.status_code==202,started.text
    ready=wait(client,p);run=ready['visualRun']
    assert run['status']=='succeeded',run
    assert ready['uncoveredSources']==[] and ready['nativeUncoveredSources']==['page:1','page:2']
    assert run['result']['coverageVerified'] is False
    assert 'base64' not in json.dumps(run) and 'sk-test-only' not in json.dumps(run)
    for replaykey in (key,K()):assert client.post(path,json=body,headers={'Idempotency-Key':replaykey}).json()['data']['id']==run['id']
    assert len(calls)==1
    url=f"/api/v1/projects/{p}/brief/vision/{run['id']}/pages/1"
    assert client.get(url).headers['content-type']=='image/jpeg'
    assert client.get(url.replace(p,'wrong-project')).status_code==404
    assert client.get(url[:-1]+'3').status_code==404
    with connection(client.app.state.settings.database_path) as db:
        blocks,uncovered=standards.source_blocks(db,p,brief['sourceVersionId'])
        assert not uncovered and any(b.get('origin')=='ai-visual-extraction' for b in blocks)
        chosen=next(b for b in blocks if '禁止镜像' in b['text'])
    rules=payload(client);rules['sourceVersionId']=brief['sourceVersionId']
    rules['scriptRules'][0].update({'sourceBlockId':chosen['id'],'sourceQuote':'禁止镜像'})
    response=client.post(f'/api/v1/projects/{p}/publish-task',json=rules,headers={'Idempotency-Key':K()})
    assert response.status_code==201,response.text
    task=response.json()['data']['id'];captured=[]
    def script_model(config,key,snapshot):
        captured.append(snapshot)
        return {'report':{'summary':'已核对图文识读结果','findings':[{'rule_id':r['id'],'verdict':'compliant','quote':'','description':'符合','suggestion':''} for r in snapshot['rules']]}}
    monkeypatch.setattr(workflow,'call_script_model',script_model)
    review=client.post(f'/api/v1/tasks/{task}/scripts/submit-and-review',json={'inputType':'text','text':'品牌产品名称，禁止镜像。','configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':K()})
    assert review.status_code==201,review.text
    # 判断阶段只依据规则：Brief 不再进入模型输入，覆盖元数据与规则来源仍可追溯。
    assert 'brief' not in captured[0]
    assert captured[0]['briefCoverage']['uncoveredSources']==[]
    assert any('禁止镜像' in (r.get('source') or {}).get('quote','') for r in captured[0]['rules'])
    assert review.json()['data']['autoReview']['status']=='succeeded'


@pytest.mark.parametrize('defect',['partial','summary_omission','missing_page','duplicate_page','timeout'])
def test_partial_and_invalid_extraction_never_clear_unread_pages(client,monkeypatch,defect):
    p,brief,body=prepare(client);calls=[]
    def model(*args):
        calls.append(1)
        if defect=='timeout':raise httpx.ReadTimeout('mock')
        value=report(1 if defect=='missing_page' else 2,partial=defect=='partial')
        if defect=='summary_omission':value['pages'][1]['text']='此处包含大量评论截图文字，如“产品好喝”等'
        if defect=='duplicate_page':value['pages'][1]['pageNo']=1
        return {'report':value,'providerRequestIds':['mock-partial']}
    monkeypatch.setattr(vision,'call_vision',model)
    assert client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()}).status_code==202
    ready=wait(client,p)
    if defect in ('partial','summary_omission'):
        assert ready['visualRun']['status']=='succeeded'
        assert ready['uncoveredSources']==['page:2']
        assert not ready['visualRun']['result']['coverageComplete']
    else:
        assert ready['visualRun']['status']=='failed'
        assert ready['uncoveredSources']==['page:1','page:2']
        assert not ready['visionRunId']
    assert len(calls)==1


@pytest.mark.parametrize('wrong_last_page',[False,True])
def test_long_pdf_batches_keep_physical_pages_and_validate_each_batch(client,monkeypatch,wrong_last_page):
    p,brief,body=prepare(client,page_count=25);calls=[]
    def model(config,secret,snapshot,images):
        numbers=[page['pageNo'] for page,_ in images];calls.append(numbers)
        assert snapshot['pageCount']==len(numbers)
        assert all(block['pageNo'] in numbers for block in snapshot['nativeTextBlocks'])
        value=report(25)
        value['pages']=[page for page in value['pages'] if page['pageNo'] in numbers]
        if wrong_last_page and numbers==[25]:value['pages'][0]['pageNo']=1
        return {'report':value,'providerRequestIds':[f'mock-batch-{len(calls)}'],'usage':[{'total_tokens':1}]}
    monkeypatch.setattr(vision,'call_vision',model)
    response=client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    assert response.status_code==202,response.text
    ready=wait(client,p);run=ready['visualRun']
    assert calls==[list(range(1,25)),[25]]
    assert run['result']['providerRequestIds']==['mock-batch-1','mock-batch-2']
    assert run['status']==('failed' if wrong_last_page else 'succeeded')
    if wrong_last_page:
        assert len(ready['uncoveredSources'])==25
        assert 'report' not in run['result']
    else:
        assert ready['uncoveredSources']==[]
        assert [page['pageNo'] for page in run['result']['report']['pages']]==list(range(1,26))
        assert client.get(f"/api/v1/projects/{p}/brief/vision/{run['id']}/pages/25").status_code==200


def test_image_batch_byte_budget_does_not_limit_document(monkeypatch):
    monkeypatch.setattr(vision,'MAX_IMAGE_BYTES',8)
    monkeypatch.setattr(vision,'page_bytes',lambda *_:b'abcd')
    pages=[{'pageNo':n} for n in range(1,4)]
    batches=list(vision.image_batches(None,'run',pages))
    assert [[page['pageNo'] for page,_ in batch] for batch in batches]==[[1],[2],[3]]


def test_vlm_returns_facts_in_same_batches_and_merges_with_source_ids(client,monkeypatch):
    from test_brief_processing import visual_facts,TEXT
    p,brief,body=prepare(client,page_count=25);body['extractFacts']=True;calls=[]
    def model(config,secret,snapshot,images):
        numbers=[page['pageNo'] for page,_ in images];calls.append(numbers)
        facts=visual_facts(numbers[0])
        if numbers[0]==25:facts['brands']=[]
        return {'report':{'pages':[{'pageNo':n,'text':TEXT,'visualDescription':'包装参考图','readability':'readable','uncertainties':[],'structureComplete':True,'regions':[{'id':'body','role':'context'}]} for n in numbers],'warnings':[],'facts':facts},'providerRequestIds':[f'mock-{numbers[0]}']}
    monkeypatch.setattr(vision,'call_vision',model)
    assert client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()}).status_code==202
    ready=wait(client,p);run=ready['visualRun']
    assert run['status']=='succeeded',run
    assert calls==[list(range(n,min(n+6,26))) for n in range(1,26,6)]
    assert len(run['result']['facts']['products'])==1
    view=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']
    assert view['lastRun']['id']==run['result']['factsRunId']
    assert view['lastRun']['snapshot']['modelRole']=='vision'
    assert view['facts']['brand']['value']=='测试品牌'
    assert view['facts']['products'][0]['blockId']==f"vision:{run['id']}:1:text"
    assert view['lastRun']['result']['providerRequestIds']==['mock-1','mock-7','mock-13','mock-19','mock-25']
    with connection(client.app.state.settings.database_path) as db:
        blocks,_=standards.source_blocks(db,p,brief['sourceVersionId'])
        for item in view['facts']['products']:
            assert any(b['id']==item['blockId'] and item['quote'] in b['text'] for b in blocks)
    # Idempotent replay must not reissue VLM calls or duplicate fact records.
    assert client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()}).json()['data']['id']==run['id']
    assert len(calls)==5


@pytest.mark.parametrize('defect',['missing_facts','quote','page','missing_brand','brand_conflict'])
def test_vlm_facts_errors_preserve_page_extraction_without_language_fallback(client,monkeypatch,defect):
    from test_brief_processing import visual_facts,TEXT
    from app.modules.workflow import brief_facts
    p,brief,body=prepare(client);body['extractFacts']=True
    def model(*args):
        facts=visual_facts()
        if defect=='quote':facts['products'][0]['quote']='不存在的原文'
        if defect=='page':facts['products'][0]['pageNo']=3
        if defect=='missing_brand':facts['brands']=[]
        if defect=='brand_conflict':facts['brands'].append({'value':'另一品牌','pageNo':1,'source':'text','quote':'另一品牌'})
        report={'pages':[{'pageNo':n,'text':TEXT+'另一品牌','visualDescription':'包装参考图','readability':'readable','uncertainties':[]} for n in (1,2)],'warnings':[]}
        if defect!='missing_facts':report['facts']=facts
        return {'report':report,'providerRequestIds':['mock-preserved'],'modelResponse':'raw-preserved'}
    monkeypatch.setattr(vision,'call_vision',model)
    monkeypatch.setattr(brief_facts,'call_facts',lambda *_:pytest.fail('No paid language fallback'))
    client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    ready=wait(client,p);run=ready['visualRun']
    assert run['status']=='succeeded',run
    assert ready['uncoveredSources']==['page:1','page:2']
    if defect in ('quote','page','missing_brand','brand_conflict'):
        assert run['result']['facts']
        assert not run['result']['factsComplete']
        if defect in ('quote','page'):
            assert len(run['result']['pendingFacts'])==1
            assert not run['result']['facts']['products']
        else:assert not run['result']['facts']['brand']['value']
    else:
        assert not run['result'].get('facts')
        assert run['result']['factsErrorCode']
    assert run['result']['batches'][0]['modelResponse']=='raw-preserved'
    view=client.get(f'/api/v1/projects/{p}/brief/facts').json()['data']
    if defect in ('quote','page','missing_brand','brand_conflict'):
        assert view['facts'] is not None
        assert view['lastRun']['status']=='succeeded'
    else:
        assert view['facts'] is None
        assert view['lastRun']['status']=='failed'
    assert view['lastRun']['result']['providerRequestIds']==['mock-preserved']


def test_actual_vlm_request_includes_combined_schema_without_second_model(client,monkeypatch):
    from test_brief_processing import visual_facts,TEXT
    p,brief,body=prepare(client);body['extractFacts']=True;calls=[];actual=httpx.Client
    def provider(request):
        value=json.loads(request.content);calls.append(value)
        prompt=value['messages'][0]['content']
        assert 'PageFacts' in prompt and 'pageNo' in prompt and 'facts' in prompt
        assert 'applicability' in prompt and 'sectionQuote' in prompt and 'materials' in prompt
        assert value['reasoning_effort']=='none'
        assert len([part for part in value['messages'][1]['content'] if part['type']=='image_url'])==2
        report={'pages':[{'pageNo':n,'text':TEXT,'visualDescription':'产品包装','readability':'readable','uncertainties':[]} for n in (1,2)],'warnings':[],'facts':visual_facts()}
        return httpx.Response(200,json={'id':'mock-combined','usage':{'total_tokens':1},'choices':[{'message':{'content':json.dumps(report)}}]})
    monkeypatch.setattr(vision.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    ready=wait(client,p)
    assert ready['visualRun']['result']['facts']['brand']['value']=='测试品牌'
    assert ready['visualRun']['result']['facts']['products'][0]['applicability']['materials']==['shared']
    assert len(calls)==1


import pytest as _legacy_pytest

@_legacy_pytest.fixture(autouse=True)
def legacy_vision_contract(monkeypatch):
    from app.modules.workflow import brief_vision
    monkeypatch.setattr(brief_vision, 'PIPELINE_VERSION', None)
