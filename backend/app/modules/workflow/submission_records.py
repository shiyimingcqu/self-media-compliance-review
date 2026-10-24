"""All persisted review attempts, independently of brand-queue eligibility."""
import json
from typing import Literal
from fastapi import APIRouter, Query, Request
from app.core.contracts import Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.video_model.evidence.console_router import local_only

router = APIRouter(tags=['submission-records'])
Scope = Literal['script', 'video', 'image']
Outcome = Literal['all', 'processing', 'needs_changes', 'needs_confirmation', 'call_failed', 'pending_brand', 'approved', 'returned']
TABLES = {'script': 'workflow_script_reviews', 'video': 'evidence_runs', 'image': 'image_post_reviews'}


def record_rows(db, scope=None, project_id=None, task_id=None, identity=None, search=''):
    queries, args = [], []
    for kind, table in TABLES.items():
        if scope and scope != kind: continue
        where = ["p.mode='demo'"]
        params = []
        if kind == 'video': where.append("r.kind='video'")
        for column, value in [('t.project_id', project_id), ('r.task_id', task_id), ('r.id', identity)]:
            if value: where.append(column + '=?'); params.append(value)
        if search:
            where.append('instr(lower(t.creator_name || p.name),lower(?))>0'); params.append(search)
        queries.append(f"SELECT r.id,r.task_id,r.status,r.snapshot_json,r.result_json,r.error_code,r.error_message,r.created_at,r.updated_at,t.creator_name,t.project_id,t.archived_at,p.name AS project_name,'{kind}' AS scope FROM {table} r JOIN creator_tasks t ON t.id=r.task_id JOIN projects p ON p.id=t.project_id WHERE " + ' AND '.join(where))
        args += params
    return db.execute('SELECT * FROM (' + ' UNION ALL '.join(queries) + ') ORDER BY created_at DESC,id DESC', args).fetchall()


def record_view(db, row, detail=False):
    from .router import script_acceptance, video_component_acceptance
    from .image_posts import acceptance as image_acceptance
    from .discussions import accepted_rules
    snapshot = json.loads(row['snapshot_json'])
    result = json.loads(row['result_json'] or '{}')
    report = result.get('report') or result.get('partialReport') or {}
    scope = row['scope']
    if scope == 'image':
        decision = db.execute('SELECT decision,note FROM image_post_decisions WHERE task_id=? AND review_id=? ORDER BY rowid DESC LIMIT 1', (row['task_id'], row['id'])).fetchone()
    else:
        decision = db.execute('SELECT decision,note FROM workflow_decisions WHERE task_id=? AND scope=? AND target_id=? ORDER BY rowid DESC LIMIT 1', (row['task_id'], scope, row['id'])).fetchone()
    accepted = accepted_rules(db, scope, row['id']) if scope != 'image' else set()
    acceptance = (script_acceptance(row, accepted) if scope == 'script' else video_component_acceptance(row, accepted) if scope == 'video' else image_acceptance(row) if isinstance((result.get('report') or {}).get('findings'), list) else {'passed': False})
    if row['status'] in ('queued', 'processing'):
        outcome = 'processing'
    elif row['status'] != 'succeeded':
        outcome = 'call_failed'
    elif decision and decision['decision'] == 'returned':
        outcome = 'returned'
    elif acceptance['passed']:
        outcome = 'approved' if decision and decision['decision'] == 'approved' else 'pending_brand'
    else:
        from .video_layers import active_rules
        rules = active_rules(snapshot.get('rules', [])) if scope == 'video' else snapshot.get('rules', [])
        must = {r.get('id') for r in rules if r.get('importance') == 'must'}
        unconfigured = {r.get('ruleId') for r in snapshot.get('unconfiguredRules', [])}
        checks = [*report.get('findings', []), *report.get('mandatory_checks', [])]
        failed = any(f.get('verdict') == 'non_compliant' and f.get('rule_id') in must - accepted - unconfigured for f in checks)
        failed = failed or any(r.get('reason') == '必须项明确不合规' for r in acceptance.get('blockingRules', []))
        outcome = 'needs_changes' if failed else 'needs_confirmation'
    material = {}
    version = ''
    if scope == 'script':
        source = db.execute('SELECT v.* FROM script_versions v JOIN workflow_script_reviews r ON r.script_version_id=v.id WHERE r.id=?', (row['id'],)).fetchone()
        version = source['version_label'] if source else ''
        if detail: material = {'script': snapshot.get('script', ''), 'fileId': source['file_id'] if source else None}
    elif scope == 'video':
        if detail:
            source = db.execute('SELECT file_id FROM evidence_runs WHERE id=?', (row['id'],)).fetchone()
            material = {'script': snapshot.get('approvedScript', ''), 'fileId': source['file_id']}
    else:
        post = snapshot.get('version') or {}
        version = post.get('versionLabel', '')
        if detail: material = {'post': post}
    value = {'id': row['id'], 'taskId': row['task_id'], 'scope': scope, 'projectId': row['project_id'],
             'creatorName': row['creator_name'], 'projectName': row['project_name'], 'status': row['status'],
             'outcome': outcome, 'versionLabel': version or row['id'][:8], 'createdAt': row['created_at'],
             'summary': report.get('summary', ''), 'errorCode': row['error_code'], 'errorMessage': row['error_message'],
             'brandDecision': dict(decision) if decision else None, 'manualAcceptedCount': len(accepted),
             'reportVerified': row['status'] == 'succeeded' and bool(result.get('report')) and not result.get('partialReport') and not result.get('recovery', {}).get('requiresManualReview'),
             'taskArchived': bool(row['archived_at'])}
    if detail:
        value.update(material=material,
                     findings=[{k: f.get(k) for k in ('rule_id', 'rule_number', 'verdict', 'description', 'reason', 'requirement', 'quote', 'quote_segments', 'suggestion', 'image_ids', 'evidence_ids')} for f in [*report.get('findings', []), *report.get('mandatory_checks', [])]],
                     rules=[{k: r.get(k) for k in ('id', 'ruleNumber', 'text', 'criterion', 'importance')} for r in snapshot.get('rules', [])],
                     artifacts=[{k: a.get(k) for k in ('id', 'kind', 'startMs', 'endMs')} for a in result.get('artifacts', [])])
    return value


@router.get('/workflow/submission-records', response_model=Envelope[dict])
def submissions(request: Request, scope: Scope | None = None, project_id: str | None = Query(None, alias='projectId'), task_id: str | None = Query(None, alias='taskId'), outcome: Outcome = 'all', search: str = Query('', max_length=100), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        values = [record_view(db, row) for row in record_rows(db, scope, project_id, task_id, search=search.strip())]
        counts = {}
        for value in values: counts[value['outcome']] = counts.get(value['outcome'], 0) + 1
        if outcome != 'all': values = [v for v in values if v['outcome'] == outcome]
    return Envelope(data={'items': values[offset:offset+limit], 'total': len(values), 'counts': counts}, request_id=request.state.request_id)


@router.get('/projects/{project_id}/submission-records/{scope}/{review_id}', response_model=Envelope[dict])
def submission_detail(project_id: str, scope: Scope, review_id: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        rows = record_rows(db, scope, project_id=project_id, identity=review_id)
        if not rows: raise ApiError(404, 'RESOURCE_NOT_FOUND', '提交记录不属于此项目')
        value = record_view(db, rows[0], detail=True)
    return Envelope(data=value, request_id=request.state.request_id)
