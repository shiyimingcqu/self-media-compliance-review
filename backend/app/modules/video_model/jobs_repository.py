"""共享 ReviewJob SQLite repository（D 实施 · t-22 首批）。

真实持久化/CAS 基础——无 worker、无 API、无恢复接管：
- claim_next：单条原子条件 UPDATE（CAS），rowcount=1 才算认领；
  认领后状态已是 processing，调用方【不得】再走会二次 CAS 的
  run_job/claim_and_process（worker 入口另批设计）
- 终态不降级：mark_* 仅在 status='processing' 且 mode 匹配时生效，
  SUCCEEDED/FAILED/INTERRUPTED 落定后任何 mark_* 返回 False
- mode 进所有更新/查询条件（demo/real 隔离，非仅 claim）
- kind 结构校验：script/video 必须 task_id；brief/capability 必须
  source_version_id（input/snapshot 的具体业务结构由各域后续批校验，
  本批只保证行级完整性，不伪填）
- 结果唯一权威：jobs_results 表（mark_succeeded 同事务写入）；
  review_jobs 行不存 result 列
- real 无 adapter 保持拒绝（本批注册表后置，调用方负责；repository
  不假装执行）
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import connection

JOB_KINDS = ("brief", "script", "video", "capability_test")
TASK_KINDS = ("script", "video")  # 必须绑定 task_id 的 kind
SOURCE_KINDS = ("brief",)  # 仅 brief 必须绑定 source_version_id；
# capability_test 用 input 内的测试素材/配置引用，不伪绑定品牌 Brief。
# capability_test 的最小输入结构见 validate_capability_input。


class JobValidationError(ValueError):
    """行级结构校验失败（kind×必填引用组合、JSON 可解析性）。"""


@dataclass(frozen=True)
class ClaimedJob:
    """一次成功 CAS 认领返回的作业快照（status 已为 processing）。

    注意：本 repository 尚未实现 t-18 的 JobRepositoryProtocol 完整
    适配（协议实现属后续 worker 批次）；当前字段供队列管理/测试使用，
    不伪称 protocol 已接通。status/retryable 为最小必备。
    """

    id: str
    kind: str
    task_id: str | None
    source_version_id: str | None
    script_version_id: str | None
    video_file_id: str | None
    rule_version_id: str | None
    mode: str
    status: str
    snapshot: dict
    input: dict
    provider_request_id: str | None
    submission_state: str
    error_message: str | None
    retryable: bool
    attempt: int
    retry_of: str | None
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_job_shape(kind: str, task_id: str | None, source_version_id: str | None,
                       snapshot: dict, input: dict) -> None:
    """行级结构校验（不校验业务语义——各 kind 的 input/snapshot 细节后批）。

    - kind 闭集
    - script/video → task_id 必填；brief → source_version_id 必填；
      capability_test → 无需 task/source（测试素材与配置引用在 input 内）
    - snapshot/input 必须是可序列化 JSON 对象（非空顶层结构）
    - capability_test 的 input 最小结构：testInput（测试素材引用）与
      config（配置引用）至少各有一个非空字段
    """
    if kind not in JOB_KINDS:
        raise JobValidationError(f"kind 必须是 {JOB_KINDS} 之一，收到 {kind!r}")
    if kind in TASK_KINDS and not task_id:
        raise JobValidationError(f"{kind} 作业必须绑定 task_id，不伪填")
    if kind in SOURCE_KINDS and not source_version_id:
        raise JobValidationError(f"{kind} 作业必须绑定 source_version_id，不伪填")
    if not isinstance(snapshot, dict) or not snapshot:
        raise JobValidationError("snapshot 必须是非空 JSON 对象")
    if not isinstance(input, dict) or not input:
        raise JobValidationError("input 必须是非空 JSON 对象")
    if kind == "capability_test":
        test_input = input.get("testInput")
        config = input.get("config")
        if not isinstance(test_input, dict) or not test_input:
            raise JobValidationError("capability_test 的 input.testInput 必须是非空对象（测试素材引用）")
        if not isinstance(config, dict) or not config:
            raise JobValidationError("capability_test 的 input.config 必须是非空对象（配置引用）")


ACTOR = "local-reviewer"  # 服务端固定本地身份；不接受客户端输入（A 裁决）


class SqliteJobRepository:
    """review_jobs/jobs_results 的持久化实现（连接由调用方管理生命周期）。

    约束固化（与迁移 CHECK 一致）：
    - CAS：claim_next 两步确定性认领——先 SELECT 候选 id（ORDER BY rowid
      FIFO），再对该 id 做 UPDATE ... WHERE status='queued' AND mode=?
      （rowcount=1 才算认领）。两连接竞争同一候选行时恰有一方成功。
    - 终态不降级：所有 mark_* 带 status='processing' AND mode=? 条件
    - 半态防护：mark_succeeded 用 SAVEPOINT 包裹状态+结果写入——方法
      内任一步失败整体回滚到调用前，不残留"succeeded 无结果"半态；
      调用方事务边界不受影响（不替调用方 commit）
    - 审计：create/终态 同事务写 audit_events（actor 固定本地身份）；
      方法失败不留审计残留
    - 引用一致性：create 时核查 task/source/file 所属项目的 mode 与
      作业 mode 一致（demo job 不指向 real task）
    - 不实现恢复/接管：启动时不会把 processing 打断（另一 worker 可能
      活跃；owner/lease/fencing 另批），本批不暴露恢复入口
    """

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    # ---------- 引用一致性（mode 核查） ----------

    def _check_reference_modes(self, mode: str, task_id: str | None,
                               source_version_id: str | None, video_file_id: str | None) -> None:
        """引用行所属项目的 mode 必须与作业 mode 一致（demo≠real 混绑拒）。"""
        if task_id:
            row = self._db.execute(
                "SELECT p.mode FROM creator_tasks t JOIN projects p ON p.id=t.project_id "
                "WHERE t.id=?", (task_id,)).fetchone()
            if row is None:
                raise JobValidationError(f"task {task_id} 不存在")
            if row["mode"] != mode:
                raise JobValidationError(f"task {task_id} 所属项目 mode={row['mode']} 与作业 mode={mode} 不一致")
        if source_version_id:
            row = self._db.execute(
                "SELECT p.mode FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id "
                "JOIN projects p ON p.id=b.project_id WHERE s.id=?", (source_version_id,)).fetchone()
            if row is None:
                raise JobValidationError(f"source_version {source_version_id} 不存在")
            if row["mode"] != mode:
                raise JobValidationError(f"source_version {source_version_id} 项目 mode={row['mode']} 与作业 mode={mode} 不一致")
        if video_file_id:
            row = self._db.execute(
                "SELECT mode FROM file_assets WHERE id=?", (video_file_id,)).fetchone()
            if row is None:
                raise JobValidationError(f"file {video_file_id} 不存在")
            if row["mode"] != mode:
                raise JobValidationError(f"file {video_file_id} mode={row['mode']} 与作业 mode={mode} 不一致")

    # ---------- 审计（同事务；失败不留残留） ----------

    def _audit(self, job_id: str, action: str, mode: str, details: dict, stamp: str) -> None:
        from uuid import uuid4
        self._db.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            (uuid4().hex, ACTOR, "review_job", job_id, action, mode,
             json.dumps(details, ensure_ascii=False), stamp))

    # ---------- 创建（Web 侧短事务插入 queued 行） ----------

    def create_job(self, *, kind: str, mode: str, snapshot: dict, input: dict,
                   task_id: str | None = None, source_version_id: str | None = None,
                   script_version_id: str | None = None, video_file_id: str | None = None,
                   rule_version_id: str | None = None, attempt: int = 0,
                   retry_of: str | None = None, job_id: str | None = None) -> str:
        validate_job_shape(kind, task_id, source_version_id, snapshot, input)
        self._check_reference_modes(mode, task_id, source_version_id, video_file_id)
        job_id = job_id or _new_id()
        stamp = _now()
        self._db.execute(
            "INSERT INTO review_jobs (id,kind,task_id,source_version_id,script_version_id,"
            "video_file_id,rule_version_id,mode,status,snapshot_json,input_json,"
            "submission_state,attempt,retry_of,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'queued',?,?,?,?,?,?,?)",
            (job_id, kind, task_id, source_version_id, script_version_id, video_file_id,
             rule_version_id, mode, json.dumps(snapshot, ensure_ascii=False),
             json.dumps(input, ensure_ascii=False), "not_sent", attempt, retry_of, stamp, stamp))
        self._audit(job_id, "created", mode, {"kind": kind, "attempt": attempt,
                                              "retryOf": retry_of}, stamp)
        return job_id

    # ---------- 认领（单次 CAS；认领后不得再二次 CAS） ----------

    def claim_next(self, mode: str) -> ClaimedJob | None:
        """原子认领一个 queued 作业（FIFO by created_at,id）。

        两步确定性 CAS：先选候选 id，再对该 id 做
        UPDATE ... WHERE id=? AND status='queued' AND mode=?（rowcount=1
        才算认领）。并发两连接竞争同一候选行时恰有一方 rowcount=1；
        败方得 None（不回写、不改胜者状态），下轮轮询取下一行。
        认领成功后状态已是 processing——调用方直接处理该作业，
        不得再调用任何 claim/run_job 二次认领。
        """
        candidate_row = self._db.execute(
            "SELECT id FROM review_jobs WHERE status='queued' AND mode=? "
            "ORDER BY rowid LIMIT 1", (mode,)).fetchone()
        if candidate_row is None:
            return None
        candidate = candidate_row["id"]
        stamp = _now()
        cur = self._db.execute(
            "UPDATE review_jobs SET status='processing', updated_at=? "
            "WHERE id=? AND status='queued' AND mode=?",
            (stamp, candidate, mode))
        if cur.rowcount != 1:
            return None  # 他人已认领该行：败者零写入
        self._audit(candidate, "claimed", mode, {"claimedAt": stamp}, stamp)
        row = self._db.execute(
            "SELECT * FROM review_jobs WHERE id=?", (candidate,)).fetchone()
        return _claimed_from_row(row) if row else None

    # ---------- 终态与中断（条件更新：终态不可降级/覆盖） ----------

    def mark_succeeded(self, job_id: str, mode: str, result: dict) -> bool:
        """processing→succeeded；结果写入 jobs_results（唯一权威）。

        SAVEPOINT 半态防护：状态 UPDATE 与结果 INSERT 在同一局部保存点内，
        任一步失败整体回滚——即便调用方随后 catch 并 commit 事务，也不会
        留下"succeeded 无结果"半态（不替调用方 commit/rollback 外层事务）。
        仅当当前 status='processing' 且 mode 匹配时生效；已有终态一律 False。
        """
        if not isinstance(result, dict) or not result:
            raise JobValidationError("result 必须是非空 JSON 对象")
        stamp = _now()
        self._db.execute("SAVEPOINT sp_mark_succeeded")
        try:
            cur = self._db.execute(
                "UPDATE review_jobs SET status='succeeded', updated_at=? "
                "WHERE id=? AND status='processing' AND mode=?", (stamp, job_id, mode))
            if cur.rowcount != 1:
                self._db.execute("ROLLBACK TO sp_mark_succeeded")
                self._db.execute("RELEASE sp_mark_succeeded")
                return False
            try:
                cur = self._db.execute(
                    "INSERT INTO jobs_results (job_id, result_json, created_at) VALUES (?,?,?)",
                    (job_id, json.dumps(result, ensure_ascii=False), stamp))
            except sqlite3.IntegrityError:
                # 已有结果行（PK 冲突）：回滚状态变更，返回 False——不残留半态
                self._db.execute("ROLLBACK TO sp_mark_succeeded")
                self._db.execute("RELEASE sp_mark_succeeded")
                return False
            if cur.rowcount != 1:
                self._db.execute("ROLLBACK TO sp_mark_succeeded")
                self._db.execute("RELEASE sp_mark_succeeded")
                return False
            self._audit(job_id, "succeeded", mode, {"resultBytes": len(json.dumps(result))}, stamp)
            self._db.execute("RELEASE sp_mark_succeeded")
            return True
        except BaseException:
            self._db.execute("ROLLBACK TO sp_mark_succeeded")
            self._db.execute("RELEASE sp_mark_succeeded")
            raise

    def mark_failed(self, job_id: str, mode: str, error_message: str, *, retryable: bool) -> bool:
        """processing→failed。终态后拒绝；mode 不匹配拒绝。"""
        stamp = _now()
        cur = self._db.execute(
            "UPDATE review_jobs SET status='failed', error_message=?, retryable=?, updated_at=? "
            "WHERE id=? AND status='processing' AND mode=?",
            (error_message, 1 if retryable else 0, stamp, job_id, mode))
        if cur.rowcount != 1:
            return False
        self._audit(job_id, "failed", mode, {"retryable": retryable,
                                              "error": error_message[:200]}, stamp)
        return True

    def mark_interrupted(self, job_id: str, mode: str, reason: str) -> bool:
        """processing→interrupted（状态不确定）。终态后拒绝；mode 不匹配拒绝。"""
        stamp = _now()
        cur = self._db.execute(
            "UPDATE review_jobs SET status='interrupted', error_message=?, updated_at=? "
            "WHERE id=? AND status='processing' AND mode=?",
            (reason, stamp, job_id, mode))
        if cur.rowcount != 1:
            return False
        self._audit(job_id, "interrupted", mode, {"reason": reason[:200]}, stamp)
        return True

    # ---------- 查询（mode 一致进全部条件） ----------

    def find_by_id(self, job_id: str, mode: str) -> ClaimedJob | None:
        row = self._db.execute(
            "SELECT * FROM review_jobs WHERE id=? AND mode=?", (job_id, mode)).fetchone()
        return _claimed_from_row(row) if row else None

    def get_result(self, job_id: str, mode: str) -> dict | None:
        """读取 succeeded 作业的权威结果；非 succeeded/不存在返回 None。"""
        row = self._db.execute(
            "SELECT r.result_json FROM jobs_results r JOIN review_jobs j ON j.id=r.job_id "
            "WHERE r.job_id=? AND j.mode=? AND j.status='succeeded'", (job_id, mode)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def record_provider_request(self, job_id: str, mode: str, provider_request_id: str | None,
                                submission_state: str) -> bool:
        """登记供应商提交状态（仅 processing 中；追溯线索）。

        provider_request_id 可为 None：此时仅更新 submission_state
        （例如 UnknownSubmissionError 发生但尚无 request_id——提交
        状态未知仍必须持久化，不能让行停留在 not_sent 造成误判）。
        submission_state 闭集校验不变。
        """
        if submission_state not in ("not_sent", "submitted", "unknown"):
            raise JobValidationError(f"submission_state 非法：{submission_state!r}")
        if provider_request_id is None:
            cur = self._db.execute(
                "UPDATE review_jobs SET submission_state=?, updated_at=? "
                "WHERE id=? AND status='processing' AND mode=?",
                (submission_state, _now(), job_id, mode))
            return cur.rowcount == 1
        cur = self._db.execute(
            "UPDATE review_jobs SET provider_request_id=?, submission_state=?, updated_at=? "
            "WHERE id=? AND status='processing' AND mode=?",
            (provider_request_id, submission_state, _now(), job_id, mode))
        return cur.rowcount == 1


def _claimed_from_row(row: sqlite3.Row) -> ClaimedJob:
    return ClaimedJob(
        id=row["id"], kind=row["kind"], task_id=row["task_id"],
        source_version_id=row["source_version_id"],
        script_version_id=row["script_version_id"], video_file_id=row["video_file_id"],
        rule_version_id=row["rule_version_id"], mode=row["mode"],
        status=row["status"],
        snapshot=json.loads(row["snapshot_json"]), input=json.loads(row["input_json"]),
        provider_request_id=row["provider_request_id"],
        submission_state=row["submission_state"],
        error_message=row["error_message"],
        retryable=bool(row["retryable"]), attempt=row["attempt"],
        retry_of=row["retry_of"], created_at=row["created_at"], updated_at=row["updated_at"])


def _new_id() -> str:
    from uuid import uuid4
    return uuid4().hex
