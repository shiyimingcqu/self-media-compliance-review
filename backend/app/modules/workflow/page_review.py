"""Human resolution of page recognition notes; original VLM coverage is immutable."""
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from pydantic import Field
from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from .standards import fingerprint, project_for, record, replay

router = APIRouter(tags=['brief-review'])


def pending_checks(db, source_id):
    from .brief_vision import checked_result
    visual = db.execute("SELECT id,result_json FROM brief_visual_runs WHERE source_version_id=? AND status='succeeded' ORDER BY rowid DESC LIMIT 1", (source_id,)).fetchone()
    checks = []
    if visual:
        for page in checked_result(json.loads(visual['result_json']))['report']['pages']:
            reasons = page.get('uncertainties') or []
            if page['readability'] != 'readable' or reasons:
                checks.append({'pageNo': page['pageNo'], 'visionRunId': visual['id'], 'reasons': reasons or ['页面部分内容未识清，请对照原页查看。']})
    else:
        source = db.execute('SELECT uncovered_sources_json FROM brief_source_versions WHERE id=?', (source_id,)).fetchone()
        for value in json.loads(source['uncovered_sources_json']) if source else []:
            if isinstance(value, str) and value.startswith('page:') and value[5:].isdigit():
                checks.append({'pageNo': int(value[5:]), 'visionRunId': None, 'reasons': ['此页包含图片或扫描文字，尚未完成图文识读。']})
    resolved = {row['check_id'] for row in db.execute('SELECT check_id FROM brief_page_reviews WHERE source_version_id=?', (source_id,))}
    for check in checks:
        check['id'] = hashlib.sha256(json.dumps([source_id, check], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    return sorted([check for check in checks if check['id'] not in resolved], key=lambda c: c['pageNo'])


class PageReviewInput(ContractModel):
    source_version_id: str
    check_id: str = Field(min_length=1, max_length=64)
    action: Literal['confirmed', 'irrelevant']


@router.post('/projects/{project_id}/brief/pages/review', response_model=Envelope[dict])
def review_page(project_id: str, payload: PageReviewInput, request: Request,
                idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        project = project_for(db, project_id)
        scope = 'brief-page-review:' + project_id
        fp = fingerprint(payload); prior = replay(db, scope, idempotency_key, fp)
        if prior: return Envelope(data=prior, request_id=request.state.request_id)
        if project['status'] == 'archived': raise ApiError(409, 'PROJECT_ARCHIVED', '归档项目不可修改')
        current = db.execute('SELECT source_version_id FROM project_current_briefs WHERE project_id=?', (project_id,)).fetchone()
        if not current or current['source_version_id'] != payload.source_version_id:
            raise ApiError(409, 'REVISION_CONFLICT', 'Brief 已更新，请刷新后再核对')
        checks = pending_checks(db, payload.source_version_id)
        check = next((c for c in checks if c['id'] == payload.check_id), None)
        saved = db.execute('SELECT 1 FROM brief_page_reviews WHERE source_version_id=? AND check_id=?', (payload.source_version_id, payload.check_id)).fetchone()
        if not check and not saved: raise ApiError(409, 'REVISION_CONFLICT', '识读结果已更新，请查看当前待核对项')
        if check:
            stamp = now()
            db.execute('INSERT INTO brief_page_reviews VALUES (?,?,?,?,?)', (payload.source_version_id, payload.check_id, payload.action, json.dumps(check, ensure_ascii=False), stamp))
            db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)', (uuid4().hex, 'local-reviewer', 'project', project_id, 'brief_page_reviewed', 'demo', json.dumps({'sourceVersionId': payload.source_version_id, 'check': check, 'action': payload.action, 'modelCalled': False}, ensure_ascii=False), stamp))
        value = {'pageChecks': pending_checks(db, payload.source_version_id)}
        record(db, scope, idempotency_key, fp, value)
    return Envelope(data=value, request_id=request.state.request_id)
