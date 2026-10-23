import json

import pytest

from app.core.database import connection
from app.core.errors import ApiError
from app.modules.workflow import standards
from test_project_brief import upload
from test_separate_standards import project
from test_script_versions import K


def rule(block,quote):
    return {'text':'脚本安排品牌露出','criterion':'检查品牌名称','importance':'suggested','sourceBlockId':block,'sourceQuote':quote}


def test_provable_citation_repairs_and_manual_validation_remains_strict():
    blocks=[{'id':'fragment','pageNo':1,'text':'品牌产品'}, {'id':'full','pageNo':1,'text':'品牌产品名称准确。禁止遮挡商标。'}]
    original={'rules':[rule('fragment','品牌产品名称准确。'),rule('full','品牌产品...禁止遮挡商标。')],'warnings':[]}
    output=standards.validate_recommendation(original,blocks)
    assert output.rules[0].source_block_id=='full'
    assert output.rules[1].source_quote==blocks[1]['text']
    assert len(output.warnings)==2
    assert original['rules'][1]['sourceQuote']=='品牌产品...禁止遮挡商标。'
    with pytest.raises(ApiError):standards.check_quotes(standards.RecommendedStandards.model_validate(original).rules,blocks)
    for block,quote in [('missing','品牌产品名称准确。'),('full','品牌产品非常有效'),('full','禁止遮挡商标。...品牌产品')]:
        with pytest.raises(ApiError):standards.validate_recommendation({'rules':[rule(block,quote)],'warnings':[]},blocks)
    # Repeated matching blocks cannot silently select an arbitrary citation.
    with pytest.raises(ApiError):standards.validate_recommendation({'rules':[rule('fragment','品牌产品名称准确。')],'warnings':[]},blocks+[{'id':'duplicate','pageNo':1,'text':blocks[1]['text']}])


def test_local_revalidation_retains_failed_record_never_calls_model_or_confirms_rules(client,monkeypatch):
    p=project(client)['id'];uploaded,_=upload(client,p,'品牌产品名称准确。禁止遮挡商标。')
    brief=uploaded.json()['data']['brief'];block=brief['blocks'][0]['id']
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'mock','revision':0,'apiKey':'test-secret'})
    raw=json.dumps({'rules':[rule(block,'品牌产品...禁止遮挡商标。')],'warnings':[]})
    monkeypatch.setattr(standards,'call_recommendation',lambda *args:{'report':json.loads(raw),'modelResponse':raw,'providerRequestIds':['already-paid']})
    path=f'/api/v1/projects/{p}/standard-recommendations'
    record=client.post(path,json={'sourceVersionId':brief['sourceVersionId'],'scope':'script','configRevision':1,'allowRealCall':True},headers={'Idempotency-Key':K()}).json()['data']
    # Simulate a saved response rejected by the former strict generation path.
    with connection(client.app.state.settings.database_path) as db:
        result=record['result'];result.pop('report')
        db.execute("UPDATE standard_recommendations SET status='failed',result_json=?,error_message='旧引用校验失败' WHERE id=?",(json.dumps(result),record['id']))
    def no_model(*args):raise AssertionError('Local recovery must not call provider')
    monkeypatch.setattr(standards,'call_recommendation',no_model)
    url=path+'/'+record['id']+'/revalidate';key=K()
    recovered=client.post(url,headers={'Idempotency-Key':key})
    assert recovered.status_code==200,recovered.text
    data=recovered.json()['data']
    assert data['id']!=record['id'] and data['status']=='succeeded'
    assert data['result']['localRevalidation']
    assert data['result']['modelResponse']==raw
    assert data['result']['report']['rules'][0]['sourceQuote']=='品牌产品名称准确。禁止遮挡商标。'
    assert client.post(url,headers={'Idempotency-Key':key}).json()['data']['id']==data['id']
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute('SELECT status FROM standard_recommendations WHERE id=?',(record['id'],)).fetchone()[0]=='failed'
        assert db.execute('SELECT COUNT(*) FROM project_standard_sets WHERE project_id=?',(p,)).fetchone()[0]==0
    other=client.post('/api/v1/projects',json={'name':'另一个项目','brandName':'另一个品牌','platforms':['douyin']},headers={'Idempotency-Key':K()}).json()['data']['id']
    assert client.post(url.replace(p,other),headers={'Idempotency-Key':K()}).status_code==404
    upload(client,p,'全新要求',revision=1)
    assert client.post(url,headers={'Idempotency-Key':K()}).status_code==409
