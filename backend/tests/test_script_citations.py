import copy
import json

import httpx
import pytest

from app.core.errors import ApiError
from app.modules.workflow import standards
from app.modules.workflow.script_citations import prepare_report, source_catalog
from app.modules.workflow.script_defaults import SCRIPT_DIMENSIONS


BLOCKS = [{'id':'b1','pageNo':1,'text':'不得比价。不可拉踩竞品。产品在前15秒出现。不可产品镜像。'}]


def candidate(refs, category='品牌安全', **extra):
    return {'text':'不得拉踩竞品','criterion':'检查是否贬低竞品。','category':category,
            'importance':'must','sourceRefs':refs,**extra}


def test_catalog_uses_stable_ids_and_only_verified_literal_fragments():
    catalog=source_catalog(BLOCKS, {'value':'概括','blockId':'other','quote':'伪造出处'})
    assert catalog == source_catalog(BLOCKS, None)
    assert len(catalog)==4 and len({r['ref'] for r in catalog})==4
    assert all(r['quote'] in BLOCKS[0]['text'] for r in catalog)
    facts={'value':'概括','blockId':'b1','quote':'不可拉踩竞品。'}
    compact=source_catalog(None, facts, available_blocks=BLOCKS)
    assert len(compact)==1 and compact[0] in catalog


def test_two_selected_references_render_independently_and_preserve_eight_dimensions():
    catalog=source_catalog(BLOCKS, None)
    raw={'rules':[candidate([catalog[1]['ref'],catalog[0]['ref']])],'warnings':[]}
    original=copy.deepcopy(raw)
    report=prepare_report(raw,BLOCKS,None,catalog)
    assert raw==original
    assert [r.category for r in report.rules[:8]]==[r[0] for r in SCRIPT_DIMENSIONS]
    rule=next(r for r in report.rules if r.text=='不得拉踩竞品')
    assert rule.citation_status=='verified'
    assert '政治、宗教' in rule.criterion  # 润色补充不得把同维度的基础检查删掉
    assert [r.quote for r in rule.source_references]==['不可拉踩竞品。','不得比价。']
    assert rule.source_quote=='不可拉踩竞品。'  # 不把多个引用拼成伪造连续原文
    standards.check_quotes(report.rules,BLOCKS,'script')


@pytest.mark.parametrize('refs',[['invented'],[]])
def test_unresolved_rule_is_retained_disabled_without_replacing_baseline(refs):
    catalog=source_catalog(BLOCKS,None)
    report=prepare_report({'rules':[candidate(refs)],'warnings':[]},BLOCKS,None,catalog)
    assert len(report.rules)==9
    pending=report.rules[-1]
    assert pending.citation_status=='pending' and not pending.enabled
    assert pending.source_quote=='' and pending.source_block_id==''
    assert report.rules[2].origin=='template'
    standards.check_quotes(report.rules,BLOCKS,'script')
    pending.enabled=True
    with pytest.raises(ApiError):standards.check_quotes(report.rules,BLOCKS,'script')
    pending.citation_confirmed=True
    standards.check_quotes(report.rules,BLOCKS,'script')
    assert pending.citation_status=='pending'  # 品牌采用不等于出处被验证


def test_one_bad_reference_does_not_discard_other_valid_rules():
    catalog=source_catalog(BLOCKS,None)
    report=prepare_report({'rules':[candidate([catalog[1]['ref']]),candidate(['bad'],'合作要求达成',text='开头展示产品')],'warnings':[]},BLOCKS,None,catalog)
    assert any(r.citation_status=='verified' and r.enabled for r in report.rules)
    assert len([r for r in report.rules if r.citation_status=='pending'])==1
    assert len(report.rules)==9


def test_legacy_reversed_newline_quotes_recover_only_exact_unique_parts():
    legacy={'text':'不得拉踩竞品','criterion':'不得比价或贬低。','category':'品牌安全','importance':'must',
            'sourceBlockId':'b1','sourceQuote':'不可拉踩竞品。\n不得比价。'}
    raw={'rules':[legacy],'warnings':[]}
    report=prepare_report(raw,BLOCKS,None,[],legacy=True)
    repaired=next(r for r in report.rules if r.text==legacy['text'])
    assert repaired.source_quote=='不得比价。不可拉踩竞品。'
    assert repaired.citation_status=='verified' and '本地恢复' in ''.join(report.warnings)
    assert raw['rules'][0]['sourceQuote']=='不可拉踩竞品。\n不得比价。'
    legacy['sourceQuote']='不可拉踩其他品牌。\n不得比价。'
    pending=prepare_report(raw,BLOCKS,None,[],legacy=True).rules[-1]
    assert pending.citation_status=='pending' and not pending.enabled


def test_legacy_other_brief_and_ambiguous_quotes_are_not_guessed():
    for block_id,text in [('other',BLOCKS[0]['text']),('b1',BLOCKS[0]['text']+'不可拉踩竞品。')]:
        raw={'rules':[{'text':'规则','criterion':'要求','sourceBlockId':block_id,'sourceQuote':'不可拉踩竞品。\n不得比价。'}]}
        report=prepare_report(raw,[{**BLOCKS[0],'text':text}],None,[],legacy=True)
        assert report.rules[-1].citation_status=='pending'


def test_provider_schema_only_accepts_reference_ids_not_raw_quote_fields(monkeypatch):
    catalog=source_catalog(BLOCKS,None);bodies=[];actual=httpx.Client
    def provider(request):
        body=json.loads(request.content);bodies.append(body)
        schema=body['response_format']['json_schema']['schema']
        props=schema['$defs']['ScriptRuleCandidate']['properties']
        assert 'sourceRefs' in props and 'sourceQuote' not in props and 'sourceBlockId' not in props
        assert 'citationStatus' not in props and 'origin' not in props
        assert props['sourceRefs']['items']['enum']==[r['ref'] for r in catalog]
        content=json.loads(body['messages'][1]['content'])
        assert content['sourceReferences']==catalog and 'blocks' not in content
        return httpx.Response(200,json={'id':'fixed','choices':[{'message':{'content':'{"rules":[],"warnings":[]}'}}]})
    monkeypatch.setattr(standards.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(provider),**kw))
    standards.call_recommendation({'endpoint':'https://example.com/v1','model':'qwen3.8-flash'},'fake',
                                 {'key':'brief-script-standards','content':'选择编号'},BLOCKS,[],source_refs=catalog)
    assert len(bodies)==1


def test_partial_generation_replay_save_and_local_recovery_never_resubmit(client,monkeypatch):
    from test_project_brief import upload
    from test_project_standards import rules as confirmation
    from test_separate_standards import project
    from test_script_versions import K
    p=project(client)['id'];uploaded,_=upload(client,p,BLOCKS[0]['text'])
    brief=uploaded.json()['data']['brief']
    configured=client.put('/api/v1/settings/models/decomposition',json={'endpoint':'https://example.com/v1','model':'qwen3.8-flash','revision':0,'apiKey':'test-fixture-key'})
    assert configured.status_code==200,configured.text
    calls=[]
    def model(config,secret,prompt,blocks,uncovered,facts=None,source_refs=None):
        calls.append(source_refs)
        raw={'rules':[candidate([source_refs[0]['ref']]),candidate(['unknown'],'合作要求达成',text='无出处的定制项')],'warnings':[]}
        return {'report':raw,'modelResponse':json.dumps(raw),'providerRequestIds':['paid-once']}
    monkeypatch.setattr(standards,'call_recommendation',model)
    path=f'/api/v1/projects/{p}/standard-recommendations';key=K()
    body={'sourceVersionId':brief['sourceVersionId'],'scope':'script','configRevision':1,'allowRealCall':True}
    response=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    run=response.json()['data']
    assert run['status']=='succeeded',run
    assert run['snapshot']['sourceReferences']==calls[0]
    assert run['snapshot']['recommendationVersion']=='script_refs_v1'
    assert run['result']['report']['rules'][-1]['citationStatus']=='pending'
    client.post(path,json=body,headers={'Idempotency-Key':key})
    assert len(calls)==1
    saved=confirmation(client);saved['sourceVersionId']=brief['sourceVersionId']
    saved['scriptRules']=run['result']['report']['rules']
    save_path=f'/api/v1/projects/{p}/standards/confirm'
    response=client.post(save_path,json=saved,headers={'Idempotency-Key':K()})
    assert response.status_code==200,response.text
    frozen=client.get(f'/api/v1/projects/{p}/standards').json()['data']['script']['rules']
    assert frozen[-1]['enabled'] is False and frozen[-1]['citationStatus']=='pending'
    assert any(r['sourceReferences'] for r in frozen)
    saved['revision']=1;saved['scriptRules'][-1]['enabled']=True
    assert client.post(save_path,json=saved,headers={'Idempotency-Key':K()}).status_code==400
    saved['scriptRules'][-1]['citationConfirmed']=True
    assert client.post(save_path,json=saved,headers={'Idempotency-Key':K()}).status_code==200
    def forbidden(*args,**kw):raise AssertionError('本地恢复不能重调模型')
    monkeypatch.setattr(standards,'call_recommendation',forbidden)
    recovered=client.post(path+'/'+run['id']+'/revalidate',headers={'Idempotency-Key':K()})
    assert recovered.status_code==200,recovered.text
    assert recovered.json()['data']['result']['modelResponse']==run['result']['modelResponse']
