"""Read-only project history for brands tuning rules; historical snapshots stay intact."""
import json
from typing import Literal

from fastapi import APIRouter, Query, Request
from app.core.contracts import Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.video_model.evidence.console_router import local_only

router = APIRouter(tags=['submission-feedback'])
Scope = Literal['script', 'video']
Outcome = Literal['all', 'needs_changes', 'needs_confirmation', 'call_failed', 'returned']


def authorize(db, project_id, task_id=None):
    if not db.execute("SELECT 1 FROM projects WHERE id=? AND mode='demo'", (project_id,)).fetchone():
        raise ApiError(404, 'RESOURCE_NOT_FOUND', '项目不存在')
    if task_id and not db.execute('SELECT 1 FROM creator_tasks WHERE id=? AND project_id=?', (task_id, project_id)).fetchone():
        raise ApiError(404, 'RESOURCE_NOT_FOUND', '合作任务不属于当前项目')


def current_rules(db, project_id):
    values = {}
    for scope in ('script', 'video'):
        row = db.execute('SELECT rules_json FROM project_standard_sets WHERE project_id=? AND scope=? ORDER BY revision DESC LIMIT 1', (project_id, scope)).fetchone()
        values[scope] = json.loads(row['rules_json']) if row else []
    return values


def rows(db, project_id, scope=None, task_id=None, identity=None, creator_name=''):
    queries, args = [], []
    for kind, table in [('script', 'workflow_script_reviews'), ('video', 'evidence_runs')]:
        if scope and kind != scope:
            continue
        extra = " AND r.kind='video'" if kind == 'video' else ''
        if task_id:
            extra += ' AND r.task_id=?'
        if identity:
            extra += ' AND r.id=?'
        if creator_name:
            extra += ' AND instr(lower(t.creator_name),lower(?))>0'
        version = "(SELECT version_label FROM script_versions WHERE id=r.script_version_id)" if kind == 'script' else 'NULL'
        material = "(SELECT file_id FROM script_versions WHERE id=r.script_version_id)" if kind == 'script' else 'r.file_id'
        queries.append(f"""SELECT r.id,r.task_id,r.status,r.created_at,r.snapshot_json,r.result_json,
            r.error_code,r.error_message,t.creator_name,t.archived_at AS task_archived,
            '{kind}' AS scope,{version} AS version_label,{material} AS material_file_id,
            (SELECT decision FROM workflow_decisions d WHERE d.target_id=r.id AND d.task_id=r.task_id AND d.scope='{kind}' ORDER BY d.rowid DESC LIMIT 1) AS brand_decision,
            (SELECT note FROM workflow_decisions d WHERE d.target_id=r.id AND d.task_id=r.task_id AND d.scope='{kind}' ORDER BY d.rowid DESC LIMIT 1) AS brand_note
            FROM {table} r JOIN creator_tasks t ON t.id=r.task_id
            WHERE t.project_id=? AND r.status IN ('succeeded','failed','interrupted'){extra}""")
        args.extend([project_id] + ([task_id] if task_id else []) + ([identity] if identity else []) + ([creator_name] if creator_name else []))
    return db.execute('SELECT * FROM (' + ' UNION ALL '.join(queries) + ') ORDER BY created_at DESC,id DESC', args)


def signature(rules):
    return [(r.get('id'), r.get('text'), r.get('criterion'), r.get('importance', 'must'), r.get('layer'), r.get('enabled',True), r.get('videoReviewLevel')) for r in rules]


def case(row, latest, detail=False):
    snapshot = json.loads(row['snapshot_json'])
    result = json.loads(row['result_json'] or '{}')
    report = result.get('report') or result.get('partialReport') or {}
    rules = snapshot.get('rules') or []
    by_id = {r['id']: r for r in rules if r.get('id')}
    from .video_layers import active_rules
    active_ids = {r['id'] for r in active_rules(rules)} if row['scope'] == 'video' else set(by_id)
    issues = []
    for finding in [*(report.get('findings') or []), *(report.get('mandatory_checks') or [])]:
        if finding.get('verdict') not in ('non_compliant', 'needs_confirmation', 'pending'):
            continue
        rule = by_id.get(finding.get('rule_id'), {})
        issues.append({'ruleId': finding.get('rule_id'), 'ruleNumber': rule.get('ruleNumber', finding.get('rule_number')),
                       'ruleText': rule.get('text') or finding.get('requirement') or '未匹配的历史规则',
                       'criterion': rule.get('criterion', ''), 'importance': rule.get('importance', 'must'),
                       'layer': (rule.get('layer') or ('precise' if rule.get('componentPlan') else 'brief')) if row['scope']=='video' else None,
                       'verdict': finding.get('verdict'), 'description': finding.get('description') or finding.get('reason') or '',
                       'suggestion': finding.get('suggestion', ''), 'quote': finding.get('quote', ''),
                       'evidenceIds': finding.get('evidence_ids') or [],
                       'blocking': finding.get('rule_id') in active_ids and rule.get('enabled',True) and rule.get('importance', 'must') != 'suggested'})
    blocking = [item for item in issues if item['blocking']]
    configuration_required = any(r.get('importance')=='must' for r in snapshot.get('unconfiguredRules',[]))
    if row['status'] in ('failed', 'interrupted'):
        outcome = 'call_failed'
    elif row['brand_decision'] == 'returned':
        outcome = 'returned'
    elif any(item['verdict'] == 'non_compliant' for item in blocking):
        outcome = 'needs_changes'
    elif configuration_required or blocking or result.get('recovery', {}).get('requiresManualReview') or snapshot.get('uncoveredText') or (result.get('coverage') or {}).get('pendingRuleIds'):
        outcome = 'needs_confirmation'
    else:
        return None
    value = {'id': row['id'], 'taskId': row['task_id'], 'creatorName': row['creator_name'],
             'scope': row['scope'], 'status': row['status'], 'outcome': outcome, 'createdAt': row['created_at'],
             'versionLabel': row['version_label'] or '视频记录 ' + row['id'][:8],
             'rulesCurrent': bool(rules) and signature(rules) == signature(latest[row['scope']]),
             'taskArchived': bool(row['task_archived']), 'summary': report.get('summary', ''),
             'errorCode': row['error_code'], 'errorMessage': row['error_message'],
             'brandDecision': row['brand_decision'], 'brandNote': row['brand_note'], 'issues': issues,
             'configurationRequired':configuration_required,
             'unconfiguredRules': snapshot.get('unconfiguredRules', []) if row['scope'] == 'video' else [],
             'reportVerified': row['status'] == 'succeeded' and bool(result.get('report'))}
    if detail:
        value['rules'] = [{'id': r.get('id'), 'ruleNumber': r.get('ruleNumber'), 'text': r.get('text'), 'criterion': r.get('criterion'), 'importance': r.get('importance'), 'layer':r.get('layer') or ('precise' if r.get('componentPlan') else 'brief'), 'enabled':r.get('enabled',True)} for r in rules]
        value['scriptText'] = snapshot.get('script') if row['scope'] == 'script' else snapshot.get('approvedScript')
        value['fileId'] = row['material_file_id']
        value['artifacts'] = [{k: a.get(k) for k in ('id', 'kind', 'startMs', 'endMs', 'issueId')} for a in result.get('artifacts', [])]
        value['providerRequestIds'] = result.get('providerRequestIds') or []
    return value


@router.get('/projects/{project_id}/submission-feedback', response_model=Envelope[dict])
def list_feedback(project_id: str, request: Request, scope: Scope | None = None, task_id: str | None = Query(None, alias='taskId'),
                  outcome: Outcome = 'all', offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                  creator_name: str = Query('', alias='creatorName', max_length=50), layer: Literal['baseline','brief','precise'] | None = None):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        authorize(db, project_id, task_id)
        latest = current_rules(db, project_id)
        total, items = 0, []
        counts = {name: 0 for name in ('needs_changes', 'needs_confirmation', 'call_failed', 'returned')}
        for row in rows(db, project_id, scope, task_id, creator_name=creator_name.strip()):
            item = case(row, latest)
            if not item or (layer and (item['scope']!='video' or not any(issue.get('layer')==layer for issue in [*item['issues'], *item['unconfiguredRules']]))):
                continue
            counts[item['outcome']] += 1
            if outcome != 'all' and item['outcome'] != outcome:
                continue
            if offset <= total < offset + limit:
                items.append(item)
            total += 1
    return Envelope(data={'items': items, 'total': total, 'offset': offset, 'limit': limit, 'counts': counts}, request_id=request.state.request_id)


@router.get('/projects/{project_id}/submission-feedback/{scope}/{identity}', response_model=Envelope[dict])
def feedback_detail(project_id: str, scope: Scope, identity: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:
        authorize(db, project_id)
        row = next(iter(rows(db, project_id, scope, identity=identity)), None)
        value = case(row, current_rules(db, project_id), detail=True) if row else None
        if not value:
            raise ApiError(404, 'RESOURCE_NOT_FOUND', '未找到此项目的未通过记录')
    return Envelope(data=value, request_id=request.state.request_id)
