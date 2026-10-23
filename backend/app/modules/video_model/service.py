"""C video-model job service (CAS claim, state layering, unknown no-reblow).

纯逻辑 + mock 范围：不声称持久化/CAS 真实并发保证 —— 原子性由真实
repository 的单条 UPDATE 保证，本模块只定义协议语义并正确消费结果。

安全基线（P0 修正后）：
- 服务端不信任 adapter 自报结论：任一完整性事实为 False → 强制 INCOMPLETE
- 重试 fail-closed：默认不可重试；interrupted/未知必须过类型化安全门，
  unknown 绝不等于"请求已死"
- 异常分层：SafeRetryableError / UnknownSubmissionError / InvalidAnalysisStructure
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.core.contracts import Conclusion, JobStatus
from app.core.review_contracts import AnalysisIntegrity, ReviewResult, ReviewSnapshot

from .adapter import (
    InvalidAnalysisStructure,
    SafeRetryableError,
    UnknownSubmissionError,
    VideoAnalysisResult,
)

# 供应商请求状态：query_request_status 的闭集返回值。
PROVIDER_COMPLETED = "completed"
PROVIDER_PENDING = "pending"
PROVIDER_UNKNOWN = "unknown"
_VALID_PROVIDER_STATUSES = {PROVIDER_COMPLETED, PROVIDER_PENDING, PROVIDER_UNKNOWN}

# 作业的提交跟踪状态（显式，不靠 request_id 是否为 None 推断）：
# not_sent=确认从未发出；submitted=已发出（可能已有 request_id）；
# unknown=发出后状态未知（可能已收费）。
SUBMISSION_NOT_SENT = "not_sent"
SUBMISSION_SUBMITTED = "submitted"
SUBMISSION_UNKNOWN = "unknown"


@dataclass
class JobRecordInternal:
    """Internal job record shape before DB persistence."""

    id: str
    kind: str = "video"  # brief/script/video/capability_test；纯逻辑域不强制枚举
    status: JobStatus = JobStatus.QUEUED
    snapshot: ReviewSnapshot | None = None
    provider_request_id: str | None = None
    submission_state: str = SUBMISSION_NOT_SENT  # 显式提交跟踪，None≠未发送
    error_message: str | None = None
    retryable: bool = False  # 显式字段；默认 False（fail-closed）
    attempt: int = 0
    retry_of: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RetrySafetyConfirmation:
    """重试安全门的类型化确认（不许裸 bool 自证）。

    调用方必须基于真实证据构造，且凭证仅由服务端内部可信代码构造、
    绑定当前 job（不接受客户端自由提交）：
    - provider_terminal: 供应商明确确认原请求已终止且不会处理
    - never_submitted: 有明确证据请求从未发出（如连接从未建立）
    - idempotent_replay: 保留字段，当前增量不计入 is_confirmed ——
      未绑定原供应商幂等 key/原 request 且无实际复用动作，仅凭布尔
      证据不能放行；待真实幂等链路落地后再启用（文档注明）
    - evidence: 证据说明（非空），如供应商响应摘录/错误类型
    is_confirmed = (provider_terminal 或 never_submitted) 且 evidence 非空。
    """

    provider_terminal: bool = False
    never_submitted: bool = False
    idempotent_replay: bool = False  # 当前不计入确认；未来启用须绑定原请求

    evidence: str = ""

    def is_confirmed(self) -> bool:
        has_positive = self.provider_terminal or self.never_submitted
        return has_positive and bool(self.evidence.strip())


class JobRepositoryProtocol:
    """Repository protocol for job CRUD without real DB schema dependency.

    实现约束（docstring 固化，真实 repo 必须遵守）：
    - update_to_processing 必须是单条原子条件更新（CAS）：
      UPDATE ... SET status='processing' WHERE id=? AND status='queued'，
      不允许 check-then-set 两步实现。
    - 终态不降级：mark_succeeded/mark_failed/mark_interrupted 仅当
      status='processing' 时生效；SUCCEEDED/FAILED/INTERRUPTED 一旦落定，
      任何后续 mark_* 都不得改写它（返回 False）。
    """

    def find_by_id(self, job_id: str) -> JobRecordInternal | None:
        raise NotImplementedError

    def update_to_processing(self, job_id: str) -> bool:
        """原子 CAS：queued→processing。返回 False 表示他人已认领/不存在。"""
        raise NotImplementedError

    def mark_succeeded(self, job_id: str, result: ReviewResult) -> bool:
        """processing→succeeded 并保存 result。终态后拒绝。"""
        raise NotImplementedError

    def mark_failed(self, job_id: str, error_message: str, retryable: bool) -> bool:
        """processing→failed（执行异常/非法结构）。终态后拒绝。"""
        raise NotImplementedError

    def mark_interrupted(self, job_id: str, reason: str) -> bool:
        """processing→interrupted（状态不确定）。终态后拒绝。"""
        raise NotImplementedError

    def create_job(self, job: JobRecordInternal) -> str:
        raise NotImplementedError


class VideoJobService:
    """Service implementing job lifecycle with safety rules.

    Rules:
    1. CAS claim: on fail, do NOT write back to queued (overwrite winner's processing)
    2. 状态分层（服务端权威，不信任 adapter 自报）：
       - 任一完整性事实（audio/video/rules）为 False → conclusion 强制 INCOMPLETE，
         job=SUCCEEDED（分析已产生结果，结论如实不完整可见，AT-15）
       - adapter 自报 PASSED 但事实不完整 → 仍强制 INCOMPLETE（防恶意/失配）
       - 快照缺失 → FAILED（不伪造占位）
       - InvalidAnalysisStructure → FAILED 不可重试
       - SafeRetryableError（明确未发出/受理前拒绝）→ FAILED 可重试
       - UnknownSubmissionError（提交未知，可能已收费）→ INTERRUPTED，查询后再定
    3. 终态不降级
    4. 重试 fail-closed：仅 FAILED(retryable) 可直接重试；INTERRUPTED 必须
       过类型化安全门（RetrySafetyConfirmation），unknown≠已死、无
       request_id≠未发送，证据不足一律拒绝。
    """

    def __init__(self, repository: JobRepositoryProtocol):
        self.repository = repository

    # ---------- 1. CAS 认领 ----------

    def claim_and_process(self, job_id: str) -> JobRecordInternal | None:
        """Atomic CAS claim. Returns claimed job or None (loser, no write-back)."""
        claimed = self.repository.update_to_processing(job_id)
        if not claimed:
            return None
        return self.repository.find_by_id(job_id)

    # ---------- 2. 状态分层：服务端权威覆盖 ----------

    def _integrity_from(self, result: VideoAnalysisResult) -> AnalysisIntegrity:
        """三个独立事实各自映射，不互相推导。"""
        return AnalysisIntegrity(
            rules_complete=result.rules_covered,
            video="complete" if result.video_complete else "incomplete",
            audio="complete" if result.audio_complete else "incomplete",
        )

    def complete_job(self, job_id: str, result: VideoAnalysisResult) -> bool:
        """把适配器结果落成 job 终态 + ReviewResult。

        服务端权威：任一完整性事实为 False → conclusion 强制 INCOMPLETE
        （无论 adapter 自报什么，包括 PASSED —— 防恶意/失配 adapter）。
        快照缺失 → FAILED，不伪造 unknown 占位快照。
        """
        job = self.repository.find_by_id(job_id)
        if job is None or job.status != JobStatus.PROCESSING:
            return False
        if job.snapshot is None:
            return self.repository.mark_failed(
                job_id, "snapshot missing: cannot bind result to versions", retryable=False)

        all_complete = (result.audio_complete and result.video_complete
                        and result.rules_covered)
        conclusion = result.conclusion if all_complete else Conclusion.INCOMPLETE
        review = ReviewResult(
            job_id=job_id,
            snapshot=job.snapshot,
            conclusion=conclusion,
            integrity=self._integrity_from(result),
            results=result.results,
            issues=result.issues,
        )
        return self.repository.mark_succeeded(job_id, review)

    def run_job(self, job_id: str, adapter, *, video_file_id: str,
                script_version_id: str | None, rule_version_id: str,
                model_config_id: str) -> JobStatus:
        """认领并执行一个作业（测试/演示编排；真实 worker 另行接入）。

        异常分层（P0③）：
        - InvalidAnalysisStructure → FAILED 不可重试（同输入仍会非法）
        - SafeRetryableError → FAILED 可重试（明确未发出/受理前拒绝）
        - UnknownSubmissionError → INTERRUPTED（提交未知可能已收费，先查询）
        - 其他未知 Exception → FAILED 不可重试（fail-closed，禁止默认安全）
        """
        job = self.claim_and_process(job_id)
        if job is None:
            existing = self.repository.find_by_id(job_id)
            return existing.status if existing else JobStatus.QUEUED
        try:
            analysis = adapter.analyze_video(
                video_file_id, script_version_id, rule_version_id, model_config_id)
        except InvalidAnalysisStructure as exc:
            ok = self.repository.mark_failed(
                job_id, f"invalid model structure: {exc}", retryable=False)
            return JobStatus.FAILED if ok else job.status
        except SafeRetryableError as exc:
            job.submission_state = SUBMISSION_NOT_SENT
            ok = self.repository.mark_failed(
                job_id, f"safe-retryable failure (never submitted): {exc}", retryable=True)
            return JobStatus.FAILED if ok else job.status
        except UnknownSubmissionError as exc:
            job.submission_state = SUBMISSION_UNKNOWN
            ok = self.repository.mark_interrupted(
                job_id, f"submission state unknown: {exc}")
            return JobStatus.INTERRUPTED if ok else job.status
        except Exception as exc:  # 未知异常：禁止默认可重试（fail-closed）
            ok = self.repository.mark_failed(
                job_id, f"unexpected adapter error: {exc}", retryable=False)
            return JobStatus.FAILED if ok else job.status
        if job.provider_request_id is None and analysis.provider_request_id:
            job.provider_request_id = analysis.provider_request_id
            job.submission_state = SUBMISSION_SUBMITTED
        self.complete_job(job_id, analysis)
        final = self.repository.find_by_id(job_id)
        return final.status if final else job.status

    # ---------- 3/4. 供应商未知：先查不重发 ----------

    def handle_provider_unknown_timeout(self, job: JobRecordInternal, adapter) -> JobStatus:
        """request_id 存在但响应不明：先查询原请求，绝不盲重发。

        - completed → 结果取回未实现：interrupted 注明，不伪造成功
        - pending → interrupted 等待轮询
        - unknown → interrupted（仅表示查询后仍未知，不证明已终止）
        query 返回闭集外值按 unknown 处理（防御）。
        """
        if job.provider_request_id is None:
            raise ValueError("provider_request_id must exist for unknown handling")

        status = adapter.query_request_status(job.provider_request_id)
        if status not in _VALID_PROVIDER_STATUSES:
            status = PROVIDER_UNKNOWN
        if status == PROVIDER_COMPLETED:
            ok = self.repository.mark_interrupted(
                job.id, f"request {job.provider_request_id} completed but result fetch not implemented")
            return JobStatus.INTERRUPTED if ok else job.status
        ok = self.repository.mark_interrupted(
            job.id, f"request {job.provider_request_id} status {status}")
        return JobStatus.INTERRUPTED if ok else job.status

    # ---------- 5. 有界重试（fail-closed） ----------

    def retry_with_bounds(self, job_id: str, max_retries: int = 3,
                          *, safety: RetrySafetyConfirmation | None = None,
    ) -> JobRecordInternal | None:
        """有界重试：默认拒绝；仅以下两种情形放行。

        1. FAILED 且 retryable=True（SafeRetryableError 路径：明确未发出）
        2. INTERRUPTED 且安全门给出类型化确认（provider_terminal /
           never_submitted / idempotent_replay 之一 + 非空 evidence）——
           unknown 状态本身绝不是确认；无 request_id 也不等于未发送。
        QUEUED/PROCESSING/SUCCEEDED 一律拒绝；超界不开新作业不改原终态。
        成功时创建新关联作业，原记录保持不变。
        """
        job = self.repository.find_by_id(job_id)
        if job is None:
            return None
        if job.status == JobStatus.FAILED:
            if not job.retryable:
                return None
        elif job.status == JobStatus.INTERRUPTED:
            if safety is None or not safety.is_confirmed():
                return None  # 无证据/证据不足：fail-closed 拒绝
        else:
            return None
        if job.attempt >= max_retries:
            return None

        now = datetime.now(timezone.utc).isoformat()
        new_job = JobRecordInternal(
            id=f"{job.id}-retry-{job.attempt + 1}",
            kind=job.kind,
            status=JobStatus.QUEUED,
            snapshot=job.snapshot,
            retry_of=job.id,
            attempt=job.attempt + 1,
            created_at=now,
            updated_at=now,
        )
        return self.repository.create_job(new_job) and self.repository.find_by_id(new_job.id)
