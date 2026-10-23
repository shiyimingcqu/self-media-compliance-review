"""Durable upload -> visual extraction -> source-linked facts orchestration."""
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only

router=APIRouter(tags=['brief-processing'])
ACTIVE=('queued','vision','facts')


def view(row):
    if not row:return None
    return {'id':row['id'],'sourceVersionId':row['source_version_id'],'status':row['status'],
            'visionRunId':row['vision_run_id'],'factsRunId':row['facts_run_id'],
            'errorCode':row['error_code'],'errorMessage':row['error_message'],'createdAt':row['created_at'],'updatedAt':row['updated_at']}


def latest(db,source_id):
    return view(db.execute('SELECT * FROM brief_processing_runs WHERE source_version_id=? ORDER BY rowid DESC LIMIT 1',(source_id,)).fetchone())


def create(db,project_id,source_id):
    active=db.execute("SELECT id FROM brief_processing_runs WHERE source_version_id=? AND status IN ('queued','vision','facts')",(source_id,)).fetchone()
    if active:return active['id'],False
    identity=uuid4().hex;stamp=now()
    db.execute('INSERT INTO brief_processing_runs VALUES (?,?,?,?,?,?,?,?,?,?)',
               (identity,project_id,source_id,'queued',None,None,None,None,stamp,stamp))
    return identity,True


def update(settings,identity,status,code=None,message=None,vision=None,facts=None):
    with connection(settings.database_path) as db:
        db.execute('UPDATE brief_processing_runs SET status=?,error_code=?,error_message=?,vision_run_id=COALESCE(?,vision_run_id),facts_run_id=COALESCE(?,facts_run_id),updated_at=? WHERE id=?',
                   (status,code,message,vision,facts,now(),identity))


def current(settings,project_id,source_id):
    with connection(settings.database_path) as db:
        row=db.execute('SELECT c.source_version_id,p.status FROM project_current_briefs c JOIN projects p ON p.id=c.project_id WHERE c.project_id=?',(project_id,)).fetchone()
    return bool(row and row['source_version_id']==source_id and row['status']!='archived')


def execute(settings,identity):
    from . import brief_vision,brief_facts
    try:
        with connection(settings.database_path) as db:
            row=db.execute('SELECT * FROM brief_processing_runs WHERE id=?',(identity,)).fetchone()
            if not row or row['status']!='queued':return
            project_id=row['project_id'];source_id=row['source_version_id']
            source=db.execute('SELECT * FROM brief_source_versions WHERE id=?',(source_id,)).fetchone()
            current_row=db.execute('SELECT revision FROM project_current_briefs WHERE project_id=?',(project_id,)).fetchone()
            vision_config=db.execute('SELECT * FROM evidence_model_settings WHERE id=1').fetchone()
            facts_config=db.execute('SELECT * FROM decomposition_model_settings WHERE id=1').fetchone()
            cached_vision=db.execute("SELECT * FROM brief_visual_runs WHERE source_version_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1",(source_id,)).fetchone()

        if not current(settings,project_id,source_id):
            update(settings,identity,'superseded');return
        if cached_vision and not json.loads(cached_vision['result_json']).get('facts'):
            from .vision_facts import revalidate_saved_facts
            repaired=revalidate_saved_facts(settings,cached_vision['id'])
            if repaired:
                with connection(settings.database_path) as db:cached_vision=db.execute('SELECT * FROM brief_visual_runs WHERE id=?',(cached_vision['id'],)).fetchone()
            else:cached_vision=None
        # Check only the credentials required by the PDF or TXT path before billing.
        if not source['page_count'] and (not facts_config or not facts_config['encrypted_key']):
            raise ApiError(409,'DECOMPOSITION_MODEL_KEY_MISSING','请配置 Brief 解构模型 Key 后继续自动处理')
        if source['page_count'] and not cached_vision and (not vision_config or not vision_config['encrypted_key']):
            raise ApiError(409,'MODEL_KEY_MISSING','请配置图文解析模型 Key 后继续自动处理')
        if source['page_count']:
            update(settings,identity,'vision')
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
            if cached_vision:
                # Successful extraction with readability warnings is reusable; missing coverage stays explicit.
                result=brief_vision.run_view(cached_vision)
            else:
                result=brief_vision.start_vision(request,project_id,brief_vision.VisionInput(
                    source_version_id=source_id,brief_revision=current_row['revision'],
                    config_revision=vision_config['revision'],allow_real_call=True,extract_facts=True),'pipeline-vision-'+identity,execute_inline=True)
            update(settings,identity,'vision',vision=result['id'])
            if result['status']!='succeeded':
                update(settings,identity,'failed',result.get('errorCode'),result.get('errorMessage') or '图文解析未完成，未继续提取事实卡');return
            if not current(settings,project_id,source_id):
                update(settings,identity,'superseded');return
            visual_result=result.get('result') or {}
            facts_id=visual_result.get('factsRunId')
            if not visual_result.get('facts') or not facts_id:
                update(settings,identity,'failed',visual_result.get('factsErrorCode') or 'BRIEF_FACTS_REPORT_INVALID',
                       visual_result.get('factsErrorMessage') or 'VLM未返回可用事实卡；图文识读已保留，未调用语言模型补采',facts=facts_id);return
            update(settings,identity,'succeeded',facts=facts_id)
            return
        if not current(settings,project_id,source_id):
            update(settings,identity,'superseded');return
        update(settings,identity,'facts')
        result=brief_facts.extract(settings,project_id,brief_facts.FactsInput(
            source_version_id=source_id,config_revision=facts_config['revision'],allow_real_call=True),'pipeline-facts-'+identity)
        if not current(settings,project_id,source_id):
            update(settings,identity,'superseded',facts=result['id']);return
        update(settings,identity,'succeeded' if result['status']=='succeeded' else 'failed',
               (result.get('result') or {}).get('errorCode'),result.get('errorMessage'),facts=result['id'])
    except ApiError as exc:
        update(settings,identity,'blocked',exc.code,exc.message)
    except Exception as exc:
        update(settings,identity,'failed','BRIEF_PROCESSING_FAILED','自动处理未完成，请核对阶段记录后继续；未自动重试（'+type(exc).__name__+'）')


def submit(request,identity):
    try:request.app.state.brief_processing_executor.submit(execute,request.app.state.settings,identity)
    except Exception:update(request.app.state.settings,identity,'failed','QUEUE_FAILED','任务未能排队，未发起自动调用')


class ProcessInput(ContractModel):
    source_version_id:str


@router.post('/projects/{project_id}/brief/process',response_model=Envelope[dict],status_code=202)
def process(project_id:str,payload:ProcessInput,request:Request,idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request)
    from .standards import fingerprint,replay,record,project_for
    settings=request.app.state.settings
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE');project_for(db,project_id)
        source=db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?',(project_id,)).fetchone()
        if not source or source['source_version_id']!=payload.source_version_id:raise ApiError(409,'REVISION_CONFLICT','Brief已更新，请刷新')
        scope='brief-process:'+project_id;fp=fingerprint(payload);prior=replay(db,scope,idempotency_key,fp)
        if prior:identity=prior['id'];created=False
        else:
            identity,created=create(db,project_id,payload.source_version_id)
            record(db,scope,idempotency_key,fp,{'id':identity})
    if created:submit(request,identity)
    with connection(settings.database_path) as db:result=view(db.execute('SELECT * FROM brief_processing_runs WHERE id=?',(identity,)).fetchone())
    return Envelope(data=result,request_id=request.state.request_id)
