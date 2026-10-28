"""Test the default staged pipeline with image crops, never paid model calls."""
import io

import pytest
from PIL import Image

from app.modules.workflow import brief_vision
from app.modules.workflow.brief_layout_pipeline import LayoutRegion,crop_region,job_batches
from test_brief_vision import prepare,wait
from test_script_versions import K

@pytest.fixture(autouse=True)
def staged_pipeline(monkeypatch):
    monkeypatch.setattr(brief_vision, 'PIPELINE_VERSION', 'layout-crop-summary-v2')


COLOR='不可产品画面调色饱和度低，使食物无食欲'


def layout():
    return {'pages':[
        {'pageNo':1,'readingMode':'sequential','regions':[]},
        {'pageNo':2,'readingMode':'regions','regions':[
            {'id':'allow','label':'视频要求','rowHeader':'视频','columnHeader':'请遵守',
             'bbox':[0,0,500,1000],'role':'requirement'},
            {'id':'ban','label':'共用禁令','rowHeader':'图片／视频','columnHeader':'不要做',
             'bbox':[500,0,1000,1000],'role':'requirement'}]}]}


def model_fixture(calls, defect=None):
    def model(config,secret,snapshot,images):
        stage=snapshot['visionStage'];calls.append((stage,images,snapshot['stagePayload']))
        if stage=='layout':
            report=layout()
            if defect=='box':report['pages'][1]['regions'][0]['bbox']=[600,0,500,1000]
            if defect=='page':report['pages'][1]['pageNo']=3
        elif stage=='ocr':
            assert [meta['jobId'] for meta,_ in images]==['page:1','page:2:allow','page:2:ban']
            sizes=[]
            for _,data in images:
                with Image.open(io.BytesIO(data)) as image:sizes.append(image.size)
            assert sizes[1][0]==sizes[0][0]//2 and sizes[2]==sizes[1]
            report={'items':[{'id':meta['jobId'],'text':text,'readability':'readable'}
                            for (meta,_),text in zip(images,['测试品牌\n新品芝士浓奶铁','前15s完整露出产品',COLOR])]}
            if defect=='ocr-id':report['items'][1]['id']='not-requested'
            if defect=='ocr-partial':report['items'][1].update(readability='partial',uncertainties=['右侧小字模糊'])
        else:
            assert stage=='summary' and images==[]
            sources=snapshot['stagePayload']['sources']
            brand=next(s for s in sources if '测试品牌' in s['quote'])
            color=next(s for s in sources if s['quote']==COLOR)
            assert color['pageNo']==2
            report={'facts':{'brandEntities':[{'name':{'value':'测试品牌','sourceId':brand['id']},'role':'primary'}],
                             'publishRules':[{'value':'产品画面不得调低饱和度影响食欲','sourceId':color['id'],
                                              'applicability':{'materials':['image','video'],'stage':'execution','section':'图片／视频','sectionQuote':'图片／视频','region':'右侧共用禁令'}}]}}
            if defect=='summary':report={}
            if defect=='quote':report['facts']['publishRules'][0]['sourceId']='not-a-real-source'
        return {'report':report,'providerRequestIds':['fixture-'+stage]}
    return model


def run(client,monkeypatch,defect=None):
    project,_,body=prepare(client);body['extractFacts']=True;calls=[]
    monkeypatch.setattr(brief_vision,'call_vision',model_fixture(calls,defect))
    response=client.post(f'/api/v1/projects/{project}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    assert response.status_code==202,response.text
    return project,wait(client,project),calls


def test_default_layout_crop_ocr_then_single_document_summary(client,monkeypatch):
    project,ready,calls=run(client,monkeypatch)
    visual=ready['visualRun'];result=visual['result']
    assert visual['status']=='succeeded',visual
    assert [stage for stage,_,_ in calls]==['layout','ocr','summary']
    assert 'structuredBatchPages' not in visual['snapshot']
    assert visual['snapshot']['pipelineVersion']=='layout-crop-summary-v2'
    assert result['progress']=={'stage':'complete','completed':2,'total':2}
    assert ready['recognizedPages'][1]['regions'][1]['bbox']==[500,0,1000,1000]
    assert len(result['facts']['publishRules'])==1 and not result['facts']['freeform']
    assert result['factsComplete'] and result['pendingFacts']==[]
    # Full OCR remains available even though it wasn't expanded into a card.
    assert '前15s完整露出产品' in ready['recognizedPages'][1]['text']
    cards=client.get(f'/api/v1/projects/{project}/brief/facts').json()['data']
    assert cards['lastRun']['snapshot']['pipelineVersion']=='layout-crop-summary-v2'


@pytest.mark.parametrize('defect,expected_stages',[('box',['layout']),('page',['layout']),('ocr-id',['layout','ocr'])])
def test_invalid_regions_or_ids_stop_dependent_stages_without_retry(client,monkeypatch,defect,expected_stages):
    _,ready,calls=run(client,monkeypatch,defect)
    assert ready['visualRun']['status']=='failed'
    assert [stage for stage,_,_ in calls]==expected_stages
    assert not ready['visionRunId']
    assert ready['visualRun']['result']['batches']


def test_summary_failure_keeps_completed_ocr_and_creates_failed_facts_record(client,monkeypatch):
    project,ready,calls=run(client,monkeypatch,'summary')
    assert ready['visualRun']['status']=='succeeded'
    assert len(ready['recognizedPages'])==2
    assert ready['visualRun']['result']['factsErrorCode']
    assert [stage for stage,_,_ in calls]==['layout','ocr','summary']
    facts=client.get(f'/api/v1/projects/{project}/brief/facts').json()['data']
    assert facts['facts'] is None and facts['lastRun']['status']=='failed'


def test_explicit_summary_retry_reuses_ocr_and_does_not_rebill_layout_or_crops(client,monkeypatch):
    project,ready,first_calls=run(client,monkeypatch,'summary')
    calls=[]
    monkeypatch.setattr(brief_vision,'call_vision',model_fixture(calls))
    body={'sourceVersionId':ready['sourceVersionId'],'briefRevision':ready['revision'],
          'configRevision':1,'allowRealCall':True,'extractFacts':True}
    response=client.post(f'/api/v1/projects/{project}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    assert response.status_code==202,response.text
    retried=wait(client,project)['visualRun']
    assert [stage for stage,_,_ in calls]==['summary']
    assert retried['result']['reusedOcrFrom']==ready['visualRun']['id']
    assert retried['result']['factsComplete'] and len(first_calls)==3


def test_progress_is_saved_before_requests_and_cards_are_not_from_region_count(client,monkeypatch):
    from app.core.database import connection
    import json
    project,_,body=prepare(client);body['extractFacts']=True;stages=[]
    provider=model_fixture([])
    def model(config,secret,snapshot,images):
        with connection(client.app.state.settings.database_path) as db:
            row=db.execute('SELECT result_json FROM brief_visual_runs WHERE project_id=? ORDER BY rowid DESC LIMIT 1',(project,)).fetchone()
        progress=json.loads(row['result_json'])['progress']
        assert progress['stage']==snapshot['visionStage'] and progress['completed']==0
        stages.append(progress['stage'])
        return provider(config,secret,snapshot,images)
    monkeypatch.setattr(brief_vision,'call_vision',model)
    client.post(f'/api/v1/projects/{project}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    ready=wait(client,project)
    assert ready['visualRun']['status']=='succeeded' and stages==['layout','ocr','summary']


@pytest.mark.parametrize('defect',['quote','ocr-partial'])
def test_real_citation_or_readability_problems_remain_incomplete(client,monkeypatch,defect):
    _,ready,_=run(client,monkeypatch,defect)
    result=ready['visualRun']['result']
    assert ready['visualRun']['status']=='succeeded' and not result['factsComplete']
    if defect=='quote':assert result['factsErrorCode']=='BRIEF_FACT_SOURCE_UNKNOWN'
    else:assert ready['recognizedPages'][1]['readability']=='partial'


def test_crop_uses_normalized_coordinates_and_does_not_read_neighbouring_cell():
    image=Image.new('RGB',(400,200),'red');image.paste('blue',(200,0,400,200))
    buffer=io.BytesIO();image.save(buffer,format='PNG')
    with Image.open(io.BytesIO(crop_region(buffer.getvalue(),[500,0,1000,1000]))) as cropped:
        assert cropped.size==(200,200)
        red,green,blue=cropped.getpixel((20,20))
        assert blue>240 and red<15 and green<15
    with pytest.raises(ValueError):LayoutRegion(id='invalid',label='invalid',role='context',bbox=[-1,0,200,1000])


def test_ordinary_pages_are_batched_at_24_not_6():
    jobs=[({'pageNo':n,'jobId':str(n)},b'x') for n in range(1,28)]
    assert [len(batch) for batch in job_batches(jobs)]==[24,3]
