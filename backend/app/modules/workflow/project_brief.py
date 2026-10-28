"""One current Brief per project; replacements retain immutable source history."""
import json
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from pydantic import Field

from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.brief_script.router import BriefCreate
from app.modules.brief_script.service_registration import create_brief
from app.modules.video_model.evidence.console_router import local_only
from .standards import project_for, fingerprint, replay, record, invalidate_project, source_blocks

router = APIRouter(tags=['project-brief'])


class ProjectBriefInput(ContractModel):
    file_id: str
    title: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)
    auto_process: bool = False


def adopt_confirmed_project_briefs(db):
    """An already confirmed project source is an explicit choice, even with other history."""
    rows=db.execute("SELECT r.project_id,r.source_version_id FROM project_standard_sets r JOIN brief_source_versions s ON s.id=r.source_version_id JOIN briefs b ON b.id=s.brief_id JOIN projects p ON p.id=r.project_id WHERE r.scope='script' AND r.revision=(SELECT MAX(v.revision) FROM project_standard_sets v WHERE v.project_id=r.project_id AND v.scope='script') AND b.project_id=r.project_id AND s.extraction_status='ready' AND p.mode='demo' AND p.status!='archived' AND NOT EXISTS (SELECT 1 FROM project_current_briefs c WHERE c.project_id=r.project_id)").fetchall()
    for row in rows:
        db.execute('INSERT INTO project_current_briefs VALUES (?,?,1,?)', (row['project_id'],row['source_version_id'],now()))
        invalidate_project(db,row['project_id'],{'briefSourceVersionId':row['source_version_id']},reason='采用项目规则已确认的 Brief 原文，需重新验收')


def current_brief(db, project_id, include_blocks=True):
    # Keep coverage, visual run and human review projection on the same SQLite snapshot.
    # Otherwise a background completion between SELECTs can mix old coverage with a new run.
    if not db.in_transaction:
        db.execute('BEGIN')
    project = db.execute("SELECT id FROM projects WHERE id=? AND mode='demo'", (project_id,)).fetchone()
    if not project: raise ApiError(404, 'RESOURCE_NOT_FOUND', '项目不存在')
    row = db.execute('SELECT c.revision,c.updated_at,s.*,b.title FROM project_current_briefs c JOIN brief_source_versions s ON s.id=c.source_version_id JOIN briefs b ON b.id=s.brief_id WHERE c.project_id=? AND b.project_id=c.project_id', (project_id,)).fetchone()
    if not row: return None
    result = {'sourceVersionId': row['id'], 'briefId': row['brief_id'], 'fileId': row['file_id'], 'title': row['title'], 'revision': row['revision'], 'updatedAt': row['updated_at'], 'extractionStatus': row['extraction_status'], 'pageCount': row['page_count'], 'uncoveredSources': json.loads(row['uncovered_sources_json'])}
    from .brief_vision import resolve_material,run_view
    native_uncovered=result['uncoveredSources']
    native_blocks=[{'id': b['id'], 'ordinal': b['ordinal'], 'text': b['text'], 'pageNo': b['page_no']} for b in db.execute('SELECT * FROM brief_text_blocks WHERE source_version_id=? ORDER BY ordinal', (row['id'],))] if include_blocks else []
    blocks,uncovered,run_id=resolve_material(db,row['id'],native_blocks,native_uncovered)
    result.update({'nativeUncoveredSources':native_uncovered,'uncoveredSources':uncovered,'visionRunId':run_id})
    from .page_review import pending_checks
    result['pageChecks'] = pending_checks(db, row['id'])
    from .brief_processing import latest
    result['processing']=latest(db,row['id'])
    if include_blocks:
        result['blocks']=blocks
        result['visualRun']=run_view(db.execute('SELECT * FROM brief_visual_runs WHERE source_version_id=? ORDER BY rowid DESC LIMIT 1',(row['id'],)).fetchone())
        # Display the same successful extraction that supplied blocks, even when
        # a newer attempt failed. Latest job status remains in visualRun.
        applied=run_view(db.execute('SELECT * FROM brief_visual_runs WHERE id=?',(run_id,)).fetchone()) if run_id else None
        result['recognizedPages']=applied['result']['report']['pages'] if applied and applied.get('result') else []
        result['directSummary']=applied['result'].get('directSummary') if applied and applied.get('result') else None
        result['summaryUpdatedAt']=applied['updatedAt'] if applied else None
    return result


def review_brief(db, project_id, fallback_source=None):
    value = current_brief(db, project_id, include_blocks=False)
    source = value['sourceVersionId'] if value else fallback_source
    if not source: return None
    blocks, uncovered = source_blocks(db, project_id, source)
    if not value:
        row = db.execute('SELECT b.title,s.file_id FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id WHERE s.id=? AND b.project_id=?', (source, project_id)).fetchone()
        value = {'sourceVersionId': source, 'title': row['title'], 'fileId': row['file_id'], 'revision': None}
    return {**value, 'blocks': blocks, 'uncoveredSources': uncovered}


@router.get('/projects/{project_id}/brief', response_model=Envelope[dict])
def get_brief(project_id: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        value = current_brief(db, project_id)
    return Envelope(data={'projectId': project_id, 'revision': value['revision'] if value else 0, 'brief': value}, request_id=request.state.request_id)


@router.post('/projects/{project_id}/brief', response_model=Envelope[dict])
def upload_brief(project_id: str, payload: ProjectBriefInput, request: Request, idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE'); project = project_for(db, project_id)
        scope = 'project-brief:' + project_id; fp = fingerprint(payload)
        prior = replay(db, scope, idempotency_key, fp)
        if prior: return Envelope(data=prior, request_id=request.state.request_id)
        existing = current_brief(db, project_id, include_blocks=False)
        revision = existing['revision'] if existing else 0
        if payload.revision != revision: raise ApiError(409, 'REVISION_CONFLICT', '项目 Brief 已更新，请刷新后重试')
        registered = create_brief(db, request.app.state.settings, project_id, BriefCreate(file_id=payload.file_id, title=payload.title, platforms=json.loads(project['platforms_json'])), idempotency_key, start_transaction=False,allow_image_only_pdf=True)
        if registered['extractionStatus'] == 'ready':
            db.execute('INSERT INTO project_current_briefs VALUES (?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET source_version_id=excluded.source_version_id,revision=excluded.revision,updated_at=excluded.updated_at', (project_id, registered['sourceVersionId'], revision + 1, now()))
            invalidate_project(db, project_id, {'briefSourceVersionId': registered['sourceVersionId']}, reason='项目 Brief 更新，需按当前原文重新验收')
            db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)', (uuid4().hex, 'local-reviewer', 'project', project_id, 'current_brief_updated', 'demo', json.dumps({'sourceVersionId': registered['sourceVersionId'], 'revision': revision + 1}), now()))
        processing_id=None
        if registered['extractionStatus']=='ready' and payload.auto_process:
            from .brief_processing import create
            processing_id,_=create(db,project_id,registered['sourceVersionId'])
        value = {'registration': registered, 'brief': current_brief(db, project_id), 'revision': revision + 1 if registered['extractionStatus'] == 'ready' else revision}
        record(db, scope, idempotency_key, fp, value)
    if processing_id:
        from .brief_processing import submit
        submit(request,processing_id)
    return Envelope(data=value, request_id=request.state.request_id)
