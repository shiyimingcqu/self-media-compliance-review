"""Rendered PDF pages -> multimodal extraction -> immutable source-linked blocks."""
import base64
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import Field

from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.files_service import get_asset
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from app.modules.video_model.evidence.secrets import cipher

router=APIRouter(tags=['brief-vision'])
BATCH_PAGES=24
MAX_IMAGE_BYTES=20*1024*1024
PROMPT_PATH=Path(__file__).with_name('prompts')/'brief-vision.md'


class VisionInput(ContractModel):
    source_version_id: str
    brief_revision: int = Field(ge=1)
    config_revision: int = Field(ge=1)
    allow_real_call: bool=False
    extract_facts: bool=False


class VisualPage(ContractModel):
    page_no: int = Field(ge=1)
    text: str = Field(max_length=25000)
    visual_description: str = Field(min_length=1,max_length=5000)
    readability: Literal['readable','partial','unreadable']
    uncertainties: list[str] = Field(default_factory=list,max_length=30)


class VisualReport(ContractModel):
    pages: list[VisualPage] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list,max_length=40)


from .vision_facts import PageFacts, verify_batch_facts, merge_facts, store_facts


class VisualFactsReport(VisualReport):
    facts: PageFacts


def checked_result(result):
    """Do not let a model's 'readable' label hide explicit OCR omissions."""
    if not result or not result.get('report'):return result
    result=json.loads(json.dumps(result));pages=result['report']['pages']
    for page in pages:
        text=page['text']+'\n'+page['visualDescription']
        if re.search(r'此处包含大量|文字省略|省略了|略去|未逐字|无法辨认|无法识别|无法看清|难以辨认|文字模糊|小字不清',text):
            note='识读结果包含省略或辨认限制，不能视为逐字完整提取，请对照原页核对'
            if note not in page['uncertainties']:page['uncertainties'].append(note)
            if page['readability']=='readable':page['readability']='partial'
    result['coverageComplete']=all(p['readability']=='readable' and not p['uncertainties'] for p in pages)
    return result


def run_view(row):
    if not row:return None
    return {'id':row['id'],'sourceVersionId':row['source_version_id'],'status':row['status'],'snapshot':json.loads(row['snapshot_json']),'result':checked_result(json.loads(row['result_json'])) if row['result_json'] else None,'errorCode':row['error_code'],'errorMessage':row['error_message'],'createdAt':row['created_at'],'updatedAt':row['updated_at']}


def resolved_pages(db, source_id, run_id):
    """人工已核对（confirmed/irrelevant）且针对当前识读版本的页号。

    只认 snapshot.visionRunId == 本次 run 的记录：Brief 重新识读后 check 会变，
    旧的人工判定不得跨版本继续清覆盖门（与 page_review 的 REVISION_CONFLICT 同口径）。
    """
    pages = set()
    for row in db.execute('SELECT action,snapshot_json FROM brief_page_reviews WHERE source_version_id=?', (source_id,)):
        if row['action'] not in ('confirmed', 'irrelevant'):
            continue
        try:
            snapshot = json.loads(row['snapshot_json'] or '{}')
        except json.JSONDecodeError:
            continue
        if run_id and snapshot.get('visionRunId') != run_id:
            continue
        page_no = snapshot.get('pageNo')
        if isinstance(page_no, int):
            pages.add(page_no)
    return pages


def resolve_material(db,source_id,blocks,uncovered):
    row=db.execute("SELECT * FROM brief_visual_runs WHERE source_version_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1",(source_id,)).fetchone()
    if not row:return blocks,uncovered,None
    pages=checked_result(json.loads(row['result_json']))['report']['pages'];covered=set();merged=list(blocks)
    for p in pages:
        if p['readability']=='readable' and not p['uncertainties']:covered.add(f"page:{p['pageNo']}")
        for kind,key in [('text','text'),('visual','visualDescription')]:
            if p[key].strip():merged.append({'id':f"vision:{row['id']}:{p['pageNo']}:{kind}",'pageNo':p['pageNo'],'text':p[key],'origin':'ai-visual-extraction','visionRunId':row['id']})
    # 品牌在待核对里判定"已确认/无关"的页，视为已覆盖：人工承担责任，验收不再重复拦。
    covered |= {f"page:{n}" for n in resolved_pages(db, source_id, row['id'])}
    return merged,[item for item in uncovered if item not in covered],row['id']


def render_pages(settings,run_id,file_id,expected_sha):
    import pypdfium2 as pdfium
    asset,path=get_asset(settings,file_id)
    payload=path.read_bytes()
    if asset.extension!='pdf' or hashlib.sha256(payload).hexdigest()!=expected_sha:raise ValueError('PDF source integrity')
    root=settings.data_dir/'brief-vision'/run_id;root.mkdir(parents=True,exist_ok=True)
    pages=[]
    with pdfium.PdfDocument(payload) as doc:
        if len(doc)<1:raise ValueError('Empty PDF')
        for index in range(len(doc)):
            page=doc[index]
            try:
                width,height=page.get_size()
                if min(width,height)<=0:raise ValueError('Invalid PDF dimensions')
                bitmap=page.render(scale=2200/max(width,height))
                try:
                    image=bitmap.to_pil().convert('RGB')
                    out=io.BytesIO();image.save(out,format='JPEG',quality=90);data=out.getvalue();size=image.size;image.close()
                finally:bitmap.close()
            finally:page.close()
            filename=f'page-{index+1}.jpg';(root/filename).write_bytes(data)
            pages.append({'pageNo':index+1,'filename':filename,'width':size[0],'height':size[1],'byteSize':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return pages


def page_bytes(settings,run_id,page):
    # Both IDs and page paths come from controlled records, not arbitrary URLs.
    if not run_id.isalnum() or page['filename']!=f"page-{page['pageNo']}.jpg":raise ValueError('Invalid page path')
    root=(settings.data_dir/'brief-vision').resolve();path=(root/run_id/page['filename']).resolve()
    if root not in path.parents:raise ValueError('Invalid page path')
    data=path.read_bytes()
    if len(data)!=page['byteSize'] or hashlib.sha256(data).hexdigest()!=page['sha256']:raise ValueError('Rendered page integrity')
    return data


def image_batches(settings,run_id,pages):
    """Bound each provider request, without limiting the document page count."""
    batch=[];total=0
    for page in pages:
        data=page_bytes(settings,run_id,page)
        encoded_size=4*((len(data)+2)//3)
        if encoded_size>MAX_IMAGE_BYTES:raise ValueError('Single rendered page exceeds 20MiB')
        if batch and (len(batch)>=BATCH_PAGES or total+encoded_size>MAX_IMAGE_BYTES):
            yield batch
            batch=[];total=0
        batch.append((page,data));total+=encoded_size
    if batch:yield batch


def call_vision(config,secret,snapshot,images):
    content=[{'type':'text','text':json.dumps({'documentContext':snapshot.get('documentContext',{}),'pageCount':snapshot['pageCount'],'nativeTextBlocks':snapshot['nativeTextBlocks']},ensure_ascii=False)}]
    for page,data in images:
        content.extend([{'type':'text','text':f"PDF物理第{page['pageNo']}页，pageNo={page['pageNo']}"},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(data).decode()}}])
    with httpx.Client(timeout=httpx.Timeout(300,connect=15,write=60),follow_redirects=False) as client:
        response=client.post(config['endpoint'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+secret},json={'model':config['model'],'stream':False,'reasoning_effort':'none','messages':[{'role':'system','content':snapshot['prompt']['content']+'\nJSON Schema:'+json.dumps((VisualFactsReport if snapshot.get('extractFacts') else VisualReport).model_json_schema(),ensure_ascii=False)},{'role':'user','content':content}]})
    if response.status_code!=200:return {'httpStatus':response.status_code,'report':None}
    data=response.json();raw=data['choices'][0]['message'].get('content') or ''
    result={'providerRequestIds':[data.get('id')],'usage':[data.get('usage')],'modelResponse':raw.replace(secret,'[REDACTED]')[:250000]}
    value=raw.strip()
    if value.startswith('```') and value.endswith('```'):value=value.split('\n',1)[-1].rsplit('```',1)[0].strip()
    try:result['report']=json.loads(value) if len(value)<=250000 else None
    except ValueError:result['report']=None
    return result


def execute_vision(settings,run_id,config,encrypted):
    result={};status='failed';code=None;error=None;secret=''
    try:
        with connection(settings.database_path) as db:
            changed=db.execute("UPDATE brief_visual_runs SET status='processing',updated_at=? WHERE id=? AND status='queued'",(now(),run_id)).rowcount
            if not changed:return
            row=db.execute('SELECT * FROM brief_visual_runs WHERE id=?',(run_id,)).fetchone();snapshot=json.loads(row['snapshot_json'])
            current=db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?',(row['project_id'],)).fetchone()
            if not current or current['source_version_id']!=row['source_version_id']:raise ValueError('Brief changed before execution')
        pages=render_pages(settings,run_id,snapshot['fileId'],snapshot['fileSha256'])
        if len(pages)!=snapshot['pageCount']:raise ValueError('PDF page count changed')
        snapshot['pages']=pages
        with connection(settings.database_path) as db:db.execute('UPDATE brief_visual_runs SET snapshot_json=?,updated_at=? WHERE id=?',(json.dumps(snapshot,ensure_ascii=False),now(),run_id))
        secret=cipher(settings.data_dir).decrypt(encrypted.encode()).decode()
        result={'providerRequestIds':[],'usage':[],'batches':[]}
        combined=[];warnings=[];fact_batches=[];facts_error=None
        for images in image_batches(settings,run_id,pages):
            page_numbers={page['pageNo'] for page,_ in images}
            batch_snapshot={**snapshot,'pageCount':len(images),'nativeTextBlocks':[b for b in snapshot['nativeTextBlocks'] if b.get('pageNo') in page_numbers]}
            batch=call_vision(config,secret,batch_snapshot,images)
            result['batches'].append({'pageNumbers':sorted(page_numbers),**batch})
            result['providerRequestIds'].extend(batch.get('providerRequestIds',[]))
            result['usage'].extend(batch.get('usage',[]))
            if batch.get('httpStatus'):
                result['httpStatus']=batch['httpStatus']
                raise ValueError(f"Provider HTTP {batch['httpStatus']}")
            raw_report=batch.get('report')
            parsed=VisualReport.model_validate({k:v for k,v in raw_report.items() if k!='facts'} if isinstance(raw_report,dict) else raw_report)
            numbers=[p.page_no for p in parsed.pages]
            if len(numbers)!=len(page_numbers) or set(numbers)!=page_numbers:raise ValueError('Missing, duplicated or invented batch page')
            combined.extend(parsed.pages);warnings.extend(parsed.warnings)
            if snapshot.get('extractFacts'):
                try:fact_batches.append(verify_batch_facts(raw_report.get('facts'),parsed.pages))
                except Exception as exc:
                    facts_error=(getattr(exc,'code',None) or 'BRIEF_FACTS_REPORT_INVALID',getattr(exc,'message',None) or 'VLM事实卡结构不完整，图文识读结果保留；未调用其他模型补采')
        report=VisualReport(pages=combined,warnings=list(dict.fromkeys(warnings))[:40])
        numbers=[p.page_no for p in report.pages]
        if len(numbers)!=snapshot['pageCount'] or set(numbers)!=set(range(1,snapshot['pageCount']+1)):raise ValueError('Missing, duplicated or invented page')
        if sum(len(p.text)+len(p.visual_description) for p in report.pages)>100000:raise ValueError('Recognized content exceeds 100000 characters')
        result['report']=report.model_dump(by_alias=True);result['coverageVerified']=False
        result['coverageComplete']=all(p.readability=='readable' and not p.uncertainties for p in report.pages)
        result=checked_result(result)
        if snapshot.get('extractFacts'):
            if not facts_error:
                try:result['facts']=merge_facts(run_id,result['report']['pages'],fact_batches,diagnostics=result)
                except Exception as exc:
                    facts_error=(getattr(exc,'code',None) or 'BRIEF_FACTS_REPORT_INVALID',getattr(exc,'message',None) or 'VLM事实卡合并校验失败；图文识读结果保留')
            if facts_error:result['factsErrorCode'],result['factsErrorMessage']=facts_error
        status='succeeded'
    except httpx.HTTPError as exc:
        code='PROVIDER_SUBMISSION_UNKNOWN';error='多模态解析连接中断或超时，是否计费未知；未自动重试';result['errorType']=type(exc).__name__
    except Exception as exc:
        code=f"PROVIDER_HTTP_{result['httpStatus']}" if result.get('httpStatus') else 'BRIEF_VISION_FAILED'
        error='Brief图文解析失败或页码校验未通过；未自动重试';result['errorType']=type(exc).__name__
    finally:secret=''
    if status!='succeeded':result.pop('report',None)
    with connection(settings.database_path) as db:
        from app.core.timing import stamp_duration
        stamp_duration(db, 'brief_visual_runs', run_id, result)
        if status=='succeeded' and snapshot.get('extractFacts'):
            result['factsRunId']=store_facts(db,row,snapshot,result)
        db.execute('UPDATE brief_visual_runs SET status=?,result_json=?,error_code=?,error_message=?,updated_at=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),code,error,now(),run_id))
        if status=='succeeded':
            from .standards import invalidate_project
            current=db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?',(row['project_id'],)).fetchone()
            if current and current['source_version_id']==row['source_version_id']:invalidate_project(db,row['project_id'],{'briefVisionRunId':run_id},reason='Brief图文识别结果更新，需重新验收')


def start_vision(request,project_id,payload,key,execute_inline=False):
    from .standards import project_for,fingerprint,replay,record
    settings=request.app.state.settings
    if not payload.allow_real_call:raise ApiError(400,'REAL_CALL_CONFIRMATION_REQUIRED','请确认使用已保存模型进行图文解析，可能计费')
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE');project_for(db,project_id);scope='brief-vision:'+project_id;fp=fingerprint(payload)
        prior=replay(db,scope,key,fp)
        if prior:return run_view(db.execute('SELECT * FROM brief_visual_runs WHERE id=?',(prior['id'],)).fetchone())
        current=db.execute('SELECT * FROM project_current_briefs WHERE project_id=?',(project_id,)).fetchone()
        if not current or current['source_version_id']!=payload.source_version_id or current['revision']!=payload.brief_revision:raise ApiError(409,'REVISION_CONFLICT','项目Brief已更新，请刷新后再解析')
        config=db.execute('SELECT * FROM evidence_model_settings WHERE id=1').fetchone()
        if not config or not config['encrypted_key']:raise ApiError(409,'MODEL_KEY_MISSING','请先在模型设置保存Key')
        if config['revision']!=payload.config_revision:raise ApiError(409,'REVISION_CONFLICT','模型设置已更新')
        previous=db.execute("SELECT * FROM brief_visual_runs WHERE source_version_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1",(payload.source_version_id,)).fetchone()
        if previous and checked_result(json.loads(previous['result_json'])).get('coverageComplete') and (not payload.extract_facts or json.loads(previous['result_json']).get('facts')):
            record(db,scope,key,fp,{'id':previous['id']});return run_view(previous)
        if db.execute("SELECT 1 FROM brief_visual_runs WHERE status IN ('queued','processing')").fetchone():raise ApiError(409,'BRIEF_VISION_BUSY','已有Brief图文解析正在进行，请等待完成')
        source=db.execute('SELECT * FROM brief_source_versions WHERE id=?',(payload.source_version_id,)).fetchone()
        asset,path=get_asset(settings,source['file_id'])
        if asset.extension!='pdf':raise ApiError(400,'PDF_REQUIRED','图文解析入口用于PDF；TXT直接使用已提取文字')
        if not source['page_count'] or source['page_count']<1:raise ApiError(400,'BRIEF_VISION_EMPTY_PDF','PDF没有可解析的页面')
        identity=uuid4().hex;content=PROMPT_PATH.read_text(encoding='utf-8').strip()
        if payload.extract_facts:content+='\n'+PROMPT_PATH.with_name('brief-vision-facts.md').read_text(encoding='utf-8').strip()
        blocks=[{'id':b['id'],'pageNo':b['page_no'],'text':b['text']} for b in db.execute('SELECT * FROM brief_text_blocks WHERE source_version_id=? ORDER BY ordinal',(source['id'],))]
        if sum(len(b['text']) for b in blocks)>100000:raise ApiError(400,'BRIEF_INPUT_LIMIT','原文超过10万字符，请拆分')
        snapshot={'sourceVersionId':source['id'],'fileId':asset.id,'fileSha256':asset.sha256,'pageCount':source['page_count'],'nativeTextBlocks':blocks,'pages':[],'model':config['model'],'endpoint':config['endpoint'],'configRevision':config['revision'],'prompt':{'content':content,'version':hashlib.sha256(content.encode()).hexdigest()[:12]},'renderLongEdge':2200,'extractFacts':payload.extract_facts}
        from .brief_import import document_context
        snapshot['documentContext']=document_context(db,project_id,source['id'])
        db.execute('INSERT INTO brief_visual_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(identity,project_id,source['id'],'queued',json.dumps(snapshot,ensure_ascii=False),None,None,None,now(),now()))
        record(db,scope,key,fp,{'id':identity});encrypted=config['encrypted_key'];frozen={'model':config['model'],'endpoint':config['endpoint']}
    try:
        if execute_inline:execute_vision(settings,identity,frozen,encrypted)
        else:request.app.state.brief_vision_executor.submit(execute_vision,settings,identity,frozen,encrypted)
    except Exception:
        with connection(settings.database_path) as db:db.execute("UPDATE brief_visual_runs SET status='failed',error_code='QUEUE_FAILED',error_message='解析任务未能排队，未提交模型',updated_at=? WHERE id=?",(now(),identity))
    with connection(settings.database_path) as db:return run_view(db.execute('SELECT * FROM brief_visual_runs WHERE id=?',(identity,)).fetchone())


@router.post('/projects/{project_id}/brief/vision',response_model=Envelope[dict],status_code=202)
def create_run(project_id:str,payload:VisionInput,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    return Envelope(data=start_vision(request,project_id,payload,idempotency_key),request_id=request.state.request_id)


@router.get('/projects/{project_id}/brief/vision/{run_id}/pages/{page_no}')
def preview_page(project_id:str,run_id:str,page_no:int,request:Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        row=db.execute("SELECT r.* FROM brief_visual_runs r JOIN projects p ON p.id=r.project_id WHERE r.id=? AND r.project_id=? AND p.mode='demo'",(run_id,project_id)).fetchone()
        if not row:raise ApiError(404,'RESOURCE_NOT_FOUND','解析记录不存在')
        page=next((p for p in json.loads(row['snapshot_json'])['pages'] if p['pageNo']==page_no),None)
        if not page:raise ApiError(404,'RESOURCE_NOT_FOUND','该页未生成预览')
    try:data=page_bytes(request.app.state.settings,run_id,page)
    except (ValueError,OSError):raise ApiError(409,'BRIEF_PAGE_UNAVAILABLE','页面文件缺失或校验不符，请核对原文件')
    return Response(data,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})
