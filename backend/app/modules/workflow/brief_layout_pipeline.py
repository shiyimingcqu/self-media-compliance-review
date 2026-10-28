"""Layout routing -> full-page or cropped OCR -> one document summary."""
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic import ValidationError
import httpx

from app.core.contracts import ContractModel
from app.core.errors import ApiError
from app.core.database import connection
from .brief_structure import VisualRegion
from .fact_scope import FactApplicability
from .vision_facts import PageFacts, verify_batch_facts, merge_facts

PIPELINE_VERSION = 'layout-crop-summary-v2'
PROMPTS = Path(__file__).with_name('prompts')


class LayoutRegion(ContractModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    row_header: str = Field(default='', max_length=200)
    column_header: str = Field(default='', max_length=200)
    bbox: list[int] = Field(min_length=4, max_length=4)
    container_id: str | None = Field(default=None,max_length=80)
    role: Literal['requirement', 'reference', 'context']
    applicability: FactApplicability = Field(default_factory=FactApplicability)

    @model_validator(mode='after')
    def valid_box(self):
        x1,y1,x2,y2=self.bbox
        if not (0<=x1<x2<=1000 and 0<=y1<y2<=1000):
            raise ValueError('bbox must be ordered normalized coordinates in 0..1000')
        return self


class LayoutPage(ContractModel):
    page_no: int = Field(ge=1)
    reading_mode: Literal['sequential','regions']
    reason: str = Field(default='', max_length=300)
    regions: list[LayoutRegion] = Field(default_factory=list, max_length=40)

    @model_validator(mode='after')
    def valid_regions(self):
        if self.reading_mode=='sequential' and self.regions:
            raise ValueError('sequential pages must not have crop regions')
        if self.reading_mode=='regions' and not self.regions:
            raise ValueError('nonsequential pages require crop regions')
        ids=[r.id for r in self.regions]
        if len(ids)!=len(set(ids)):raise ValueError('region IDs must be unique per page')
        return self


class LayoutReport(ContractModel):
    pages: list[LayoutPage] = Field(min_length=1)


class OcrItem(ContractModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=25000)
    visual_description: str = Field(default='', max_length=500)
    readability: Literal['readable','partial','unreadable']
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class OcrReport(ContractModel):
    items: list[OcrItem] = Field(min_length=1)


class SummaryReport(ContractModel):
    facts: PageFacts


def crop_region(data, bbox):
    """Crop only a validated box. No padding into a neighbouring table cell."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        x1,y1,x2,y2=bbox
        box=(int(x1*image.width/1000),int(y1*image.height/1000),
             min(image.width,(x2*image.width+999)//1000),min(image.height,(y2*image.height+999)//1000))
        if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Crop is empty at source resolution')
        with image.crop(box) as cropped:
            output=io.BytesIO()
            with cropped.convert('RGB') as rgb:rgb.save(output,format='JPEG',quality=95)
            return output.getvalue()


def job_batches(jobs, max_pages=24, max_bytes=20*1024*1024):
    batch=[];total=0
    for job in jobs:
        size=4*((len(job[1])+2)//3)
        if size>max_bytes:raise ValueError('Single OCR image exceeds request budget')
        if batch and (len(batch)>=max_pages or total+size>max_bytes):
            yield batch;batch=[];total=0
        batch.append(job);total+=size
    if batch:yield batch


def saved_ocr(settings,snapshot):
    identity=snapshot.get('reuseOcrFrom')
    if not identity:return None
    with connection(settings.database_path) as db:
        row=db.execute("SELECT * FROM brief_visual_runs WHERE id=? AND status='succeeded' AND source_version_id=?",
                       (identity,snapshot['sourceVersionId'])).fetchone()
    if not row:raise ApiError(409,'BRIEF_OCR_REUSE_INVALID','原识读版本不可复用，请刷新后重试')
    source=json.loads(row['snapshot_json']);result=json.loads(row['result_json'])
    if source.get('fileSha256')!=snapshot['fileSha256'] or source.get('pipelineVersion')!=PIPELINE_VERSION or not result.get('coverageComplete'):
        raise ApiError(409,'BRIEF_OCR_REUSE_INVALID','原识读资料或布局版本不一致，请重新识别')
    return result


def run_pipeline(settings, run_id, snapshot, config, secret, pages, call_model, read_page, checkpoint):
    # Raw stage responses are retained separately. Only final summary facts go
    # into cards; crop transcriptions are never auto-expanded into fact items.
    result={'pipelineVersion':PIPELINE_VERSION,'batches':[],'providerRequestIds':[],'usage':[]}

    def phase(stage, batches, schema, prompt, payload_for):
        batches=list(batches);outputs=[];failure=None
        result['progress']={'stage':stage,'completed':0,'total':len(batches)}
        checkpoint(result)
        def invoke(batch):
            child={**snapshot,'extractFacts':False,'visionStage':stage,
                   'responseSchema':schema.model_json_schema(),
                   'prompt':{'content':prompt},'pageCount':len(batch),
                   'nativeTextBlocks':[],'stagePayload':payload_for(batch)}
            return call_model(config,secret,child,batch)
        # Independent requests only; at most two in flight, never automatic retries.
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='brief-'+stage) as pool:
            futures={pool.submit(invoke,batch):index for index,batch in enumerate(batches)}
            for future in as_completed(futures):
                index=futures[future];batch=batches[index]
                numbers=sorted({p['pageNo'] for p,_ in batch})
                saved={'stage':stage,'batchIndex':index,'pageNumbers':numbers}
                try:
                    response=future.result()
                    saved={'stage':stage,'batchIndex':index,'pageNumbers':numbers,**response}
                    result['providerRequestIds'].extend(response.get('providerRequestIds',[]))
                    result['usage'].extend(response.get('usage',[]))
                    if response.get('httpStatus'):
                        raise ApiError(502,'PROVIDER_HTTP_'+str(response['httpStatus']),'模型服务返回 HTTP '+str(response['httpStatus'])+'；未自动重试')
                    if response.get('report') is None:
                        raise ApiError(400,'BRIEF_VISION_JSON_INVALID','模型未返回有效 JSON（'+stage+'阶段）；已保留返回，未自动重试')
                    raw=response['report']
                    if isinstance(raw,list) and stage in ('layout','ocr'):
                        raw={('pages' if stage=='layout' else 'items'):raw}
                    parsed=schema.model_validate(raw)
                    if stage=='layout' and (len(parsed.pages)!=len(numbers) or {p.page_no for p in parsed.pages}!=set(numbers)):
                        raise ApiError(400,'BRIEF_VISION_PAGE_MISMATCH','布局返回页码与本批原页不一致；未自动重试')
                    if stage=='ocr' and (len(parsed.items)!=len(batch) or {p.id for p in parsed.items}!={meta['jobId'] for meta,_ in batch}):
                        raise ApiError(400,'BRIEF_OCR_ID_MISMATCH','识读编号与本批裁图不对应；未自动重试')
                    outputs.append((index,parsed))
                except Exception as exc:
                    saved={**saved,'stage':stage,'batchIndex':index,'pageNumbers':numbers,'errorType':type(exc).__name__}
                    if failure is None:failure=exc
                result['batches'].append(saved)
                result['progress']['completed']+=1
                checkpoint(result)
                # No subsequent phase starts if any request or validation failed.
        if failure is not None:raise failure
        return [parsed for _,parsed in sorted(outputs,key=lambda item:item[0])]

    try:
        reused=saved_ocr(settings,snapshot)
        if reused:
            from .brief_vision import VisualReport
            recognized=VisualReport.model_validate(reused['report']).pages
            result['layout']=reused.get('layout',[])
            result['reusedOcrFrom']=snapshot['reuseOcrFrom']
        else:
            from .brief_geometry import pdf_geometry,layout_candidates,refine_page
            geometry=pdf_geometry(settings,snapshot)
            full=[({'pageNo':page['pageNo'],'jobId':f"page:{page['pageNo']}"},read_page(settings,run_id,page)) for page in pages]
            layouts=[]
            for report in phase('layout',job_batches(full),LayoutReport,snapshot['stagePrompts']['layout'],
                                lambda batch:{'pages':[meta['pageNo'] for meta,_ in batch],
                                              'geometryCandidates':{str(meta['pageNo']):layout_candidates(geometry,meta['pageNo']) for meta,_ in batch}}):
                layouts.extend(report.pages)
            expected={p['pageNo'] for p in pages}
            if len(layouts)!=len(expected) or {p.page_no for p in layouts}!=expected:
                raise ApiError(400,'BRIEF_VISION_PAGE_MISMATCH','布局定位返回页码缺失、重复或超出原页；未自动重试')
            layouts.sort(key=lambda p:p.page_no)
            result['rawLayout']=[p.model_dump(by_alias=True) for p in layouts]
            refined=[];adjustments=[]
            for page in layouts:
                page,changes=refine_page(page,geometry)
                refined.append(page);adjustments.extend({'pageNo':page.page_no,**change} for change in changes)
            layouts=refined
            result['geometryAdjustments']=adjustments
            result['layout']=[p.model_dump(by_alias=True) for p in layouts]
            checkpoint(result)
            originals={meta['pageNo']:data for meta,data in full};jobs=[]
            for page in layouts:
                if page.reading_mode=='sequential':
                    jobs.append(({'pageNo':page.page_no,'jobId':f'page:{page.page_no}'},originals[page.page_no]))
                else:
                    for region in page.regions:
                        jobs.append(({'pageNo':page.page_no,'jobId':f'page:{page.page_no}:{region.id}',
                                      'label':region.label,'rowHeader':region.row_header,'columnHeader':region.column_header},
                                     crop_region(originals[page.page_no],region.bbox)))
            ocr={}
            for report in phase('ocr',job_batches(jobs),OcrReport,snapshot['stagePrompts']['ocr'],
                                lambda batch:{'regions':[meta for meta,_ in batch]}):
                for item in report.items:
                    if item.id in ocr:raise ApiError(400,'BRIEF_OCR_ID_MISMATCH','区域识读返回重复编号；未自动重试')
                    ocr[item.id]=item
            if set(ocr)!={meta['jobId'] for meta,_ in jobs}:
                raise ApiError(400,'BRIEF_OCR_ID_MISMATCH','区域识读返回编号缺失或与裁图不对应；未自动重试')
            from .brief_vision import VisualPage
            recognized=[]
            for page in layouts:
                items=[];regions=[];texts=[];descriptions=[]
                if page.reading_mode=='sequential':
                    item=ocr[f'page:{page.page_no}'];items=[item];texts=[item.text];descriptions=[item.visual_description or '顺序读取页面']
                else:
                    for region in page.regions:
                        item=ocr[f'page:{page.page_no}:{region.id}'];items.append(item)
                        header=' / '.join(dict.fromkeys(filter(None,[region.row_header,region.column_header,region.label])))
                        texts.append('【'+header+'】\n'+item.text)
                        if item.visual_description:descriptions.append(header+'：'+item.visual_description)
                        regions.append(VisualRegion(id=region.id,rowHeader=region.row_header,columnHeader=region.column_header,
                                      role=region.role,applicability=region.applicability,text=item.text,bbox=region.bbox))
                uncertainty=[f'{item.id}：{note}' for item in items for note in item.uncertainties][:30]
                readability='readable' if all(item.readability=='readable' for item in items) and not uncertainty else 'partial'
                if all(item.readability=='unreadable' for item in items):readability='unreadable'
                recognized.append(VisualPage(pageNo=page.page_no,text='\n\n'.join(texts),
                                  visualDescription=('\n'.join(descriptions) or '分区裁图识读')[:5000],
                                  readability=readability,uncertainties=uncertainty,regions=regions,structureComplete=True))
        if sum(len(p.text)+len(p.visual_description) for p in recognized)>100000:
            raise ApiError(400,'BRIEF_INPUT_LIMIT','识读文字超过10万字符，请拆分资料；未提交摘要模型')
        result['report']={'pages':[p.model_dump(by_alias=True) for p in recognized],'warnings':[]}
        checkpoint(result)
        # One text-only request after all OCR. No per-page/per-region facts.
        result['coverageVerified']=False
        result['coverageComplete']=all(p.readability=='readable' and not p.uncertainties for p in recognized)
        try:
            from .brief_summary_sources import ReferencedSummary,summary_sources,resolve_summary
            sources=summary_sources(recognized)
            result['summarySourceCount']=len(sources)
            summary=phase('summary',[[]],ReferencedSummary,snapshot['stagePrompts']['summary'],
                          lambda _: {'sources':sources,'documentContext':snapshot.get('documentContext',{})})[0]
            resolved=resolve_summary(summary,sources)
            checked=verify_batch_facts(resolved.model_dump(by_alias=True),recognized,require_scope=True)
            result['facts']=merge_facts(run_id,result['report']['pages'],[checked],diagnostics=result)
        except (ApiError,ValidationError,httpx.HTTPError) as exc:
            result['factsErrorCode']=getattr(exc,'code','BRIEF_FACTS_REPORT_INVALID')
            result['factsErrorMessage']='全文重点汇总未完成，已保留页面与区域识读结果；未自动重试'
            result['factsErrorType']=type(exc).__name__
            result['factsComplete']=False
            result['progress']['stage']='summary_failed'
            checkpoint(result)
            return result
        result['factsComplete']=result.get('factsComplete',False) and result['coverageComplete']
        result['progress']={'stage':'complete','completed':len(recognized),'total':len(recognized)}
        checkpoint(result)
        return result
    except Exception:
        # Preserve completed stages and raw replies even when a later stage fails.
        checkpoint(result)
        raise
