"""Facts emitted alongside VLM page transcription; no second model call."""
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import Field, PrivateAttr
from app.core.contracts import ContractModel
from app.core.errors import ApiError
from app.modules.foundation.service import now
from .brief_facts import FACT_GROUPS, validate_facts
from .fact_scope import FactApplicability, validate_scope


class PageFact(ContractModel):
    value: str = Field(min_length=1,max_length=500)
    page_no: int = Field(ge=1)
    source: Literal['text','visual']
    quote: str = Field(min_length=1,max_length=3000)
    applicability: FactApplicability | None = None


class PageBrandEntity(ContractModel):
    name: PageFact
    aliases: list[PageFact] = Field(default_factory=list,max_length=30)
    role: Literal['primary','related']


class PageFacts(ContractModel):
    # A batch may have no brand; the document must have a source-backed brand.
    brands: list[PageFact] = Field(default_factory=list,max_length=30)
    brand_entities: list[PageBrandEntity] = Field(default_factory=list,max_length=30)
    background: str = Field(default='',max_length=2000)
    products: list[PageFact] = Field(default_factory=list,max_length=30)
    competitors: list[PageFact] = Field(default_factory=list,max_length=30)
    selling_points: list[PageFact] = Field(default_factory=list,max_length=30)
    required_terms: list[PageFact] = Field(default_factory=list,max_length=30)
    forbidden_terms: list[PageFact] = Field(default_factory=list,max_length=30)
    publish_rules: list[PageFact] = Field(default_factory=list,max_length=30)
    thresholds: list[PageFact] = Field(default_factory=list,max_length=30)
    freeform: list[PageFact] = Field(default_factory=list,max_length=30)
    warnings: list[str] = Field(default_factory=list,max_length=40)
    _pending: list[dict] = PrivateAttr(default_factory=list)
    _repairs: list[dict] = PrivateAttr(default_factory=list)
    _coverage: list[dict] | None = PrivateAttr(default=None)


def source_quote(quote,text):
    """Resolve whitespace-only differences to an actual contiguous source span."""
    if not quote.strip():return None
    if quote in text:return quote
    positions=[i for i,char in enumerate(text) if not char.isspace()]
    compact=''.join(text[i] for i in positions)
    needle=''.join(char for char in quote if not char.isspace())
    offset=compact.find(needle)
    if offset<0:return None
    return text[positions[offset]:positions[offset+len(needle)-1]+1]


def verify_batch_facts(value,pages,require_scope=False,require_structure=False):
    missing_summary=value is None and require_structure
    if missing_summary:
        value={'warnings':['模型未返回事实汇总，仅保留区域清单中可核对的要求；品牌与产品等背景事实可能缺失，请核对。']}
    facts=PageFacts.model_validate(value)
    lookup={p.page_no:p for p in pages}
    for key in ('brands',*FACT_GROUPS):
        attr=re.sub(r'([A-Z])',lambda m:'_'+m[1].lower(),key)
        kept=[]
        for item in getattr(facts,attr):
            page=lookup.get(item.page_no)
            canonical=source_quote(item.quote,page.text if item.source=='text' else page.visual_description) if page else None
            if canonical is None:
                facts._pending.append({'group':key,**item.model_dump(by_alias=True),
                    'reason':'引用页码不属于本批页面' if not page else '引文无法在对应页面连续核对，可能存在省略、拼接或改写'})
                continue
            if canonical!=item.quote:
                facts._repairs.append({'group':key,'pageNo':item.page_no,'value':item.value,
                    'originalQuote':item.quote,'quote':canonical,'method':'whitespace-only'})
                item=item.model_copy(update={'quote':canonical})
            if require_scope and item.applicability is None:
                item=item.model_copy(update={'applicability':FactApplicability()})
            if item.applicability is not None:
                checked_scope=validate_scope(item.applicability.model_dump(by_alias=True),[page.text,page.visual_description])
                item=item.model_copy(update={'applicability':FactApplicability.model_validate(checked_scope)})
            kept.append(item)
        setattr(facts,attr,kept)
    verified_entities=[]
    for entity in facts.brand_entities:
        checked=[]
        for item in [entity.name,*entity.aliases]:
            page=lookup.get(item.page_no)
            canonical=source_quote(item.quote,page.text if item.source=='text' else page.visual_description) if page else None
            if canonical is None:
                facts._pending.append({'group':'brands',**item.model_dump(by_alias=True),'reason':'品牌名称或别名的出处待核对'})
                checked.append(None)
            else:checked.append(item.model_copy(update={'quote':canonical}))
        if checked[0]:verified_entities.append(entity.model_copy(update={'name':checked[0],'aliases':[item for item in checked[1:] if item]}))
    facts.brand_entities=verified_entities
    if require_structure:
        reconcile_regions(facts,pages)
        if missing_summary:
            for entry in facts._coverage:
                entry['complete']=False
                entry['issues'].append('模型未返回事实汇总，区域要求已保留，其他事实待核对')
    return facts


def reconcile_regions(facts,pages):
    """Do not lose a requirement between VLM's region ledger and its summary facts."""
    from .brief_structure import unmapped_bullets
    facts._coverage=[]
    for page in pages:
        issues=[];verified_quotes=[];seen=set();requirements=0;recovered=0
        if not page.structure_complete or not page.regions:
            issues.append('未提供完整的原页区域清单，不能确认要求已全部入卡')
        for region in page.regions:
            if region.id in seen:issues.append('区域编号重复，请核对分区')
            seen.add(region.id)
            scope=validate_scope(region.applicability.model_dump(by_alias=True),[page.text,page.visual_description])
            scope['region']=' / '.join(filter(None,[region.row_header,region.column_header,scope.get('region','')]))[:300]
            if region.role=='requirement' and not region.items:
                issues.append('要求区域为空：'+region.id)
            for item in region.items:
                canonical=source_quote(item.quote,page.text if item.source=='text' else page.visual_description)
                if canonical is not None:verified_quotes.append(canonical)
                if region.role!='requirement':
                    if canonical is None:issues.append('参考或背景区域引文无法核对：'+region.id)
                    continue
                requirements+=1
                entry={'group':item.group,'value':item.value,'pageNo':page.page_no,'source':item.source,'quote':canonical or item.quote,'applicability':scope}
                if canonical is None:
                    facts._pending.append({**entry,'reason':'区域要求引文无法在本页连续核对'})
                    issues.append('要求引文无法核对：'+region.id)
                    continue
                attr=re.sub(r'([A-Z])',lambda m:'_'+m[1].lower(),item.group)
                group=getattr(facts,attr)
                existing=next((old for old in group if old.page_no==page.page_no and old.source==item.source and old.quote==canonical and old.value==item.value),None)
                if existing:
                    existing.applicability=FactApplicability.model_validate(scope)
                    continue
                if len(group)>=30:
                    facts._pending.append({**entry,'reason':'区域要求超过本分组容量，已保留待人工核对'})
                    issues.append('要求超过分组容量，待核对')
                    continue
                group.append(PageFact(value=item.value,pageNo=page.page_no,source=item.source,quote=canonical,applicability=scope))
                recovered+=1
        missing=unmapped_bullets(page.text,verified_quotes)
        if missing:issues.append(f'有{len(missing)}条识读条目未被区域清单覆盖，例如：'+missing[0][:100])
        facts._coverage.append({'pageNo':page.page_no,'complete':not issues and page.readability=='readable' and not page.uncertainties,
                                'regionCount':len(page.regions),'requirementCount':requirements,'recoveredFacts':recovered,
                                'unmappedItems':missing,'issues':list(dict.fromkeys(issues))})


def merge_facts(run_id,pages,batches,diagnostics=None):
    groups={key:[] for key in ('brands',*FACT_GROUPS)};warnings=[];background=[]
    pending=[item for batch in batches for item in batch._pending]
    repairs=[item for batch in batches for item in batch._repairs]
    if diagnostics is not None:diagnostics.update(pendingFacts=pending,quoteRepairs=repairs,factsComplete=not pending)
    coverage=[entry for batch in batches for entry in (batch._coverage or [])]
    if coverage:
        from .brief_structure import STRUCTURE_VERSION
        complete=all(entry['complete'] for entry in coverage)
        if diagnostics is not None:
            diagnostics['factsCoverage']={'version':STRUCTURE_VERSION,'complete':complete,'pages':coverage}
            diagnostics['factsComplete']=not pending and complete
        if not complete:warnings.append('部分页面的区域要求尚未全部核对入卡，请查看待核对中的具体页码；已验证事实仍可使用。')
    if pending:warnings.append(f'有{len(pending)}条事实的出处待核对，已单独保留，未用于自动生成规则；其他有效事实可继续使用。')
    for facts in batches:
        data=facts.model_dump(by_alias=True)
        if data['background'] and data['background'] not in background:background.append(data['background'])
        for warning in data['warnings']:
            if warning not in warnings:warnings.append(warning)
        for key in groups:
            for item in data[key]:
                entry={'value':item['value'],'blockId':f"vision:{run_id}:{item['pageNo']}:{item['source']}",'quote':item['quote'],'applicability':item.get('applicability')}
                if not any(old['value']==entry['value'] and old.get('applicability')==entry['applicability'] for old in groups[key]):
                    if len(groups[key])>=30:
                        pending.append({'group':key,**item,'reason':'跨页合并后超出分组容量，已保留待人工核对'})
                    else:groups[key].append(entry)
    entities=[]
    def cited(item):return {'value':item.value,'blockId':f'vision:{run_id}:{item.page_no}:{item.source}','quote':item.quote}
    for batch in batches:
        for entity in batch.brand_entities:
            existing=next((old for old in entities if old['name']['value']==entity.name.value and old['role']==entity.role),None)
            if existing is None:
                existing={'name':cited(entity.name),'aliases':[],'role':entity.role};entities.append(existing)
            for alias in entity.aliases:
                if not any(old['value']==alias.value for old in existing['aliases']):existing['aliases'].append(cited(alias))
    primary=[entity for entity in entities if entity['role']=='primary']
    assigned={item['value'] for entity in entities for item in [entity['name'],*entity['aliases']]}
    brands=[item for item in groups.pop('brands') if item['value'] not in assigned]
    if len(primary)==1:
        for item in brands:
            pending.append({'group':'brands','value':item['value'],'pageNo':int(item['blockId'].rsplit(':',2)[1]),'source':item['blockId'].rsplit(':',1)[1],'quote':item['quote'],'reason':'名称尚未归入已识别的品牌实体，请确认关系'})
        if brands and diagnostics is not None:diagnostics.update(pendingFacts=pending,factsComplete=False)
        brands=[primary[0]['name']]
    elif len(primary)>1:
        brands.extend(item for entity in entities for item in [entity['name'],*entity['aliases']]);entities=[]
        brands=list({item['value']:item for item in brands}.values())

    if len(brands)>1:
        warnings.append('品牌名称存在多个候选，未自动选择：'+'；'.join(item['value'] for item in brands))
        for item in brands:
            pending.append({'group':'brands','value':item['value'],'pageNo':int(item['blockId'].rsplit(':',2)[1]),'source':item['blockId'].rsplit(':',1)[1],'quote':item['quote'],'reason':'存在多个品牌候选，需要品牌确认主品牌'})
        brands=[]
        if diagnostics is not None:diagnostics.update(pendingFacts=pending,factsComplete=False)
    required={item['value'] for item in groups['requiredTerms']}
    for item in groups['forbiddenTerms']:
        if item['value'] in required:warnings.append('同一词句同时列为必提和禁用，请品牌确认：'+item['value'])
    numeric={}
    for item in groups['thresholds']:
        label=re.split(r'[=<>≥≤：:]',item['value'],maxsplit=1)[0].strip()
        if label and label!=item['value']:
            if label in numeric and numeric[label]!=item['value']:warnings.append('数值要求存在多个版本，请核对适用范围：'+numeric[label]+'；'+item['value'])
            numeric[label]=item['value']
    if any(p['readability']!='readable' or p['uncertainties'] for p in pages):warnings.append('部分页面识读不完整，事实卡可能遗漏要求，请对照原件确认。')
    blocks=[{'id':f"vision:{run_id}:{p['pageNo']}:{kind}",'text':p[key]} for p in pages for kind,key in [('text','text'),('visual','visualDescription')]]
    if not brands and not entities and not any(groups.values()):raise ApiError(400,'BRIEF_FACTS_CITATION_MISMATCH','没有取得可核对的事实，请对照原件补充')
    value={'brand':brands[0] if brands else {'value':'','blockId':'','quote':''},'brandEntities':entities,'background':'\n'.join(background)[:2000],**groups,'warnings':list(dict.fromkeys(warnings))[:40]}
    if not brands:
        value['warnings'].insert(0,'品牌名缺少有效出处，待品牌补充；其他有据事实已保留。')
        if diagnostics is not None:diagnostics['factsComplete']=False
    if diagnostics is not None:
        diagnostics['pendingFacts']=pending
        if pending:diagnostics['factsComplete']=False
        overflow_pages={item.get('pageNo') for item in pending if '容量' in item.get('reason','')}
        for entry in coverage:
            if entry['pageNo'] in overflow_pages:
                entry['complete']=False
                entry['issues'].append('部分区域要求超出事实分组容量，已保留待核对')
        if coverage:diagnostics['factsCoverage']['complete']=all(entry['complete'] for entry in coverage)
    if pending:
        value['warnings']=[w for w in value['warnings'] if not re.match(r'有\d+条事实的出处待核对',w)]
        value['warnings'].insert(0,f'有{len(pending)}条事实的出处待核对，已单独保留，未用于自动生成规则；其他有效事实可继续使用。')
    value['warnings']=value['warnings'][:40]
    return validate_facts(value,blocks,allow_missing_brand=True).model_dump(by_alias=True)


def store_facts(db,vision_row,snapshot,result):
    """Same transaction as visual result; reuse ID if replayed, no billing here."""
    for row in db.execute('SELECT id,snapshot_json,status,result_json FROM brief_facts_runs WHERE source_version_id=?',(vision_row['source_version_id'],)):
        saved=json.loads(row['snapshot_json'])
        output=json.loads(row['result_json'] or '{}')
        if saved.get('visionRunId')==vision_row['id'] and saved.get('factValidationVersion')=='whitespace-v2' and row['status']==('succeeded' if result.get('facts') else 'failed') and all(output.get(key)==result.get(key) for key in ('facts','pendingFacts','factsComplete','factsCoverage')):return row['id']
    identity=uuid4().hex;stamp=now();success=bool(result.get('facts'))
    frozen={'factValidationVersion':'whitespace-v2','modelRole':'vision','visionRunId':vision_row['id'],'sourceVersionId':vision_row['source_version_id'],
            'model':snapshot['model'],'configRevision':snapshot['configRevision'],'prompt':snapshot['prompt'],
            'billingOrigin':'shared-vision-call','requestOptions':{'reasoningEffort':'none','readTimeoutSeconds':300,'maxAttempts':1}}
    if snapshot.get('factScopeVersion'):frozen['factScopeVersion']=snapshot['factScopeVersion']
    if snapshot.get('structureVersion'):frozen['structureVersion']=snapshot['structureVersion']
    if snapshot.get('pipelineVersion'):frozen['pipelineVersion']=snapshot['pipelineVersion']
    output={key:result[key] for key in ('facts','providerRequestIds','usage','pendingFacts','quoteRepairs','factsComplete','factsCoverage','directSummary') if key in result}
    if not success:output['errorCode']=result.get('factsErrorCode','BRIEF_FACTS_REPORT_INVALID')
    error=None if success else result.get('factsErrorMessage','VLM未返回有效事实卡；图文识读结果保留，未调用其他模型补采')
    db.execute('INSERT INTO brief_facts_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,vision_row['project_id'],vision_row['source_version_id'],
               'succeeded' if success else 'failed',json.dumps(frozen,ensure_ascii=False),json.dumps(output,ensure_ascii=False),None,error,stamp,stamp))
    return identity


def revalidate_saved_facts(settings,run_id):
    """Derive a new fact card from preserved model output; never call a provider."""
    from app.core.database import connection
    from .brief_vision import parse_visual_report,normalize_visual_report
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT * FROM brief_visual_runs WHERE id=? AND status='succeeded'",(run_id,)).fetchone()
        if not row:return None
        current=db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?',(row['project_id'],)).fetchone()
        if not current or current['source_version_id']!=row['source_version_id']:return None
        result=json.loads(row['result_json']);snapshot=json.loads(row['snapshot_json'])
        if not snapshot.get('extractFacts') or not result.get('batches'):return None
        if result.get('factsValidation',{}).get('version')=='whitespace-v2' and result.get('facts'):return result
        batches=[]
        try:
            for batch in result['batches']:
                report=normalize_visual_report(batch['report'])
                pages=parse_visual_report(report).pages
                if {p.page_no for p in pages}!=set(batch['pageNumbers']):return None
                batches.append(verify_batch_facts(report.get('facts'),pages,require_scope=bool(snapshot.get('factScopeVersion')),require_structure=bool(snapshot.get('structureVersion'))))
            facts=merge_facts(run_id,result['report']['pages'],batches,diagnostics=result)
        except (ValueError,ApiError,KeyError,TypeError):return None
        result['facts']=facts
        result['factsValidation']={'version':'whitespace-v2','derivedFromSavedOutput':True,
                                  'previousErrorCode':result.pop('factsErrorCode',None),'previousErrorMessage':result.pop('factsErrorMessage',None),'validatedAt':now()}
        result['factsRunId']=store_facts(db,row,snapshot,result)
        db.execute('UPDATE brief_visual_runs SET result_json=?,updated_at=? WHERE id=?',(json.dumps(result,ensure_ascii=False),now(),run_id))
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',(uuid4().hex,'local-reviewer','project',row['project_id'],'brief_facts_revalidated','demo',json.dumps({'visionRunId':run_id,'factsRunId':result['factsRunId'],'validationVersion':'whitespace-v2','modelCalled':False}),now()))
        return result
