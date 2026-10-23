"""独立 worker 的 run_once 最小纵向切片（D t-24 修订版；函数级，无常驻循环）。

三段式短事务（A 纠正②）：
  1. 短事务 A：claim_next 认领 + commit（释放写锁）
  2. 入参先验 + 快照校验（adapter 调用【前】，失败零 adapter 调用）
  3. 无事务：adapter 计算（本批仅 demo+video fake；无网络）
  4. 短事务 B：持久化 provider 状态/终态 + 结果 + audit

首批硬边界（A 纠正①）：mode=real 与 kind!=video 一律明确拒绝——
即便注册表误注入 ('real','video') 适配器也【零调用】。demo fake
绝不在 real 作业上运行。

快照校验（A 纠正②，adapter 前置）：
  - ReviewSnapshot 构造合法（不合法 failed，不造 unknown 占位）
  - snapshot.mode == 作业行 mode（不信 fake/行外数据）
  - snapshot.ruleVersionId == 作业行 rule_version_id
  - ruleVersionId 实查 rule_versions 存在且 state='enabled'（表已存在）
  - script 一致性：script 版本表未建，【未实现边界】——本批仅接受
    可完全验证的 demo fixture，不声称完整视频业务准入
video 入参先验（A 纠正④）：file 存在/purpose=video/mode 一致。
异常映射按 t-18 四层；UnknownSubmissionError 无论有无 request_id
都持久化 submission_state='unknown'（A 纠正③）。
无恢复/接管：不捞 processing、不打断其他 worker。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.core.database import connection
from app.modules.video_model import jobs_repository as jr
from app.modules.video_model.adapter import (
    InvalidAnalysisStructure,
    SafeRetryableError,
    UnknownSubmissionError,
)
from app.modules.video_model.jobs_adapter import RepoJobAdapter, SnapshotInvalid
from app.modules.video_model.service import VideoJobService
from app.core.review_contracts import ReviewSnapshot

# 首批允许的 (mode, kind)：仅 demo+video。real/其他 kind 即便注册表
# 有适配器也硬拒（防误注入 fake 跑 real）。
_ALLOWED = {("demo", "video")}

AdapterRegistry = dict  # dict[tuple[str, str], ModelAdapterProtocol]


@dataclass(frozen=True)
class RunOnceResult:
    claimed: bool
    job_id: str | None = None
    status: str | None = None
    detail: str = ""


def run_once(db_path: Path, mode: str, adapters: AdapterRegistry) -> RunOnceResult:
    """认领并执行至多一个作业（demo+video fake 纵向切片）。"""
    if mode not in ("demo", "real"):
        raise ValueError(f"mode 必须是 demo/real，收到 {mode!r}")

    # ---- 段1：短事务认领并提交（释放写锁） ----
    with connection(db_path) as db:
        repo = jr.SqliteJobRepository(db)
        claimed = repo.claim_next(mode)
    if claimed is None:
        return RunOnceResult(claimed=False, detail="empty")
    job_id = claimed.id

    # ---- 首批硬边界（纠正①）：real / 非 video 即便有适配器也零调用 ----
    if (mode, claimed.kind) not in _ALLOWED:
        detail = (f"首批仅支持 demo/video；mode={mode} kind={claimed.kind} 明确拒绝，"
                  "注册表即便有适配器也不执行")
        return _fail(db_path, mode, job_id, detail, retryable=False, code="out_of_scope")

    adapter = adapters.get((mode, claimed.kind))
    if adapter is None:
        detail = f"mode={mode} kind={claimed.kind} 无已实现适配器，不伪造执行结果"
        return _fail(db_path, mode, job_id, detail, retryable=False, code="no_adapter")

    # ---- 入参先验（adapter 前；缺引用 failed 不网络） ----
    precondition = _precheck(db_path, mode, claimed)
    if precondition is not None:
        return _fail(db_path, mode, job_id, precondition, retryable=False, code="precondition")

    # ---- 快照校验（纠正②：adapter 调用前；失败零 adapter 调用） ----
    snapshot_error = _validate_snapshot(db_path, mode, claimed)
    if snapshot_error is not None:
        code, message = snapshot_error
        return _fail(db_path, mode, job_id, message, retryable=False, code=code)

    # ---- 段2：无事务计算（demo fake，无网络） ----
    try:
        analysis = adapter.analyze_video(
            claimed.video_file_id or "", claimed.script_version_id,
            claimed.rule_version_id or "", claimed.snapshot.get("modelConfigId", ""))
    except InvalidAnalysisStructure as exc:
        code, message = _safe_error(exc)
        return _fail(db_path, mode, job_id, message, retryable=False, code=code)
    except SafeRetryableError as exc:
        code, message = _safe_error(exc)
        return _fail(db_path, mode, job_id, message, retryable=True, code=code)
    except UnknownSubmissionError as exc:
        # 纠正③：无论有无 request_id，submission_state='unknown' 必须持久化
        partial_id = getattr(exc, "provider_request_id", None)
        with connection(db_path) as db:
            repo = jr.SqliteJobRepository(db)
            repo.record_provider_request(
                job_id, mode, str(partial_id) if partial_id else None, "unknown")
            repo.mark_interrupted(job_id, mode, _SAFE_MESSAGES["UnknownSubmissionError"][1])
        return RunOnceResult(claimed=True, job_id=job_id, status="interrupted",
                             detail="submission_unknown")
    except Exception as exc:  # 未知异常 fail-closed
        code, message = _safe_error(exc)
        return _fail(db_path, mode, job_id, message, retryable=False, code=code)

    # provider_request_id 追溯登记（正常返回路径）
    if analysis.provider_request_id:
        with connection(db_path) as db:
            jr.SqliteJobRepository(db).record_provider_request(
                job_id, mode, analysis.provider_request_id, "submitted")

    # ---- 段3：短事务持久化终态 + 结果（复用 t-18 分层，不复制） ----
    with connection(db_path) as db:
        adapter_repo = RepoJobAdapter(db, mode)
        try:
            internal = adapter_repo.find_by_id(job_id)
        except SnapshotInvalid:
            code, message = _SAFE_MESSAGES["ValidationError"]
            adapter_repo.mark_failed(job_id, message, retryable=False)
            return RunOnceResult(claimed=True, job_id=job_id, status="failed",
                                 detail=code)
        if internal is None or internal.snapshot is None:
            adapter_repo.mark_failed(job_id, "快照缺失或不可用，不造 unknown 占位",
                                     retryable=False)
            return RunOnceResult(claimed=True, job_id=job_id, status="failed",
                                 detail="invalid_snapshot")
        service = VideoJobService(repository=adapter_repo)
        ok = service.complete_job(job_id, analysis)
        if not ok:
            return RunOnceResult(claimed=True, job_id=job_id, status="conflict",
                                 detail="terminal_state_conflict")
        row = jr.SqliteJobRepository(db).find_by_id(job_id, mode)
        return RunOnceResult(claimed=True, job_id=job_id,
                             status=row.status if row else "unknown", detail="ok")


def _validate_snapshot(db_path: Path, mode: str, claimed: jr.ClaimedJob) -> tuple[str, str] | None:
    """快照校验（adapter 前）。返回 None=通过；(code, 固定文案)=失败。

    全链实查（A 2026-10-20 01:03 纠正②）：
    - ReviewSnapshot 构造合法（失败文案不含 Pydantic 输入回显）
    - snapshot.mode == 作业行 mode
    - 作业行 rule_version_id 非空（空=拒，不条件跳过）
    - snapshot.ruleVersionId == 作业行 rule_version_id
    - task JOIN project：任务存在、同项目、项目 mode==作业 mode
    - rule JOIN brief：规则存在、属同项目、state ∈ {enabled, superseded}
      （既有任务资格允许 superseded——旧绑定不误杀；新绑定限制 enabled
      归基础 bindable_version，不在此重复）
    - snapshot.scriptVersionId == 作业行 script_version_id（若行有值）
    script 实际资格校验未实现（表未建）——demo fixture 边界，不称真实准入。
    """
    try:
        snap = ReviewSnapshot.model_validate(claimed.snapshot)
    except Exception:
        return _SAFE_MESSAGES["ValidationError"]
    if snap.mode != mode:
        return ("snapshot_mode_mismatch", "快照 mode 与作业行不一致")
    if not claimed.rule_version_id:
        return ("job_rule_binding_missing", "作业行未绑定规则版本")
    if snap.rule_version_id != claimed.rule_version_id:
        return ("snapshot_rule_mismatch", "快照规则与作业行绑定不一致")
    if snap.script_version_id != claimed.script_version_id:
        # 可空值直接相等：None/None=当前 demo fixture 合法边界；
        # None/非空 与 非空/None 均失配拒（不条件跳过任何一侧）。
        # 实际脚本 DB 准入未实现（表在建，B 另批接线）。
        return ("snapshot_script_mismatch", "快照脚本版本与作业行不一致")
    with connection(db_path) as db:
        task = db.execute(
            "SELECT t.project_id, t.rule_version_id AS task_rule, p.mode FROM creator_tasks t "
            "JOIN projects p ON p.id=t.project_id WHERE t.id=?",
            (claimed.task_id,)).fetchone() if claimed.task_id else None
        if task is None:
            return ("task_reference_invalid", "任务引用无效或不存在")
        if task["task_rule"] != claimed.rule_version_id:
            # 任务已改绑（当前绑定 != 作业行绑定）：旧 job 依据过期，需重审
            return ("task_rebind_stale_job", "任务已改绑规则，旧作业依据过期")
        rule = db.execute(
            "SELECT b.project_id, rv.state FROM rule_versions rv "
            "JOIN briefs b ON b.id=rv.brief_id WHERE rv.id=?",
            (snap.rule_version_id,)).fetchone()
    if rule is None:
        return ("rule_reference_invalid", "规则版本不存在")
    if rule["project_id"] != task["project_id"]:
        return ("rule_project_mismatch", "规则版本与任务不属于同一项目")
    if task["mode"] != mode:
        return ("task_mode_mismatch", "任务所属项目 mode 与作业不一致")
    if rule["state"] not in ("enabled", "superseded"):
        return ("rule_state_invalid", "规则版本状态不可用于既有任务资格")
    return None


def _precheck(db_path: Path, mode: str, claimed: jr.ClaimedJob) -> str | None:
    """video 入参先验（file 存在/purpose/mode；缺引用 failed 不网络）。"""
    if claimed.kind == "video":
        if not claimed.video_file_id:
            return "video 作业缺 video_file_id，不伪填"
        with connection(db_path) as db:
            row = db.execute("SELECT purpose, mode FROM file_assets WHERE id=?",
                             (claimed.video_file_id,)).fetchone()
        if row is None:
            return f"video 文件不存在（id 失效）"
        if row["purpose"] != "video":
            return "引用文件 purpose 非 video，拒绝"
        if row["mode"] != mode:
            return "引用文件 mode 与作业不一致"
    return None


def _fail(db_path: Path, mode: str, job_id: str, message: str, *,
          retryable: bool, code: str) -> RunOnceResult:
    with connection(db_path) as db:
        jr.SqliteJobRepository(db).mark_failed(job_id, mode, message, retryable=retryable)
    return RunOnceResult(claimed=True, job_id=job_id, status="failed", detail=code)


# ---------- 错误落库策略：固定安全文案（白名单异常类型），不透传原文 ----------
# 落库 error_message 只用稳定 code + 固定中文文案；绝不保存 str(exc)
# （可能含密钥/签名 URL/本地路径/Pydantic 输入回显）。诊断信息留在
# worker 进程日志（本批无日志设施，仅丢弃），DB 只存可公开的安全描述。
_SAFE_MESSAGES = {
    "InvalidAnalysisStructure": ("invalid_model_structure", "模型输出结构非法，作业标记失败"),
    "SafeRetryableError": ("safe_retryable_not_submitted", "请求未发出即失败，可安全重试"),
    "UnknownSubmissionError": ("submission_state_unknown", "供应商提交状态未知，等待人工处理"),
    "ValidationError": ("invalid_snapshot", "快照校验失败"),
    "unexpected": ("unexpected_adapter_error", "适配器执行异常，作业标记失败"),
}


def _safe_error(exc: BaseException) -> tuple[str, str]:
    """按异常类型返回稳定 (code, 固定文案)；不使用 str(exc)。"""
    for type_name, message in _SAFE_MESSAGES.items():
        if type(exc).__name__ == type_name:
            return message
    return _SAFE_MESSAGES["unexpected"]
