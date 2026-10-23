"""RepoJobAdapter：把 SqliteJobRepository 适配为 t-18 VideoJobService 可用的协议实现。

设计边界（A 2026-10-20 纠正后）：
- 本适配器【只处理已认领的作业】：认领（queued→processing 的 CAS）只能
  由 SqliteJobRepository.claim_next 完成；update_to_processing 在此显式
  拒绝（NotImplementedError、零副作用）——若允许实现，会认领到另一个
  作业，破坏 run_once 的单次认领约束。
- mark_* / find_by_id 直通 repo 同名方法（repo 已带 status='processing'
  AND mode 条件与终态不降级）。
- complete 的快照/结果映射在这里提供：SqliteJobRepository 行的
  snapshot_json（dict）→ ReviewSnapshot（Pydantic 校验，不合法抛错由
  worker 转 failed，不造 unknown 占位）；VideoAnalysisResult →
  jobs_results 的 result dict（含完整 ReviewResult 语义）。
- create_job 直通（retry 链新建 queued 行）。
"""
from __future__ import annotations

from app.core.contracts import JobStatus
from app.core.review_contracts import ReviewSnapshot
from app.modules.video_model import jobs_repository as jr
from app.modules.video_model.service import JobRecordInternal


class SnapshotInvalid(Exception):
    """行内 snapshot_json 无法构造合法 ReviewSnapshot（worker 应 failed，不造占位）。"""


class RepoJobAdapter:
    """VideoJobService 的持久化仓储适配（单连接短事务由调用方管理）。"""

    def __init__(self, db, mode: str):
        self._repo = jr.SqliteJobRepository(db)
        self._mode = mode

    # ---------- 认领：显式拒绝（防误用认领他人作业） ----------

    def update_to_processing(self, job_id: str) -> bool:
        """显式不支持：本适配器只服务已认领作业。

        CAS 认领唯一入口是 SqliteJobRepository.claim_next(mode)——
        若在此实现（例如调 claim_next 再比对 id），会把另一个 queued
        作业误标 processing。故显式拒绝且零副作用。
        """
        raise NotImplementedError(
            "认领只能经 SqliteJobRepository.claim_next；本适配器只处理已认领作业")

    # ---------- 查询（mode 固定为适配器构造时的值） ----------

    def find_by_id(self, job_id: str) -> JobRecordInternal | None:
        row = self._repo.find_by_id(job_id, self._mode)
        return _to_internal(row) if row else None

    # ---------- 终态直通（repo 层已有条件更新与终态不降级） ----------

    def mark_succeeded(self, job_id: str, result: ReviewResult) -> bool:
        return self._repo.mark_succeeded(job_id, self._mode, result.model_dump(by_alias=True, mode="json"))

    def mark_failed(self, job_id: str, error_message: str, retryable: bool) -> bool:
        return self._repo.mark_failed(job_id, self._mode, error_message, retryable=retryable)

    def mark_interrupted(self, job_id: str, reason: str) -> bool:
        return self._repo.mark_interrupted(job_id, self._mode, reason)

    def record_provider_request(self, job_id: str, provider_request_id: str,
                                submission_state: str) -> bool:
        return self._repo.record_provider_request(job_id, self._mode,
                                                  provider_request_id, submission_state)

    def create_job(self, job: JobRecordInternal) -> str:
        """显式不支持（A 纠正④）：本批不实现经协议创建重试作业。

        JobRecordInternal 无 task_id/video_file_id 等完整入队字段，
        直通会丢输入或崩溃；重试创建属后续批次（届时经 repo 显式
        全参数 create_job）。零副作用。
        """
        raise NotImplementedError(
            "本批不实现协议级 create_job（缺完整入队字段）；重试创建走 "
            "SqliteJobRepository.create_job 全参数路径")

    # ---------- 快照映射（A 纠正④：不合法显式 failed，不造占位） ----------

    def snapshot_of(self, job_id: str) -> ReviewSnapshot:
        """从行内 snapshot_json 构造合法 ReviewSnapshot；不合法抛 SnapshotInvalid。"""
        row = self._repo.find_by_id(job_id, self._mode)
        if row is None:
            raise SnapshotInvalid(f"job {job_id} 不存在于 mode={self._mode}")
        try:
            return ReviewSnapshot.model_validate(row.snapshot)
        except Exception as exc:
            raise SnapshotInvalid(f"job {job_id} snapshot 不合法: {exc}") from exc


def _to_internal(row: jr.ClaimedJob) -> JobRecordInternal:
    """ClaimedJob（含 status/retryable/error）→ t-18 JobRecordInternal。"""
    try:
        snapshot = ReviewSnapshot.model_validate(row.snapshot)
    except Exception:
        snapshot = None  # 不合法快照由 worker 经 snapshot_of 显式 failed
    return JobRecordInternal(
        id=row.id, kind=row.kind, status=JobStatus(row.status), snapshot=snapshot,
        provider_request_id=row.provider_request_id,
        submission_state=row.submission_state, error_message=row.error_message,
        retryable=row.retryable, attempt=row.attempt, retry_of=row.retry_of,
        created_at=row.created_at, updated_at=row.updated_at)
