"""Run-bound creator responses, rule disputes, human rulings and in-app reminders.

Uses the same demo/gateway access boundary as existing workflow routes; UI role
switching is not an authenticated identity system.
"""
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Header, Query, Request
from pydantic import Field, field_validator
from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.brief_script.script_service import authorize_task, require_task_writable
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only

router = APIRouter(tags=['creator-discussions'])
Scope = Literal['script', 'video']
TABLES = {'script': 'workflow_script_reviews', 'video': 'evidence_runs'}
OPEN = ('pending', 'needs_info')


class ResponseInput(ContractModel):
    scope: Scope
    review_id: str
    action: Literal['modifying', 'submitted'] = 'modifying'
    revision: int = Field(default=0, ge=0)


class MessageInput(ContractModel):
    message: str = Field(min_length=1, max_length=4000)
    location: str = Field(default='', max_length=300)
    revision: int = Field(default=0, ge=0)

    @field_validator('message')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('请填写具体说明')
        return value.strip()


class DisputeInput(MessageInput):
    scope: Scope
    review_id: str
    rule_id: str


class RulingInput(ContractModel):
    outcome: Literal['accepted', 'rejected', 'needs_info']
    note: str = Field(min_length=1, max_length=4000)
    revision: int = Field(ge=1)
    reviewed_material: bool = False


class ReminderInput(ContractModel):
    scope: Scope
    message: str = Field(min_length=1, max_length=1000)


def accepted_rules(db, scope, review_id):
    if not review_id:
        return set()
    return {r[0] for r in db.execute("SELECT rule_id FROM review_disputes WHERE scope=? AND review_id=? AND status='accepted'", (scope, review_id))}


def target(db, task_id, scope, review_id):
    row = db.execute(f'SELECT * FROM {TABLES[scope]} WHERE id=? AND task_id=?', (review_id, task_id)).fetchone()
    if not row or (scope == 'video' and row['kind'] != 'video'):
        raise ApiError(404, 'RESOURCE_NOT_FOUND', '审核记录不属于此合作任务')
    return row, json.loads(row['snapshot_json']), json.loads(row['result_json'] or '{}')


def is_current(db, task_id, scope, review_id):
    from .router import state
    view = state(db, task_id)
    return review_id == ((view['scriptReview'] or {}).get('id') if scope == 'script' else view['latestVideoRunId'])


def require_current(db, task_id, scope, review_id):
    if not is_current(db, task_id, scope, review_id):
        raise ApiError(409, 'REVIEW_OUTDATED', '内容或规则已更新，请对当前审核结果反馈；历史异议仍保留')


def mutate(request, task_id, operation, key, payload, action):
    from .router import replay, remember
    local_only(request)
    fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    with connection(request.app.state.settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        task = authorize_task(db, task_id)
        prior = replay(db, operation + ':' + task_id, key, fingerprint)
        if prior is not None:
            return Envelope(data=prior, request_id=request.state.request_id)
        require_task_writable(task)
        value = action(db, task)
        stamp = now()
        db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)',
                   (uuid4().hex, 'local-workspace', 'creator_discussion', task_id, operation, 'demo', payload.model_dump_json(), stamp))
        db.execute('UPDATE creator_tasks SET updated_at=? WHERE id=?', (stamp, task_id))
        remember(db, operation + ':' + task_id, key, fingerprint, value)
    return Envelope(data=value, request_id=request.state.request_id)


def dispute_view(db, row):
    value = dict(row)
    value['rule'] = json.loads(value.pop('rule_json'))
    value['finding'] = json.loads(value.pop('finding_json'))
    value['messages'] = [dict(r) for r in db.execute('SELECT * FROM review_dispute_messages WHERE dispute_id=? ORDER BY rowid', (row['id'],))]
    return value


def set_response(db, task_id, scope, review_id, action):
    db.execute('INSERT INTO review_responses VALUES (?,?,?,?,1,?) ON CONFLICT(task_id,scope,review_id) DO UPDATE SET action=excluded.action,revision=review_responses.revision+1,updated_at=excluded.updated_at',
               (task_id, scope, review_id, action, now()))


@router.get('/tasks/{task_id}/review-discussion', response_model=Envelope[dict])
def discussion(task_id: str, scope: Scope, request: Request, review_id: str = Query(alias='reviewId')):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        task = authorize_task(db, task_id)
        row, snapshot, result = target(db, task_id, scope, review_id)
        response = db.execute('SELECT * FROM review_responses WHERE task_id=? AND scope=? AND review_id=?', (task_id, scope, review_id)).fetchone()
        threads = db.execute('SELECT * FROM review_disputes WHERE task_id=? AND scope=? AND review_id=? ORDER BY rowid', (task_id, scope, review_id)).fetchall()
        current = is_current(db, task_id, scope, review_id)
        from .router import script_submission_ready, video_submission_ready
        value = {'response': dict(response) if response else None, 'disputes': [dispute_view(db, r) for r in threads], 'current': current,
                 'submissionReady':current and (script_submission_ready if scope == 'script' else video_submission_ready)(row, accepted_rules(db, scope, review_id)),
                 'writable': current and task['archived_at'] is None and task['project_status'] != 'archived' and row['status'] == 'succeeded'}
        if scope=='video':
            from .video_layers import legacy_skipped_competitors
            if legacy_skipped_competitors(snapshot,(result.get('report') or {}).get('findings',[])):
                value['submissionNotice']='旧报告未执行竞品检查，可提交品牌人工核对；重新验收时由模型自动判断，无需先配置竞品名单。'
    return Envelope(data=value, request_id=request.state.request_id)


@router.post('/tasks/{task_id}/review-responses', response_model=Envelope[dict])
def respond(task_id: str, payload: ResponseInput, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=8)):
    def action(db, task):
        row, _, result = target(db, task_id, payload.scope, payload.review_id)
        require_current(db, task_id, payload.scope, payload.review_id)
        findings = (result.get('report') or {}).get('findings', [])
        if payload.action == 'submitted':
            from .router import script_submission_ready, video_submission_ready
            ready = script_submission_ready if payload.scope == 'script' else video_submission_ready
            if not ready(row, accepted_rules(db, payload.scope, payload.review_id)):
                raise ApiError(409, 'SCRIPT_SUBMISSION_UNAVAILABLE' if payload.scope == 'script' else 'VIDEO_SUBMISSION_UNAVAILABLE', '仅可提交本次审核已完成、资料齐备且没有未通过必须项的内容')
        elif row['status'] != 'succeeded' or not any(f.get('verdict') in ('non_compliant', 'needs_confirmation', 'pending') for f in [*findings, *(result.get('report') or {}).get('mandatory_checks', [])]):
            raise ApiError(409, 'RESPONSE_UNAVAILABLE', '当前没有需要处理的有效 AI 意见')
        old = db.execute('SELECT revision FROM review_responses WHERE task_id=? AND scope=? AND review_id=?', (task_id, payload.scope, payload.review_id)).fetchone()
        if payload.revision != (old['revision'] if old else 0):
            raise ApiError(409, 'REVISION_CONFLICT', '处理状态已更新，请刷新')
        set_response(db, task_id, payload.scope, payload.review_id, payload.action)
        return {'action': payload.action}
    return mutate(request, task_id, 'creator_response', idempotency_key, payload, action)


@router.post('/tasks/{task_id}/review-disputes', response_model=Envelope[dict])
def submit_dispute(task_id: str, payload: DisputeInput, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=8)):
    def action(db, task):
        row, snapshot, result = target(db, task_id, payload.scope, payload.review_id)
        require_current(db, task_id, payload.scope, payload.review_id)
        rule = next((r for r in snapshot.get('rules', []) if r.get('id') == payload.rule_id), None)
        report = result.get('report') or {}
        finding = next((f for f in [*report.get('findings', []), *report.get('mandatory_checks', [])] if f.get('rule_id') == payload.rule_id), None)
        disputable = ('non_compliant', 'needs_confirmation', 'pending', 'compliant', 'not_applicable') if payload.scope == 'script' else ('non_compliant', 'needs_confirmation', 'pending')
        if row['status'] != 'succeeded' or not rule or not finding or finding.get('verdict') not in disputable:
            raise ApiError(400, 'DISPUTE_RULE_INVALID', '请对本次有效报告中的规则提出异议；视频仅支持未通过或待确定项')
        old = db.execute('SELECT * FROM review_disputes WHERE task_id=? AND scope=? AND review_id=? AND rule_id=?', (task_id, payload.scope, payload.review_id, payload.rule_id)).fetchone()
        if old and old['status'] not in OPEN:
            raise ApiError(409, 'DISPUTE_CLOSED', '品牌已裁决此异议，可查看裁决说明')
        if payload.revision != (old['revision'] if old else 0):
            raise ApiError(409, 'REVISION_CONFLICT', '异议已更新，请刷新后补充说明')
        identity = old['id'] if old else uuid4().hex
        stamp = now()
        if old:
            db.execute("UPDATE review_disputes SET status='pending',revision=revision+1,updated_at=? WHERE id=?", (stamp, identity))
        else:
            db.execute('INSERT INTO review_disputes VALUES (?,?,?,?,?,?,?,?,1,?,?)', (identity, task_id, payload.scope, payload.review_id, payload.rule_id, json.dumps(rule, ensure_ascii=False), json.dumps(finding, ensure_ascii=False), 'pending', stamp, stamp))
        db.execute('INSERT INTO review_dispute_messages VALUES (?,?,?,?,?,?,?)', (uuid4().hex, identity, 'creator', 'comment', payload.message, payload.location.strip(), stamp))
        set_response(db, task_id, payload.scope, payload.review_id, 'disputing')
        return {'id': identity}
    return mutate(request, task_id, 'creator_dispute', idempotency_key, payload, action)


@router.post('/projects/{project_id}/review-disputes/{dispute_id}/decisions', response_model=Envelope[dict])
def rule_decision(project_id: str, dispute_id: str, payload: RulingInput, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=8)):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        row = db.execute('SELECT d.task_id FROM review_disputes d JOIN creator_tasks t ON t.id=d.task_id WHERE d.id=? AND t.project_id=?', (dispute_id, project_id)).fetchone()
        if not row:
            raise ApiError(404, 'RESOURCE_NOT_FOUND', '异议不属于此项目')
        task_id = row['task_id']
    def action(db, task):
        row = db.execute('SELECT * FROM review_disputes WHERE id=? AND task_id=?', (dispute_id, task_id)).fetchone()
        if row['revision'] != payload.revision or row['status'] not in OPEN:
            raise ApiError(409, 'REVISION_CONFLICT', '异议已处理或有新说明，请刷新')
        if not payload.note.strip() or not payload.reviewed_material:
            raise ApiError(400, 'REVIEW_CONFIRMATION_REQUIRED', '请核对原始材料并填写裁决理由')
        stamp = now()
        db.execute('UPDATE review_disputes SET status=?,revision=revision+1,updated_at=? WHERE id=?', (payload.outcome, stamp, dispute_id))
        db.execute('INSERT INTO review_dispute_messages VALUES (?,?,?,?,?,?,?)', (uuid4().hex, dispute_id, 'brand', payload.outcome, payload.note.strip(), '', stamp))
        return {'id': dispute_id, 'status': payload.outcome}
    return mutate(request, task_id, 'brand_dispute:' + dispute_id, idempotency_key, payload, action)


@router.get('/workflow/review-disputes', response_model=Envelope[dict])
def brand_disputes(request: Request, status: Literal['open', 'all', 'accepted', 'rejected'] = 'open', project_id: str | None = Query(None, alias='projectId'), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    local_only(request)
    where = ["p.mode='demo'"]
    args = []
    if status == 'open':
        where.append("d.status IN ('pending','needs_info')")
    elif status != 'all':
        where.append('d.status=?'); args.append(status)
    if project_id:
        where.append('t.project_id=?'); args.append(project_id)
    query = ' FROM review_disputes d JOIN creator_tasks t ON t.id=d.task_id JOIN projects p ON p.id=t.project_id WHERE ' + ' AND '.join(where)
    with connection(request.app.state.settings.database_path) as db:
        total = db.execute('SELECT count(*)' + query, args).fetchone()[0]
        rows = db.execute('SELECT d.*,t.creator_name,t.project_id,p.name AS project_name' + query + ' ORDER BY d.updated_at DESC,d.id LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        values = [dispute_view(db, r) for r in rows]
    return Envelope(data={'items': values, 'total': total}, request_id=request.state.request_id)


@router.get('/projects/{project_id}/review-disputes/{dispute_id}', response_model=Envelope[dict])
def dispute_detail(project_id: str, dispute_id: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        row = db.execute("SELECT d.*,t.creator_name,t.project_id,p.name AS project_name FROM review_disputes d JOIN creator_tasks t ON t.id=d.task_id JOIN projects p ON p.id=t.project_id WHERE d.id=? AND t.project_id=? AND p.mode='demo'", (dispute_id, project_id)).fetchone()
        if not row:
            raise ApiError(404, 'RESOURCE_NOT_FOUND', '异议不属于此项目')
        task = authorize_task(db, row['task_id'])
        run, snapshot, result = target(db, row['task_id'], row['scope'], row['review_id'])
        value = dispute_view(db, row)
        value.update(current=is_current(db, row['task_id'], row['scope'], row['review_id']), writable=task['archived_at'] is None and task['project_status'] != 'archived',
                     script=snapshot.get('script') or snapshot.get('approvedScript') or '', fileId=run['file_id'] if row['scope'] == 'video' else None,
                     rules=snapshot.get('rules', []), findings=(result.get('report') or {}).get('findings', []),
                     artifacts=[a for a in result.get('artifacts', []) if a.get('id') in value['finding'].get('evidence_ids', [])])
        if row['scope'] == 'script':
            from app.modules.brief_script.script_service import script_version_detail
            from .router import review_view
            value['scriptMaterial'] = script_version_detail(db, run['script_version_id'])
            value['scriptReview'] = review_view(run)
            value['fileId'] = value['scriptMaterial']['fileId']
    return Envelope(data=value, request_id=request.state.request_id)


def progress_item(db, task, view=None):
    from .router import state
    view = view or state(db, task['id'])
    def stage(scope):
        run = view['scriptReview'] if scope == 'script' else (dict(db.execute('SELECT id,status FROM evidence_runs WHERE id=?', (view['latestVideoRunId'],)).fetchone()) if view['latestVideoRunId'] else None)
        if scope == 'script' and view['scriptApproval'] or scope == 'video' and (view['finalDecision'] or {}).get('decision') == 'approved':
            return {'status': 'approved', 'reviewId': run['id'] if run else None}
        if not run:
            submitted = bool(view['currentScript']) if scope == 'script' else bool(db.execute("SELECT 1 FROM evidence_runs WHERE task_id=? AND kind='video' LIMIT 1", (task['id'],)).fetchone())
            return {'status': 'pending_review' if submitted else 'not_submitted', 'reviewId': None}
        rid = run['id']
        if run['status'] in ('queued', 'processing'):
            return {'status': 'reviewing', 'reviewId': rid}
        if run['status'] != 'succeeded':
            return {'status': 'call_failed', 'reviewId': rid}
        disputes = db.execute("SELECT status FROM review_disputes WHERE task_id=? AND scope=? AND review_id=?", (task['id'], scope, rid)).fetchall()
        if any(r['status'] in OPEN for r in disputes):
            return {'status': 'disputed', 'reviewId': rid}
        acceptance = view['scriptAcceptance'] if scope == 'script' else view['videoComponentAcceptance']
        decision = (next((r for r in view['history'] if r['scope'] == 'script' and r['target_id'] == rid), None) if scope == 'script' else view['finalDecision'])
        response = db.execute('SELECT * FROM review_responses WHERE task_id=? AND scope=? AND review_id=?', (task['id'], scope, rid)).fetchone()
        returned = decision and decision['decision'] == 'returned'
        if not returned and not (response and response['action'] == 'submitted') and (view['scriptSubmissionReady'] if scope == 'script' else acceptance['passed']):
            status = 'ready_to_submit'
        elif not returned and (acceptance['passed'] or response and response['action'] == 'submitted'):
            status = 'pending_brand'
        elif response and response['action'] == 'modifying':
            status = 'modifying'
        else:
            status = 'needs_response'
        return {'status': status, 'reviewId': rid}
    script, video = stage('script'), stage('video')
    focus = 'video' if script['status'] == 'approved' or video['reviewId'] else 'script'
    if script['status'] == 'disputed': focus = 'script'
    reminder = db.execute('SELECT * FROM creator_reminders WHERE task_id=? ORDER BY rowid DESC LIMIT 1', (task['id'],)).fetchone()
    return {**dict(task), 'script': script, 'video': video, 'focus': focus, 'status': (script if focus == 'script' else video)['status'], 'lastReminder': dict(reminder) if reminder else None}


@router.get('/workflow/creator-progress', response_model=Envelope[dict])
def progress(request: Request, project_id: str | None = Query(None, alias='projectId'), scope: Scope | None = None, status: str | None = None, search: str = Query('', max_length=100), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        rows = db.execute("SELECT t.id,t.creator_name,t.platform,t.project_id,t.updated_at,p.name AS project_name FROM creator_tasks t JOIN projects p ON p.id=t.project_id WHERE p.mode='demo' AND p.status!='archived' AND t.archived_at IS NULL ORDER BY t.updated_at DESC,t.id").fetchall()
        values = [progress_item(db, t) for t in rows if (not project_id or t['project_id'] == project_id) and (not search or search.lower() in (t['creator_name'] + t['project_name']).lower())]
        if scope:
            for item in values: item['status'] = item[scope]['status']; item['focus'] = scope
        counts = {}
        for item in values: counts[item['status']] = counts.get(item['status'], 0) + 1
        if status: values = [item for item in values if item['status'] == status]
    return Envelope(data={'items': values[offset:offset+limit], 'total': len(values), 'counts': counts}, request_id=request.state.request_id)


@router.post('/tasks/{task_id}/creator-reminders', response_model=Envelope[dict])
def remind(task_id: str, payload: ReminderInput, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=8)):
    def action(db, task):
        if not payload.message.strip(): raise ApiError(400, 'VALIDATION_ERROR', '请填写催办内容')
        identity = uuid4().hex
        db.execute('INSERT INTO creator_reminders VALUES (?,?,?,?,?,NULL)', (identity, task_id, payload.scope, payload.message.strip(), now()))
        return {'id': identity}
    return mutate(request, task_id, 'brand_reminder', idempotency_key, payload, action)


@router.get('/tasks/{task_id}/creator-reminders', response_model=Envelope[list[dict]])
def reminders(task_id: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        authorize_task(db, task_id)
        values = [dict(r) for r in db.execute('SELECT * FROM creator_reminders WHERE task_id=? ORDER BY rowid DESC LIMIT 30', (task_id,))]
    return Envelope(data=values, request_id=request.state.request_id)


@router.post('/tasks/{task_id}/creator-reminders/{reminder_id}/read', response_model=Envelope[dict])
def read_reminder(task_id: str, reminder_id: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        authorize_task(db, task_id)
        cursor = db.execute('UPDATE creator_reminders SET read_at=coalesce(read_at,?) WHERE id=? AND task_id=?', (now(), reminder_id, task_id))
        if not cursor.rowcount: raise ApiError(404, 'RESOURCE_NOT_FOUND', '催办不属于此任务')
    return Envelope(data={'read': True}, request_id=request.state.request_id)
