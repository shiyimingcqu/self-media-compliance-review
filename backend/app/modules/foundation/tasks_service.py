"""达人任务与规则绑定（第二增量后端最小集 + A dispute#79 修订）：仅 demo 模式。
写请求（创建/PATCH/换绑）幂等=Idempotency-Key+payload 指纹（含 If-Match 值），scope 按
actor/endpoint/task/项目 隔离；复用 create_project 式纯 DB 短事务（同事务查放+写+审计，
失败整体回滚，不残留半成品 key），不复制文件 lease 方案。换绑/阶段变更仅追加基础事件与
实时重算资格，不宣称下游已消费。回放旧成功不重查当前归档态（回放≠新写）。"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.database import connection
from app.core.eligibility import bindable_version, rules_enabled
from app.core.errors import ApiError
from app.modules.foundation.tasks_schemas import CreatorTask, TaskCreate, TaskPatch, TaskRebind

ACTOR = "local-reviewer"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _task_from_row(db: sqlite3.Connection, row: sqlite3.Row) -> CreatorTask:
    return CreatorTask(id=row["id"], project_id=row["project_id"], creator_name=row["creator_name"],
                       platform=row["platform"], content_type=row["content_type"],
                       campaign_phase=row["campaign_phase"], rule_version_id=row["rule_version_id"],
                       stage=row["stage"], revision=row["revision"], archived_at=row["archived_at"],
                       mode=row["project_mode"], created_at=row["created_at"], updated_at=row["updated_at"])


SELECT_TASK = ("SELECT t.*, p.mode AS project_mode, p.status AS project_status FROM creator_tasks t "
               "JOIN projects p ON p.id=t.project_id")


def get_task(db: sqlite3.Connection, task_id: str, mode: str = "demo") -> sqlite3.Row:
    row = db.execute(SELECT_TASK + " WHERE t.id=?", (task_id,)).fetchone()
    if row is None or row["project_mode"] != mode:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "任务不存在或不属于当前模式")
    return row


def _check_revision(row: sqlite3.Row, if_match: str | None) -> None:
    if if_match is None:
        raise ApiError(428, "PRECONDITION_REQUIRED", "修改任务必须携带 If-Match revision")
    try:
        if int(if_match) != row["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "任务已被更新，请读取新版本")
    except ValueError:
        raise ApiError(400, "VALIDATION_ERROR", "If-Match 必须是整数 revision", fields={"If-Match": if_match})


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()


def _replay_or_none(db: sqlite3.Connection, scope: str, key: str, fingerprint: str) -> str | None:
    """同事务查放：同 key 同 payload 返回原响应快照；同 key 异 payload 409；无记录 None。"""
    prior = db.execute("SELECT request_hash,response_json FROM idempotency_records WHERE scope=? AND key=?",
                       (scope, key)).fetchone()
    if prior is None:
        return None
    if prior["request_hash"] != fingerprint:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
    return prior["response_json"]


def _record(db: sqlite3.Connection, scope: str, key: str, fingerprint: str, response: str, stamp: str) -> None:
    db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)", (scope, key, fingerprint, response, stamp))


def create_task(path: Path, project_id: str, payload: TaskCreate, idempotency_key: str) -> CreatorTask:
    fingerprint = _fingerprint("create", project_id, payload.model_dump_json(by_alias=True))
    scope = f"{ACTOR}:demo:POST/tasks:{project_id}"
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        replay = _replay_or_none(db, scope, idempotency_key, fingerprint)
        if replay is not None:
            return CreatorTask.model_validate_json(replay)
        project = db.execute("SELECT id,status FROM projects WHERE id=? AND mode='demo'", (project_id,)).fetchone()
        if project is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在")
        if project["status"] == "archived":
            raise ApiError(409, "PROJECT_ARCHIVED", "归档项目不能创建任务")
        verdict = bindable_version(db, project_id, payload.platform.value, payload.campaign_phase,
                                   payload.content_type, payload.rule_version_id)
        if not verdict.allowed:
            raise ApiError(409 if verdict.code in {"RULES_NOT_ENABLED", "RULE_SCOPE_MISMATCH"} else 400,
                           verdict.code or "VALIDATION_ERROR", verdict.message or "绑定不兼容")
        if not payload.acknowledge_duplicate:
            duplicate = db.execute("SELECT COUNT(*) FROM creator_tasks WHERE project_id=? AND platform=? "
                                   "AND creator_name=? AND archived_at IS NULL",
                                   (project_id, payload.platform.value, payload.creator_name)).fetchone()[0]
            if duplicate:
                # 契约：疑似重复返回提示不静默合并；用户确认后带 acknowledgeDuplicate=true 的新写才继续。
                raise ApiError(409, "DUPLICATE_SUSPECTED", "同项目同平台已有同名达人任务；确认后携 acknowledgeDuplicate=true 重试",
                               details={"existing": duplicate})
        task_id, stamp = uuid4().hex, now()
        db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,campaign_phase,"
                   "rule_version_id,stage,revision,archived_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,1,NULL,?,?)",
                   (task_id, project_id, payload.creator_name, payload.platform.value, payload.content_type,
                    payload.campaign_phase, payload.rule_version_id, "requirements", stamp, stamp))
        task = _task_from_row(db, get_task(db, task_id))
        _record(db, scope, idempotency_key, fingerprint, task.model_dump_json(by_alias=True), stamp)
        db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                   (uuid4().hex, ACTOR, "creator_task", task_id, "created", "demo",
                    json.dumps({"projectId": project_id, "ruleVersionId": payload.rule_version_id,
                                "acknowledgedDuplicate": payload.acknowledge_duplicate}), stamp))
        return task


def list_tasks(path: Path, project_id: str, *, stage: str | None, page: int, page_size: int) -> tuple[list[CreatorTask], int]:
    with connection(path) as db:
        project = db.execute("SELECT id FROM projects WHERE id=? AND mode='demo'", (project_id,)).fetchone()
        if project is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在")
        clauses, values = ["t.project_id=?", "p.mode='demo'", "t.archived_at IS NULL"], [project_id]
        if stage:
            clauses.append("t.stage=?")
            values.append(stage)
        where = " WHERE " + " AND ".join(clauses)
        total = db.execute("SELECT COUNT(*) FROM creator_tasks t JOIN projects p ON p.id=t.project_id" + where,
                           values).fetchone()[0]
        rows = db.execute(SELECT_TASK + where + " ORDER BY t.updated_at DESC,t.id DESC LIMIT ? OFFSET ?",
                          [*values, page_size, (page - 1) * page_size]).fetchall()
        return [_task_from_row(db, r) for r in rows], total


def patch_task(path: Path, task_id: str, payload: TaskPatch, if_match: str | None, idempotency_key: str) -> CreatorTask:
    fingerprint = _fingerprint("patch", task_id, if_match or "", payload.model_dump_json(by_alias=True))
    scope = f"{ACTOR}:demo:PATCH/task:{task_id}"
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        replay = _replay_or_none(db, scope, idempotency_key, fingerprint)
        if replay is not None:
            return CreatorTask.model_validate_json(replay)  # 回放旧成功：不重查归档/revision
        row = get_task(db, task_id)
        if row["project_status"] == "archived" or row["archived_at"] is not None:
            raise ApiError(409, "TASK_ARCHIVED", "归档项目或任务不能修改")
        _check_revision(row, if_match)
        new_name = payload.creator_name or row["creator_name"]
        new_phase = payload.campaign_phase or row["campaign_phase"]
        phase_changed = new_phase != row["campaign_phase"]
        stamp = now()
        if phase_changed and row["rule_version_id"]:
            verdict = bindable_version(db, row["project_id"], row["platform"], new_phase,
                                       row["content_type"], row["rule_version_id"])
            if not verdict.allowed:
                raise ApiError(409, verdict.code or "RULE_SCOPE_MISMATCH", verdict.message or "新阶段与绑定规则不兼容")
        # A 补充口径：仅名称变化不失效；phase 实际变化才写事件并回退。
        # 回退起点=requirements 是本增量的基础失效状态（资格由 rules_enabled 实时重算），不是自动启用规则；
        # 任何 script/video/human/completed 旧资格相关阶段统一回 requirements，下游消费未实现不假称已撤销。
        if phase_changed:
            stage = "requirements"
        else:
            stage = row["stage"]
        db.execute("UPDATE creator_tasks SET creator_name=?, campaign_phase=?, stage=?, revision=revision+1, updated_at=? "
                   "WHERE id=?", (new_name, new_phase, stage, stamp, task_id))
        if phase_changed:
            db.execute("INSERT INTO task_invalidation_events VALUES (?,?,?,?,?,?,?)",
                       (uuid4().hex, task_id, "phase_change", "活动阶段变更，旧资格阶段回退重审起点",
                        json.dumps({"from_phase": row["campaign_phase"], "to_phase": new_phase,
                                    "from_stage": row["stage"], "to_stage": stage}), "demo", stamp))
        task = _task_from_row(db, get_task(db, task_id))
        _record(db, scope, idempotency_key, fingerprint, task.model_dump_json(by_alias=True), stamp)
        db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                   (uuid4().hex, ACTOR, "creator_task", task_id, "phase_change" if phase_changed else "updated", "demo",
                    json.dumps({"revision": task.revision}), stamp))
        return task


def rebind_task(path: Path, task_id: str, payload: TaskRebind, if_match: str | None,
                idempotency_key: str) -> CreatorTask:
    """显式换绑：真幂等（key+payload 含 If-Match）→兼容校验→绑定史+基础失效事件+audit+revision 原子追加。
    同规则重绑=no-op（不 bump revision 不写失效事件）。阶段/绑定变化后当前资格由 rules_enabled 实时重算
    即失效、stage 保守回 requirements；下游脚本/视频/人工消费失效事件未实现，不假称已全链撤销。"""
    if not payload.acknowledge_invalidation:
        raise ApiError(400, "VALIDATION_ERROR", "换绑必须显式确认失效影响", fields={"acknowledgeInvalidation": "必须为 true"})
    fingerprint = _fingerprint("rebind", task_id, if_match or "", payload.model_dump_json(by_alias=True))
    scope = f"{ACTOR}:demo:POST/rebind:{task_id}"
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        replay = _replay_or_none(db, scope, idempotency_key, fingerprint)
        if replay is not None:
            return CreatorTask.model_validate_json(replay)
        row = get_task(db, task_id)
        if row["project_status"] == "archived" or row["archived_at"] is not None:
            raise ApiError(409, "TASK_ARCHIVED", "归档项目或任务不能换绑")
        _check_revision(row, if_match)
        verdict = bindable_version(db, row["project_id"], row["platform"], row["campaign_phase"],
                                   row["content_type"], payload.rule_version_id)
        if not verdict.allowed:
            raise ApiError(409, verdict.code or "RULE_SCOPE_MISMATCH", verdict.message or "换绑目标不兼容")
        stamp = now()
        if row["rule_version_id"] == payload.rule_version_id:
            # 无变更语义：不写绑定史/失效事件、不 bump revision；仅记审计 noop 并幂等登记。
            task = _task_from_row(db, row)
            _record(db, scope, idempotency_key, fingerprint, task.model_dump_json(by_alias=True), stamp)
            db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                       (uuid4().hex, ACTOR, "creator_task", task_id, "rebind_noop", "demo",
                        json.dumps({"ruleVersionId": payload.rule_version_id}), stamp))
            return task
        key = uuid4().hex
        db.execute("INSERT INTO rule_binding_history VALUES (?,?,?,?,?,?,?,?)",
                   (key, task_id, row["rule_version_id"], payload.rule_version_id, payload.reason,
                    ACTOR, "demo", stamp))
        db.execute("INSERT INTO task_invalidation_events VALUES (?,?,?,?,?,?,?)",
                   (uuid4().hex, task_id, "rebind", payload.reason,
                    json.dumps({"from": row["rule_version_id"], "to": payload.rule_version_id}), "demo", stamp))
        db.execute("UPDATE creator_tasks SET rule_version_id=?, stage='requirements', revision=revision+1, updated_at=? "
                   "WHERE id=?", (payload.rule_version_id, stamp, task_id))
        task = _task_from_row(db, get_task(db, task_id))
        _record(db, scope, idempotency_key, fingerprint, task.model_dump_json(by_alias=True), stamp)
        db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                   (uuid4().hex, ACTOR, "creator_task", task_id, "rebind", "demo",
                    json.dumps({"from": row["rule_version_id"], "to": payload.rule_version_id,
                                "note": "invalidation event only; downstream consumption not implemented"}), stamp))
        return task


def task_detail(path: Path, task_id: str) -> CreatorTask:
    with connection(path) as db:
        row = get_task(db, task_id)
        task = _task_from_row(db, row)
        verdict = rules_enabled(db, task_id)
        from app.modules.foundation.tasks_schemas import EligibilityView
        return task.model_copy(update={"eligibility": EligibilityView(allowed=verdict.allowed,
                                                                      code=verdict.code, message=verdict.message)})


def task_history(path: Path, task_id: str) -> dict:
    with connection(path) as db:
        get_task(db, task_id)
        bindings = db.execute("SELECT id,task_id,from_rule_version_id,to_rule_version_id,reason,actor_id,mode,created_at "
                              "FROM rule_binding_history WHERE task_id=? ORDER BY created_at DESC,id DESC", (task_id,)).fetchall()
        events = db.execute("SELECT id,task_id,kind,reason,details_json,mode,created_at "
                            "FROM task_invalidation_events WHERE task_id=? ORDER BY created_at DESC,id DESC", (task_id,)).fetchall()
        return {
            "bindings": [dict(b) for b in bindings],
            "invalidations": [{**dict(e), "details": json.loads(e["details_json"])} for e in events],
            "jobs": [],   # 明确空态：作业域归 C，接入前不伪装
            "humanReviews": []  # 明确空态：复核域归 D
        }
