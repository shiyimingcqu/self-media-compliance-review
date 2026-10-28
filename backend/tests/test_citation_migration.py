import json
import sqlite3
from copy import deepcopy

import pytest

from app.core.database import connection
from app.modules.workflow.citation_migration import migrate_citations
from app.modules.workflow.standards import StandardRule
from test_project_standards import rules
from test_project_brief import upload
from test_separate_standards import project
from test_script_versions import K


OLD='a'*32
NEW='b'*32
QUOTE='产品须在前15秒出现'


@pytest.mark.parametrize('defect',[None,'foreign_project','changed','different_page','forged_old','ambiguous','wrong_kind','failed_old'])
def test_only_owned_exact_same_page_citations_migrate(defect):
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE brief_visual_runs (id TEXT,project_id TEXT,source_version_id TEXT,status TEXT,result_json TEXT)')
    old_text='其他内容' if defect=='forged_old' else QUOTE
    db.execute('INSERT INTO brief_visual_runs VALUES (?,?,?,?,?)',(OLD,'other' if defect=='foreign_project' else 'p','old','failed' if defect=='failed_old' else 'succeeded',json.dumps({'report':{'pages':[{'pageNo':20,'text':old_text}]}})))
    block=f'vision:{OLD}:20:text'
    rule=StandardRule(text='产品露出',criterion='前15秒出现',sourceBlockId=block,sourceQuote=QUOTE,
                      sourceReferences=[{'ref':'src_old','blockId':block,'quote':QUOTE}])
    target=f'vision:{NEW}:20:'+('visual' if defect=='wrong_kind' else 'text')
    blocks=[{'id':target,'text':'产品须在前30秒出现' if defect=='changed' else QUOTE,'pageNo':21 if defect=='different_page' else 20}]
    if defect=='ambiguous':blocks.append({'id':f'vision:{"c"*32}:20:text','text':QUOTE,'pageNo':20})
    original=rule.model_dump()
    result=migrate_citations(db,'p','new',[rule],blocks)
    if defect:
        assert not result and rule.model_dump()==original
    else:
        assert rule.source_block_id==target and rule.source_references[0].block_id==target
        assert rule.source_references[0].ref!='src_old'
        assert result[id(rule)]['bindings'][0]['fromBlockId']==block
        assert rule.source_quote==QUOTE
        assert not migrate_citations(db,'p','new',[rule],blocks)


def test_save_migrates_freezes_trace_and_replay_preserves_old_history(client):
    p=project(client)['id']
    first,_=upload(client,p,QUOTE)
    old_source=first.json()['data']['brief']['sourceVersionId']
    second,_=upload(client,p,QUOTE+'。新增说明。',revision=1)
    new_source=second.json()['data']['brief']['sourceVersionId']
    def report(text):
        return json.dumps({'report':{'pages':[{'pageNo':20,'text':text,'visualDescription':'视频要求表格','readability':'readable','uncertainties':[]}]}})
    with connection(client.app.state.settings.database_path) as db:
        for identity,source,text in [(OLD,old_source,QUOTE),(NEW,new_source,QUOTE+'。新增说明。')]:
            db.execute('INSERT INTO brief_visual_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,p,source,'succeeded','{}',report(text),None,None,'2026-10-25','2026-10-25'))
    body=rules(client);body['sourceVersionId']=new_source
    body['videoRules']=[{'text':'产品在前15秒出现','criterion':'前15秒须展示产品','importance':'must','sourceBlockId':f'vision:{OLD}:20:text','sourceQuote':QUOTE,'origin':'ai'}]
    original=deepcopy(body);path=f'/api/v1/projects/{p}/standards/confirm';key=K()
    response=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    view=client.get(f'/api/v1/projects/{p}/standards').json()['data']
    saved=view['video']['rules'][0]
    assert saved['sourceBlockId']==f'vision:{NEW}:20:text'
    assert saved['source']['sourceVersionId']==new_source
    assert saved['source']['citationMigration']['method']=='same_page_exact_quote_v1'
    assert body==original
    replay=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert replay.status_code==200 and replay.json()['data']==response.json()['data']
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM project_standard_sets WHERE project_id=? AND scope='video'",(p,)).fetchone()[0]==1
        assert json.loads(db.execute('SELECT result_json FROM brief_visual_runs WHERE id=?',(OLD,)).fetchone()[0])['report']['pages'][0]['text']==QUOTE
