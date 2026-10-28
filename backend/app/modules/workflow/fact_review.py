"""Versioned human fact review; provider replies remain immutable."""
import copy
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from pydantic import Field
from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from .brief_facts import FACT_GROUPS,validate_facts,facts_view
from .standards import project_for,source_blocks,fingerprint,replay,record
from .vision_facts import source_quote

router=APIRouter(tags=['brief-facts'])


class FactDecision(ContractModel):
    item_index:int=Field(ge=0)
    action:Literal['accept','ignore','brand_primary','brand_related','brand_alias']
    target_brand:str|None=Field(default=None,max_length=500)
    value:str|None=Field(default=None,min_length=1,max_length=500)
    quote:str|None=Field(default=None,min_length=1,max_length=3000)
    page_no:int|None=Field(default=None,ge=1)
    source:Literal['text','visual']|None=None


class FactReviewInput(ContractModel):
    source_version_id:str
    base_run_id:str
    decisions:list[FactDecision]=Field(min_length=1,max_length=30)


def review(settings,project_id,payload,key):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE');project=project_for(db,project_id)
        scope='brief-facts-review:'+project_id;fp=fingerprint(payload);prior=replay(db,scope,key,fp)
        if prior:return facts_view(db.execute('SELECT * FROM brief_facts_runs WHERE id=?',(prior['id'],)).fetchone())
        if project['status']=='archived':raise ApiError(409,'PROJECT_ARCHIVED','归档项目不可修改')
        current=db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?',(project_id,)).fetchone()
        row=db.execute('SELECT * FROM brief_facts_runs WHERE project_id=? AND source_version_id=? ORDER BY rowid DESC LIMIT 1',(project_id,payload.source_version_id)).fetchone()
        if not current or current['source_version_id']!=payload.source_version_id or not row or row['id']!=payload.base_run_id:
            raise ApiError(409,'REVISION_CONFLICT','Brief或事实卡已更新，请刷新后再确认')
        snapshot=json.loads(row['snapshot_json']);result=json.loads(row['result_json'] or '{}')
        if row['status']!='succeeded' or not result.get('facts'):raise ApiError(409,'FACTS_NOT_READY','暂无可编辑的事实卡')
        blocks,_=source_blocks(db,project_id,payload.source_version_id);lookup={b['id']:b['text'] for b in blocks}
        pending=result.get('pendingFacts',[]);facts=copy.deepcopy(result['facts'])
        entities=facts.setdefault('brandEntities',[])
        if facts.get('brand',{}).get('value') and not any(e['role']=='primary' for e in entities):
            entities.insert(0,{'name':copy.deepcopy(facts['brand']),'aliases':[],'role':'primary'})
        indices=[d.item_index for d in payload.decisions]
        if len(set(indices))!=len(indices) or any(index>=len(pending) for index in indices):raise ApiError(409,'REVISION_CONFLICT','待核对条目已变化，请刷新')
        applied=[]
        for decision in payload.decisions:
            item=pending[decision.item_index]
            if decision.action=='ignore':
                applied.append({'item':item,'action':'ignore'});continue
            page=decision.page_no or item.get('pageNo');kind=decision.source or item.get('source','text')
            block_id=item.get('blockId') if not decision.page_no and not decision.source else None
            block_id=block_id or f"vision:{snapshot.get('visionRunId')}:{page}:{kind}"
            quote=source_quote(decision.quote or item.get('quote',''),lookup.get(block_id,''))
            if quote is None:raise ApiError(400,'FACT_CITATION_INVALID','引文无法在所选原页连续核对，请编辑引文或页码后再确认')
            entry={'value':decision.value or item['value'],'blockId':block_id,'quote':quote}
            if item.get('applicability') and not any((decision.value,decision.quote,decision.page_no,decision.source)):
                entry['applicability']=copy.deepcopy(item['applicability'])
            elif item.get('applicability'):
                from .fact_scope import FactApplicability
                entry['applicability']=FactApplicability().model_dump(by_alias=True)
            if decision.action.startswith('brand_'):
                if item['group']!='brands':raise ApiError(400,'FACT_ACTION_INVALID','品牌操作仅用于品牌候选')
                if decision.action=='brand_alias':
                    target=next((e for e in entities if e['name']['value']==decision.target_brand),None)
                    conflict=next((e for e in entities if e is not target and (e['name']['value']==entry['value'] or any(a['value']==entry['value'] for a in e['aliases']))),None)
                    if conflict:raise ApiError(409,'BRAND_RELATION_CONFLICT','该名称已属于另一个品牌实体，请核对品牌关系，不能直接合并不同品牌')
                    if not target:raise ApiError(400,'BRAND_TARGET_REQUIRED','请先选择已确认的主品牌或关联品牌，再添加别名')
                    if entry['value']!=target['name']['value'] and not any(a['value']==entry['value'] for a in target['aliases']):target['aliases'].append(entry)
                else:
                    target=next((e for e in entities if e['name']['value']==entry['value']),None)
                    if target is None:
                        target={'name':entry,'aliases':[],'role':'related'};entities.append(target)
                    if decision.action=='brand_primary':
                        for entity in entities:entity['role']='related'
                        target['role']='primary';facts['brand']=entry
                    else:
                        if target['role']=='primary':raise ApiError(400,'PRIMARY_BRAND_REQUIRED','请先选择其他主品牌，再将当前主品牌设为关联品牌')
                        target['role']='related'
            else:
                if item['group'] not in FACT_GROUPS:raise ApiError(400,'FACT_ACTION_INVALID','请为品牌候选选择主品牌、别名或关联品牌')
                group=facts.setdefault(item['group'],[])
                if not any(old['value']==entry['value'] for old in group):group.append(entry)
            applied.append({'item':item,'action':decision.action,'accepted':entry,'targetBrand':decision.target_brand})
        remaining=[item for index,item in enumerate(pending) if index not in set(indices)]
        warnings=[w for w in facts.get('warnings',[]) if not w.startswith(('品牌名称存在多个候选','品牌名缺少有效出处')) and not re.match(r'(有\d+条事实的出处待核对|仍有\d+条事实待核对)',w)]
        if remaining:warnings.insert(0,f'仍有{len(remaining)}条事实待核对，未自动用于生成规则。')
        if not facts.get('brand',{}).get('value'):warnings.insert(0,'主品牌尚未确认，请选择主品牌。')
        facts['warnings']=warnings[:40]
        facts=validate_facts(facts,blocks,allow_missing_brand=True).model_dump(by_alias=True)
        result['facts']=facts;result['pendingFacts']=remaining
        result['factsComplete']=not remaining and bool(facts['brand']['value']) and result.get('factsCoverage',{}).get('complete',True)
        result.setdefault('humanReviews',[]).append({'at':now(),'decisions':applied})
        identity=uuid4().hex;stamp=now()
        frozen={**snapshot,'modelRole':'human-review','parentRunId':row['id'],'billingOrigin':'human-no-model-call'}
        db.execute('INSERT INTO brief_facts_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,project_id,payload.source_version_id,'succeeded',json.dumps(frozen,ensure_ascii=False),json.dumps(result,ensure_ascii=False),None,None,stamp,stamp))
        record(db,scope,key,fp,{'id':identity})
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',(uuid4().hex,'local-reviewer','project',project_id,'brief_facts_reviewed','demo',json.dumps({'factsRunId':identity,'parentRunId':row['id'],'decisionCount':len(applied),'modelCalled':False}),stamp))
        return facts_view(db.execute('SELECT * FROM brief_facts_runs WHERE id=?',(identity,)).fetchone())


@router.post('/projects/{project_id}/brief/facts/review',response_model=Envelope[dict])
def review_facts(project_id:str,payload:FactReviewInput,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=review(request.app.state.settings,project_id,payload,idempotency_key),request_id=request.state.request_id)
