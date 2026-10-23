from app.core.database import connection
from app.modules.workflow import standards, brief_vision as vision
from test_brief_vision import prepare, report, wait
from test_project_standards import rules
from test_project_brief import upload
from test_script_versions import K


def test_reparse_same_brief_does_not_break_frozen_citations_or_change_current_ai_material(client,monkeypatch):
    p,brief,body=prepare(client);calls=[]
    def model(*args):
        calls.append(1);value=report(partial=len(calls)==1)
        value['pages'][0]['text']='第一次识读的准确原文' if len(calls)==1 else '第二次识读的新原文'
        return {'report':value}
    monkeypatch.setattr(vision,'call_vision',model)
    path=f'/api/v1/projects/{p}/brief/vision'
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).status_code==202
    first=wait(client,p);old=first['visualRun']['id'];old_id=f'vision:{old}:1:text'
    assert client.post(path,json=body,headers={'Idempotency-Key':K()}).status_code==202
    latest=wait(client,p);assert latest['visualRun']['id']!=old
    draft=rules(client);draft['sourceVersionId']=brief['sourceVersionId']
    draft['scriptRules'][0].update({'sourceBlockId':old_id,'sourceQuote':'第一次识读的准确原文'})
    endpoint=f'/api/v1/projects/{p}/standards/confirm'
    saved=client.post(endpoint,json=draft,headers={'Idempotency-Key':K()})
    assert saved.status_code==200,saved.text
    with connection(client.app.state.settings.database_path) as db:
        blocks,_=standards.source_blocks(db,p,brief['sourceVersionId'])
        assert any('第二次识读的新原文'==b['text'] for b in blocks)
        assert not any(b['id']==old_id for b in blocks)
    # Changed quotations remain invalid, with an actionable rule number.
    draft['revision']=1;draft['scriptRules'][0]['sourceQuote']='第一次识读的准确原文，伪造追加'
    rejected=client.post(endpoint,json=draft,headers={'Idempotency-Key':K()})
    assert rejected.status_code==400
    assert rejected.json()['error']['code']=='INVALID_STANDARD_SOURCE'
    assert rejected.json()['error']['details']=={'scope':'script','ruleNumbers':[1]}
    assert '脚本规则第1条' in rejected.json()['error']['message']
    assert 'scriptRules.0.sourceQuote' in rejected.json()['error']['fieldErrors']
    # Even genuine frozen text from a different Brief cannot be reused by ID.
    replacement,_=upload(client,p,'全新的Brief原文',revision=1)
    draft['sourceVersionId']=replacement.json()['data']['brief']['sourceVersionId']
    draft['scriptRules'][0]['sourceQuote']='第一次识读的准确原文'
    assert client.post(endpoint,json=draft,headers={'Idempotency-Key':K()}).status_code==400
    assert len(calls)==2


def test_failed_visual_run_cannot_supply_citations(client,monkeypatch):
    p,brief,body=prepare(client)
    monkeypatch.setattr(vision,'call_vision',lambda *args:{'report':report()})
    client.post(f'/api/v1/projects/{p}/brief/vision',json=body,headers={'Idempotency-Key':K()})
    run=wait(client,p)['visualRun']['id']
    with connection(client.app.state.settings.database_path) as db:db.execute("UPDATE brief_visual_runs SET status='failed' WHERE id=?",(run,))
    draft=rules(client);draft['sourceVersionId']=brief['sourceVersionId']
    draft['scriptRules'][0].update({'sourceBlockId':f'vision:{run}:1:text','sourceQuote':'第1页：品牌产品名称，禁止镜像。'})
    response=client.post(f'/api/v1/projects/{p}/standards/confirm',json=draft,headers={'Idempotency-Key':K()})
    assert response.status_code==400,response.text
