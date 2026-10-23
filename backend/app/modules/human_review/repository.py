"""D t-34：人工复核持久化 repository（DB 层；API 当前性检查在此执行）。

安全基线（A 12:33 验收条件）：
- 事务顺序：授权（mode/project/task）→ 幂等回放 → If-Match/终态/当前快照检查 → 新写
- jobs_results 严格解析 ReviewResult：result.jobId/snapshot 必须==job 行；
  解析失败不降级成 []后放行 approve——直接失败
- approve 需当前绑定规则/脚本/视频版本无后续失效、task/project 未归档；
  缺权威 video 版本/脚本资格能力明确禁 approve（查不到表≠有效）；
  列表/详情可读并显式 stale
- script changes_requested/incomplete 不得人工直接 approve 绕过；
  needs_confirmation 满足 gate 才解除脚本资格
- 最终决定+issue 记录+audit+任务阶段/修订原子事务；script 批准只到
  video 不完成任务，video 批准才 completed，退回对应阶段
- scope 由服务端依 job.kind 决定，不信任客户端
- actor=local-reviewer 服务端固定
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.contracts import ReviewScope, ReviewSnapshot
from app.core.errors import ApiError
from app.core.review_contracts import AnalysisIntegrity, ReviewResult
from app.modules.human_review import decision
from app.modules.human_review.schemas import (
    IssueDecisionInput,
    IssueRef,
    ManualIssueInput,
)
from app.modules.human_review.schemas_impl import (
    DecisionBody,
    DecisionResult,
    DraftBody,
    ReviewDetail,
    ReviewItem,
)

ACTOR = "local-reviewer"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(*parts: str) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()


def _replay_or_none(db: sqlite3.Connection, scope: str, key: str, fp: str) -> str | None:
    prior = db.execute("SELECT request_hash,response_json FROM idempotency_records "
                       "WHERE scope=? AND key=?", (scope, key)).fetchone()
    if prior is None:
        return None
    if prior["request_hash"] != fp:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
    return prior["response_json"]


def _record(db: sqlite3.Connection, scope: str, key: str, fp: str, response: str) -> None:
    db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)",
               (scope, key, fp, response, now()))


def _audit(db: sqlite3.Connection, entity_id: str, action: str, mode: str, details: dict) -> None:
    db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
               (uuid4().hex, ACTOR, "human_review", entity_id, action, mode,
                json.dumps(details, ensure_ascii=False), now()))


def _load_job_context(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    """授权链：job→task→project（mode/归属一致性；失败 404）。"""
    row = db.execute(
        "SELECT j.*, t.project_id AS task_project, t.rule_version_id AS task_rule, "
        "t.archived_at AS task_archived, t.stage AS task_stage, t.revision AS task_revision, "
        "p.mode AS project_mode, p.status AS project_status "
        "FROM review_jobs j JOIN creator_tasks t ON t.id=j.task_id "
        "JOIN projects p ON p.id=t.project_id WHERE j.id=?", (job_id,)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "作业不存在或不属于可复核范围")
    return row


def _parse_result(db: sqlite3.Connection, job_row: sqlite3.Row,
                  *, job_id: str | None = None) -> ReviewResult:
    """严格解析 jobs_results：result.jobId==job 行；解析失败直接 409（不降级）。

    job_id 可由调用方显式给出——详情路径传入的是 human_reviews 行（其 id 是
    review_id），必须用行内 job_id 列定位作业结果，不能误用 row["id"]。
    """
    effective_job_id = job_id if job_id is not None else job_row["id"]
    rr = db.execute("SELECT result_json FROM jobs_results WHERE job_id=?",
                    (effective_job_id,)).fetchone()
    if rr is None:
        raise ApiError(409, "REVIEW_INCOMPLETE", "作业尚无结果——不可创建复核")
    try:
        result = ReviewResult.model_validate_json(rr["result_json"])
    except Exception:
        raise ApiError(422, "INVALID_MODEL_RESULT", "作业结果结构非法——复核拒绝（不降级放行）")
    if result.job_id != effective_job_id:
        raise ApiError(422, "INVALID_MODEL_RESULT", "结果 jobId 与作业不一致")
    snap = ReviewSnapshot.model_validate(json.loads(job_row["snapshot_json"]))
    if result.snapshot != snap:
        raise ApiError(422, "INVALID_MODEL_RESULT", "结果快照与作业行不一致")
    return result


def _stale_reasons(db: sqlite3.Connection, job: sqlite3.Row) -> list[str]:
    """权威引用失效检查（approve 禁；列表/详情显式 stale 可读）。

    注：creator_tasks 当前无 script_version_id/video_version_id 列（脚本/视频
    版本归属表尚未落地）——脚本一致性用 job 行自身的 script_version_id 判定；
    缺权威版本事实时**不推断为有效**（后续接入表后再加列比对）。
    """
    reasons: list[str] = []
    snap = ReviewSnapshot.model_validate(json.loads(job["snapshot_json"]))
    if job["task_rule"] != snap.rule_version_id:
        reasons.append("task_rebind_stale")
    if job["task_archived"] is not None:
        reasons.append("task_archived")
    if job["project_status"] == "archived":
        reasons.append("project_archived")
    invalidated = db.execute(
        "SELECT COUNT(*) FROM task_invalidation_events WHERE task_id=? AND created_at > ?",
        (job["task_id"], job["created_at"])).fetchone()[0]
    if invalidated:
        reasons.append("invalidation_after_job")
    return reasons


def _scope_of(kind: str) -> ReviewScope:
    """服务端依 job.kind 决定 scope（不信任客户端）。"""
    if kind == "script":
        return ReviewScope.SCRIPT
    if kind == "video":
        return ReviewScope.VIDEO
    raise ApiError(409, "VALIDATION_ERROR", f"作业类型 {kind} 不可创建人工复核")


def create_review(db: sqlite3.Connection, job_id: str, idempotency_key: str) -> str:
    """POST /jobs/:job_id/human-reviews：succeeded 真实 job 才建；UNIQUE 幂等。

    幂等 scope 按 actor:mode:endpoint（不含资源 id）——同 key 不同 job 视为
    不同 payload → IDEMPOTENCY_CONFLICT；同 job 重放返回既有待办。
    """
    job = _load_job_context(db, job_id)
    fp = _fingerprint("create-review", job_id)
    scope_key = f"{ACTOR}:{job['project_mode']}:POST/human-reviews"
    replay = _replay_or_none(db, scope_key, idempotency_key, fp)
    if replay is not None:
        return json.loads(replay)["id"]
    if job["status"] != "succeeded":
        raise ApiError(409, "REVIEW_INCOMPLETE",
                       f"作业状态 {job['status']} 非 succeeded——不可创建复核")
    result = _parse_result(db, job)  # 严格解析（失败即拒，不降级）
    existing = db.execute("SELECT id FROM human_reviews WHERE job_id=?", (job_id,)).fetchone()
    if existing:
        _record(db, scope_key, idempotency_key, fp,
                json.dumps({"id": existing["id"]}))
        return existing["id"]
    review_id, stamp = uuid4().hex, now()
    # 显式列序：mode 先于 state（D_0001 定义 mode 在第 6 列、state 在第 5 列）
    db.execute("INSERT INTO human_reviews (id,job_id,task_id,scope,state,mode,revision,"
               "created_at,updated_at) VALUES (?,?,?,?,'pending',?,1,?,?)",
               (review_id, job_id, job["task_id"], _scope_of(job["kind"]).value,
                job["project_mode"], stamp, stamp))
    _record(db, scope_key, idempotency_key, fp, json.dumps({"id": review_id}))
    _audit(db, review_id, "created", job["project_mode"], {"jobId": job_id})
    return review_id


def _review_row(db: sqlite3.Connection, review_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT r.*, j.kind AS job_kind, j.task_id, j.snapshot_json, "
        "j.script_version_id AS job_script, j.created_at AS job_created, "
        "t.creator_name, t.platform, t.rule_version_id AS task_rule, "
        "t.archived_at AS task_archived, t.stage AS task_stage, "
        "p.status AS project_status, p.mode AS project_mode "
        "FROM human_reviews r JOIN review_jobs j ON j.id=r.job_id "
        "JOIN creator_tasks t ON t.id=r.task_id JOIN projects p ON p.id=t.project_id "
        "WHERE r.id=?", (review_id,)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "复核不存在")
    return row


def _detail_from(db: sqlite3.Connection, row: sqlite3.Row) -> ReviewDetail:
    result = _parse_result(db, row, job_id=row["job_id"])
    snap = ReviewSnapshot.model_validate(json.loads(row["snapshot_json"]))
    # Issue 是 Pydantic 模型（非 dict）：按属性取，不按下标
    issues = [IssueRef(issue_id=i.id, importance=i.importance, origin=i.origin)
              for i in result.issues]
    draft_row = db.execute("SELECT draft_json FROM review_drafts WHERE review_id=?",
                           (row["id"],)).fetchone()
    stale = _stale_reasons(db, row)
    return ReviewDetail(
        id=row["id"], job_id=row["job_id"], task_id=row["task_id"],
        scope=ReviewScope(row["scope"]), state=row["state"], mode=row["mode"],
        revision=row["revision"], snapshot=snap,
        conclusion=result.conclusion.value, integrity=result.integrity,
        issues=issues, draft=json.loads(draft_row["draft_json"]) if draft_row else None,
        stale=bool(stale), stale_reasons=stale,
        created_at=row["created_at"], updated_at=row["updated_at"])


def get_review(db: sqlite3.Connection, review_id: str) -> ReviewDetail:
    return _detail_from(db, _review_row(db, review_id))


def list_reviews(db: sqlite3.Connection, *, state: str | None, q: str | None,
                 platform: str | None, page: int, page_size: int) -> tuple[list[ReviewItem], int]:
    """基线列表：state 默认 pending/draft；q 匹配达人/项目；platform 筛选。"""
    clauses, values = ["1=1"], []
    if state:
        clauses.append("r.state=?"); values.append(state)
    else:
        clauses.append("r.state IN ('pending','draft')")
    if q:
        clauses.append("(t.creator_name LIKE ? OR p.name LIKE ?)")
        values.extend([f"%{q}%", f"%{q}%"])
    if platform:
        clauses.append("t.platform=?"); values.append(platform)
    where = " WHERE " + " AND ".join(clauses)
    base = ("FROM human_reviews r JOIN review_jobs j ON j.id=r.job_id "
            "JOIN creator_tasks t ON t.id=r.task_id JOIN projects p ON p.id=t.project_id")
    total = db.execute("SELECT COUNT(*) " + base + where, values).fetchone()[0]
    rows = db.execute(
        "SELECT r.*, t.creator_name, t.platform, j.snapshot_json, "
        "j.created_at AS job_created, t.rule_version_id AS task_rule, "
        "t.archived_at AS task_archived, p.status AS project_status, p.mode AS project_mode "
        + base + where
        + " ORDER BY r.updated_at DESC, r.id DESC LIMIT ? OFFSET ?",
        [*values, page_size, (page - 1) * page_size]).fetchall()
    items = []
    for row in rows:
        rr = db.execute("SELECT result_json FROM jobs_results WHERE job_id=?",
                        (row["job_id"],)).fetchone()
        conclusion = None
        if rr:
            try:
                conclusion = ReviewResult.model_validate_json(rr["result_json"]).conclusion.value
            except Exception:
                conclusion = None  # 列表可读：解析失败显式 None（详情才严格拒）
        items.append(ReviewItem(
            id=row["id"], job_id=row["job_id"], task_id=row["task_id"],
            scope=ReviewScope(row["scope"]), state=row["state"], mode=row["mode"],
            revision=row["revision"], creator_name=row["creator_name"],
            platform=row["platform"], conclusion=conclusion,
            created_at=row["created_at"], updated_at=row["updated_at"],
            stale=bool(_stale_reasons(db, row))))
    return items, total


def save_draft(db: sqlite3.Connection, review_id: str, body: DraftBody,
               if_match: str | None, idempotency_key: str) -> ReviewDetail:
    row = _review_row(db, review_id)
    fp = _fingerprint("draft", review_id, if_match or "", body.model_dump_json(by_alias=True))
    scope_key = f"{ACTOR}:{row['project_mode']}:PATCH/draft:{review_id}"
    db.execute("BEGIN IMMEDIATE")
    replay = _replay_or_none(db, scope_key, idempotency_key, fp)
    if replay is not None:
        return ReviewDetail.model_validate_json(replay)
    _check_revision(row, if_match)
    if row["state"] not in ("pending", "draft"):
        raise ApiError(409, "REVIEW_STATE_CONFLICT", f"终态 {row['state']} 不可保存草稿")
    stamp = now()
    db.execute("INSERT INTO review_drafts (review_id,draft_json,updated_at) VALUES (?,?,?) "
               "ON CONFLICT(review_id) DO UPDATE SET draft_json=excluded.draft_json,"
               "updated_at=excluded.updated_at",
               (review_id, body.model_dump_json(by_alias=True), stamp))
    db.execute("UPDATE human_reviews SET state='draft', revision=revision+1, updated_at=? "
               "WHERE id=?", (stamp, review_id))
    _record(db, scope_key, idempotency_key, fp, _detail_from(db, _review_row(db, review_id)).model_dump_json(by_alias=True))
    _audit(db, review_id, "draft_saved", row["project_mode"], {})
    return _detail_from(db, _review_row(db, review_id))


def _check_revision(row: sqlite3.Row, if_match: str | None) -> None:
    if if_match is None:
        raise ApiError(428, "PRECONDITION_REQUIRED", "必须携带 If-Match revision")
    try:
        if int(if_match) != row["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "复核已被更新，请读取新版本")
    except ValueError:
        raise ApiError(400, "VALIDATION_ERROR", "If-Match 必须是整数", fields={"If-Match": if_match})


_TARGET_STAGE = {"script": "video", "video": "completed"}


def submit_decision(db: sqlite3.Connection, review_id: str, body: DecisionBody,
                    if_match: str | None, idempotency_key: str) -> DecisionResult:
    """POST /human-reviews/:id/decision：原子事务（gate+issue+audit+任务阶段）。"""
    row = _review_row(db, review_id)
    fp = _fingerprint("decision", review_id, if_match or "",
                      body.model_dump_json(by_alias=True))
    scope_key = f"{ACTOR}:{row['project_mode']}:POST/decision:{review_id}"
    db.execute("BEGIN IMMEDIATE")
    replay = _replay_or_none(db, scope_key, idempotency_key, fp)
    if replay is not None:
        return DecisionResult.model_validate_json(replay)
    # 授权→幂等→If-Match→终态→当前快照（顺序固定）
    _check_revision(row, if_match)
    if row["state"] in ("approved", "returned", "superseded"):
        raise ApiError(409, "REVIEW_STATE_CONFLICT", f"终态 {row['state']} 不可重复提交")
    # 严格解析（失败即拒不降级）
    result = _parse_result(db, row, job_id=row["job_id"])
    snap = ReviewSnapshot.model_validate(json.loads(row["snapshot_json"]))
    # 权威失效检查
    stale = _stale_reasons(db, row)
    # manualIssues 结构校验（纯函数）+ decision 重复/未知 ID 校验
    for mi in body.manual_issues:
        violations = decision.validate_manual_issue(mi, duration_ms=None)
        if violations:
            raise ApiError(400, "VALIDATION_ERROR", "人工问题结构非法")
    seen_ids = set()
    for d in body.issue_decisions:
        if d.issue_id in seen_ids:
            raise ApiError(400, "VALIDATION_ERROR", f"问题 {d.issue_id} 重复裁决")
        seen_ids.add(d.issue_id)
    known_ids = {i.id for i in result.issues}
    for d in body.issue_decisions:
        if d.issue_id not in known_ids:
            raise ApiError(400, "VALIDATION_ERROR", f"问题 {d.issue_id} 不存在于本结果")
    # 纯 gate 评估（服务端组装 payload——不信任客户端自证）
    payload, denial = _gate_payload(row, result, snap, body, stale)
    if denial is not None:
        raise ApiError(409, denial.code, denial.message)
    evaluation = decision.evaluate_final_decision(payload)
    if not evaluation.allowed:
        first = evaluation.violations[0]
        raise ApiError(409, first.code, first.message)
    # 原子写：issue_decisions + final_decisions + 状态 + 任务阶段 + audit
    stamp = now()
    for d in body.issue_decisions:
        db.execute("INSERT INTO review_issue_decisions (id,review_id,issue_key,action,reason,"
                   "created_at) VALUES (?,?,?,?,?,?)",
                   (uuid4().hex, review_id, d.issue_id, d.action, d.reason or "", stamp))
    new_stage = (_TARGET_STAGE[row["scope"]] if body.decision == "approved"
                 else ("script" if row["scope"] == "video" else "requirements"))
    db.execute("INSERT INTO final_decisions (id,review_id,decision,comment,snapshot_json,"
               "result_ref,actor_id,simulated,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (uuid4().hex, review_id, body.decision, body.comment,
                snap.model_dump_json(by_alias=True), row["job_id"], ACTOR,
                1 if evaluation.simulated else 0, stamp))
    db.execute("UPDATE human_reviews SET state=?, revision=revision+1, updated_at=? WHERE id=?",
               (body.decision, stamp, review_id))
    db.execute("UPDATE creator_tasks SET stage=?, revision=revision+1, updated_at=? WHERE id=?",
               (new_stage, stamp, row["task_id"]))
    out = DecisionResult(review_id=review_id, decision=body.decision,
                         simulated=evaluation.simulated, task_stage=new_stage,
                         revision=row["revision"] + 1)
    _record(db, scope_key, idempotency_key, fp, out.model_dump_json(by_alias=True))
    _audit(db, review_id, body.decision, row["project_mode"],
           {"jobId": row["job_id"], "taskStage": new_stage,
            "simulated": evaluation.simulated})
    return out


def _gate_payload(row: sqlite3.Row, result: ReviewResult, snap: ReviewSnapshot,
                  body: DecisionBody, stale: list[str]):
    """服务端组装 gate 输入，并做 API 当前性强化（gate 本身不看 DB）。

    返回 (payload, denial)：denial 非 None 时直接拒绝（当前性门禁）。
    """
    from app.core.contracts import Conclusion
    from app.modules.human_review.schemas import (
        FinalDecisionPayload, GateViolation,
    )

    issues = [IssueRef(issue_id=i.id, importance=i.importance, origin=i.origin)
              for i in result.issues]
    for mi in body.manual_issues:
        issues.append(IssueRef(issue_id=f"manual-{len(issues) + 1}",
                               importance=mi.importance, origin="human"))
    payload = FinalDecisionPayload(
        scope=ReviewScope(row["scope"]), decision=body.decision, comment=body.comment,
        snapshot=snap, integrity=result.integrity, issues=issues,
        decisions=body.issue_decisions)
    if body.decision == "approved":
        if stale:
            return payload, GateViolation(code="STALE_REFERENCES",
                                           message="权威引用已失效，不可通过")
        # script scope：源结论必须 needs_confirmation
        # （changes_requested/incomplete 不得人工直接通过）
        if row["scope"] == "script" and result.conclusion != Conclusion.NEEDS_CONFIRMATION:
            return payload, GateViolation(
                code="SCRIPT_CONCLUSION_NOT_CONFIRMABLE",
                message=f"脚本结论 {result.conclusion.value} 不可人工直接通过")
    return payload, None
