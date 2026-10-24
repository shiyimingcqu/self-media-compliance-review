import json

import httpx
import pytest

from app.core.database import connection
from app.modules.workflow import standards
from test_script_versions import task_with_rule, K


def test_generation_disables_reasoning_and_allows_long_response_without_retry(monkeypatch):
    actual=httpx.Client;calls=[]
    def provider(request):
        calls.append(request)
        body=json.loads(request.content)
        assert body['reasoning_effort']=='none' and body['stream'] is False
        assert request.extensions['timeout']['read']==600  # 规则生成单独放宽，事实卡提取仍 300
        assert standards.RECOMMENDATION_READ_TIMEOUT==300 and standards.RULE_GENERATION_READ_TIMEOUT==600
        assert request.extensions['timeout']['connect']==15
        assert request.extensions['timeout']['write']==60
        assert body['messages'][1]['role']=='user'
        return httpx.Response(200,json={'id':'mock-generation','choices':[{'message':{'content':json.dumps({'rules':[],'warnings':[]})}}]})
    monkeypatch.setattr(standards.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    response=standards.call_recommendation({'endpoint':'https://example.com/v1','model':'omni'},'test-secret',{'key':'brief-script-standards','content':'生成规则'},[{'id':'b1','text':'品牌原文'}],[])
    assert response['providerRequestIds']==['mock-generation']
    assert len(calls)==1


@pytest.mark.parametrize('exception,code',[ (httpx.ReadTimeout,'PROVIDER_READ_TIMEOUT'), (httpx.ConnectError,'PROVIDER_CONNECTION_INTERRUPTED') ])
def test_failure_diagnostics_are_saved_and_idempotent_replay_never_resubmits(client,monkeypatch,exception,code):
    project,task=task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source=db.execute('SELECT source_version_id FROM rule_versions WHERE id=?',(task['ruleVersionId'],)).fetchone()[0]
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'omni','reasoningEffort':'none','revision':0,'apiKey':'test-secret'})
    calls=[]
    def fail(*args):
        calls.append(1);raise exception('test-secret must not appear in diagnostics')
    monkeypatch.setattr(standards,'call_recommendation',fail)
    path=f"/api/v1/projects/{project['id']}/standard-recommendations";key=K()
    body={'sourceVersionId':source,'scope':'script','configRevision':1,'allowRealCall':True}
    response=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    data=response.json()['data'];assert data['status']=='failed'
    assert data['result']['errorCode']==code
    assert data['result']['errorType']==exception.__name__
    assert 'test-secret' not in json.dumps(data)
    assert data['snapshot']['requestOptions']['reasoningEffort']=='none'
    assert '未自动重试' in data['errorMessage']
    replay=client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']
    assert replay['id']==data['id'] and len(calls)==1


def test_strict_models_do_not_receive_the_schema_twice(monkeypatch):
    actual=httpx.Client;bodies=[]
    def provider(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'mock','choices':[{'message':{'content':json.dumps({'rules':[],'warnings':[]})}}]})
    monkeypatch.setattr(standards.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    standards.call_recommendation({'endpoint':'https://example.com/v1','model':'qwen3.8-flash'},'test-secret',
                                  {'key':'brief-script-standards','content':'生成规则'},[{'id':'b1','text':'品牌原文'}],[])
    body=bodies[0]
    assert body['response_format']['type']=='json_schema'
    assert body['messages'][0]['content']=='生成规则'


def _schema_bodies(monkeypatch, template_key):
    actual=httpx.Client;bodies=[]
    def provider(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'mock','choices':[{'message':{'content':json.dumps({'rules':[],'warnings':[]})}}]})
    monkeypatch.setattr(standards.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    standards.call_recommendation({'endpoint':'https://example.com/v1','model':'qwen3.8-flash'},'test-secret',
                                  {'key':template_key,'content':'生成规则'},[{'id':'b1','text':'品牌原文'}],[])
    schema=bodies[0]['response_format']['json_schema']['schema']
    properties=schema['$defs']['StandardRule']['properties']
    return properties, schema


def test_script_generation_schema_has_no_video_layer_fields(monkeypatch):
    properties,schema=_schema_bodies(monkeypatch,'brief-script-standards')
    assert 'criterion' in properties
    for field in ('layer','enabled','baselineKey','presetKey','componentPlan'):
        assert field not in properties, field
    text=json.dumps(schema,ensure_ascii=False)
    assert 'ComponentPlan' not in text  # 未被引用的 $defs 一并清掉


def test_video_generation_schema_keeps_layer_fields(monkeypatch):
    properties,_=_schema_bodies(monkeypatch,'brief-video-standards')
    for field in ('layer','enabled','baselineKey','presetKey','componentPlan'):
        assert field in properties, field


def test_http_rejection_preserves_redacted_response_and_does_not_retry(monkeypatch):
    actual=httpx.Client; calls=[]
    def provider(request):
        calls.append(request)
        return httpx.Response(401,headers={'x-request-id':'rejected-1'},json={'error':{'message':'Invalid test-secret; Bearer another-secret'}})
    monkeypatch.setattr(standards.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    result=standards.call_recommendation({'endpoint':'https://example.com/v1','model':'omni'},'test-secret',
        {'key':'brief-script-standards','content':'生成规则'},[{'id':'b','text':'原文'}],[])
    assert result['errorCode']=='PROVIDER_HTTP_401'
    assert result['providerRequestIds']==['rejected-1']
    assert 'test-secret' not in result['providerResponse'] and 'another-secret' not in result['providerResponse']
    assert result['report'] is None and len(calls)==1


@pytest.mark.parametrize('status',[401,403,413,429,500])
def test_http_failure_never_becomes_report_validation_error(client,monkeypatch,status):
    project,task=task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        source=db.execute('SELECT source_version_id FROM rule_versions WHERE id=?',(task['ruleVersionId'],)).fetchone()[0]
    client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'omni','revision':0,'apiKey':'test-secret'})
    calls=[]
    def fail(*args):
        calls.append(1);return {'httpStatus':status,'report':None,'providerResponse':'masked error'}
    monkeypatch.setattr(standards,'call_recommendation',fail)
    path=f"/api/v1/projects/{project['id']}/standard-recommendations";key=K()
    body={'sourceVersionId':source,'scope':'script','configRevision':1,'allowRealCall':True}
    first=client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']
    assert first['status']=='failed' and first['result']['errorCode']==f'PROVIDER_HTTP_{status}'
    assert first['result']['providerResponse']=='masked error'
    assert '无法通过重新校验恢复' in first['errorMessage'] and '格式或引用校验失败' not in first['errorMessage']
    assert client.post(path,json=body,headers={'Idempotency-Key':key}).json()['data']['id']==first['id']
    assert len(calls)==1


def test_old_http_failure_is_reclassified_on_read_without_mutating_original():
    row={'id':'old','scope':'script','status':'failed','snapshot_json':'{}','result_json':json.dumps({'httpStatus':401,'errorCode':'RECOMMENDATION_REPORT_INVALID','errorType':'ValidationError'}),'error_message':'格式失败；已保留返回内容','created_at':'old','updated_at':'old'}
    original=row['result_json'];value=standards.recommendation_view(row)
    assert value['result']['errorCode']=='PROVIDER_HTTP_401'
    assert value['result']['storedErrorType']=='ValidationError'
    assert '认证失败' in value['errorMessage']
    assert row['result_json']==original
