"""C video-model job tests (D t-18 P0 修正版；纯 mock，不声称持久化/真实并发)。

P0 覆盖：
① 服务端权威——恶意/失配 adapter 自报 PASSED+audio_false → 强制 INCOMPLETE；
   video/rules 独立事实各自映射不互相推导
② 重试 fail-closed——unknown≠已死；无 request_id≠未发送；类型化安全门，
   裸确认/空证据/未知状态一律拒绝
③ 异常分层——safe_not_sent→FAILED可重试；unknown_timeout→INTERRUPTED；
   invalid_structure→FAILED不可重试；未知异常→默认不可重试
"""
from __future__ import annotations

import pytest

from app.core.contracts import Conclusion, JobStatus, RunMode
from app.core.review_contracts import ReviewSnapshot
from app.modules.video_model.adapter import (
    FakeVideoAdapter,
    InvalidAnalysisStructure,
)
from app.modules.video_model.service import (
    JobRecordInternal,
    JobRepositoryProtocol,
    RetrySafetyConfirmation,
    VideoJobService,
)


class MockJobRepository(JobRepositoryProtocol):
    """In-memory mock：CAS 用「检查+写入于同一临界区」模拟单条原子 UPDATE。

    与真实 DB 的差异（docstring 固化）：无跨进程并发、锁集合不模拟
    数据库行锁生命周期；本 mock 只验证 service 协议语义。
    """

    def __init__(self):
        self._jobs: dict[str, JobRecordInternal] = {}
        self._claimed: set[str] = set()

    def find_by_id(self, job_id: str) -> JobRecordInternal | None:
        return self._jobs.get(job_id)

    def update_to_processing(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.QUEUED or job_id in self._claimed:
            return False
        self._claimed.add(job_id)
        job.status = JobStatus.PROCESSING
        return True

    def mark_succeeded(self, job_id: str, result) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.PROCESSING:
            return False
        job.status = JobStatus.SUCCEEDED
        job._last_result = result  # 测试观察用
        return True

    def mark_failed(self, job_id: str, error_message: str, retryable: bool) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.PROCESSING:
            return False
        job.status = JobStatus.FAILED
        job.error_message = error_message
        job.retryable = retryable
        return True

    def mark_interrupted(self, job_id: str, reason: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.PROCESSING:
            return False
        job.status = JobStatus.INTERRUPTED
        job.error_message = reason
        return True

    def create_job(self, job: JobRecordInternal) -> str:
        self._jobs[job.id] = job
        return job.id


def make_service():
    repo = MockJobRepository()
    return VideoJobService(repo), repo


def make_job(job_id: str = "job-1", **overrides) -> JobRecordInternal:
    base = dict(
        id=job_id,
        snapshot=ReviewSnapshot(
            mode=RunMode.DEMO,
            rule_version_id="rv_1",
            model_config_id="mc_1",
            model_config_revision=1,
        ),
        created_at="",
        updated_at="",
    )
    base.update(overrides)
    return JobRecordInternal(**base)


def run_adapter(service, repo, job_id, mode):
    """认领后用指定故障模式的 fake 执行 run_job 的便捷封装。"""
    repo.create_job(make_job(job_id))
    adapter = FakeVideoAdapter(failure_mode=mode)
    final = service.run_job(job_id, adapter, video_file_id="v",
                            script_version_id="s", rule_version_id="r",
                            model_config_id="c")
    return repo.find_by_id(job_id), final


class TestCasClaim:
    def test_first_claim_wins_and_loser_gets_none_without_writeback(self):
        service, repo = make_service()
        repo.create_job(make_job("job-c"))
        assert service.claim_and_process("job-c") is not None
        assert repo.find_by_id("job-c").status == JobStatus.PROCESSING
        assert service.claim_and_process("job-c") is None
        assert repo.find_by_id("job-c").status == JobStatus.PROCESSING

    def test_claim_missing_job_returns_none(self):
        service, _ = make_service()
        assert service.claim_and_process("nope") is None


class TestServerAuthoritativeLayering:
    """P0①：服务端权威覆盖，不信任 adapter 自报结论。"""

    def test_malicious_passed_with_audio_false_forced_to_incomplete(self):
        service, repo = make_service()
        repo.create_job(make_job("job-m"))
        service.claim_and_process("job-m")
        adapter = FakeVideoAdapter(failure_mode="mismatch_passed_incomplete_facts")
        analysis = adapter.analyze_video("v", "s", "r", "c")
        assert analysis.conclusion == Conclusion.PASSED  # fake 确实自报 PASSED
        assert service.complete_job("job-m", analysis) is True
        job = repo.find_by_id("job-m")
        assert job.status == JobStatus.SUCCEEDED  # 分析有结果：succeeded
        result = job._last_result
        assert result.conclusion == Conclusion.INCOMPLETE  # 服务端强制覆盖
        assert result.integrity.audio == "incomplete"  # 事实如实

    def test_audio_genuinely_missing_is_succeeded_with_incomplete(self):
        service, repo = make_service()
        repo.create_job(make_job("job-a"))
        service.claim_and_process("job-a")
        adapter = FakeVideoAdapter(failure_mode="audio_incomplete")
        assert service.complete_job("job-a", adapter.analyze_video("v", "s", "r", "c"))
        job = repo.find_by_id("job-a")
        assert job.status == JobStatus.SUCCEEDED
        result = job._last_result
        assert result.conclusion == Conclusion.INCOMPLETE
        assert result.integrity.audio == "incomplete"
        assert result.integrity.rules_complete is True

    def test_video_incomplete_independent_fact(self):
        service, repo = make_service()
        repo.create_job(make_job("job-v"))
        service.claim_and_process("job-v")
        adapter = FakeVideoAdapter(failure_mode="video_incomplete")
        assert service.complete_job("job-v", adapter.analyze_video("v", "s", "r", "c"))
        result = repo.find_by_id("job-v")._last_result
        # video 事实独立映射，不从 rules_covered 推导
        assert result.integrity.video == "incomplete"
        assert result.integrity.rules_complete is True
        assert result.integrity.audio == "complete"  # 音轨完整就如实说完整
        assert result.conclusion == Conclusion.INCOMPLETE

    def test_rules_incomplete_forces_incomplete(self):
        service, repo = make_service()
        repo.create_job(make_job("job-r0"))
        service.claim_and_process("job-r0")
        adapter = FakeVideoAdapter(failure_mode="rules_incomplete")
        assert service.complete_job("job-r0", adapter.analyze_video("v", "s", "r", "c"))
        result = repo.find_by_id("job-r0")._last_result
        assert result.integrity.rules_complete is False
        assert result.conclusion == Conclusion.INCOMPLETE

    def test_normal_result_succeeded_with_original_conclusion(self):
        service, repo = make_service()
        repo.create_job(make_job("job-n"))
        service.claim_and_process("job-n")
        adapter = FakeVideoAdapter()
        assert service.complete_job("job-n", adapter.analyze_video("v", "s", "r", "c"))
        job = repo.find_by_id("job-n")
        assert job.status == JobStatus.SUCCEEDED
        result = job._last_result
        assert result.conclusion == Conclusion.NEEDS_CONFIRMATION
        assert result.integrity.audio == "complete"
        assert len(result.issues) == 1

    def test_missing_snapshot_fails_without_forging(self):
        service, repo = make_service()
        repo.create_job(make_job("job-s", snapshot=None))
        service.claim_and_process("job-s")
        adapter = FakeVideoAdapter()
        service.complete_job("job-s", adapter.analyze_video("v", "s", "r", "c"))
        job = repo.find_by_id("job-s")
        assert job.status == JobStatus.FAILED
        assert not hasattr(job, "_last_result")

    def test_complete_job_rejects_non_processing(self):
        service, repo = make_service()
        repo.create_job(make_job("job-q"))
        adapter = FakeVideoAdapter()
        assert service.complete_job("job-q", adapter.analyze_video("v", "s", "r", "c")) is False
        assert repo.find_by_id("job-q").status == JobStatus.QUEUED


class TestExceptionLayering:
    """P0③：异常分层，禁止默认安全。"""

    def test_safe_not_sent_failed_retryable(self):
        service, repo = make_service()
        job, final = run_adapter(service, repo, "job-sns", "safe_not_sent")
        assert final == JobStatus.FAILED
        assert job.retryable is True  # 明确未发出：唯一可安全重试类
        assert "never submitted" in job.error_message

    def test_unknown_timeout_interrupted_not_failed(self):
        service, repo = make_service()
        job, final = run_adapter(service, repo, "job-ut", "unknown_timeout")
        assert final == JobStatus.INTERRUPTED  # 可能已收费：查询后再定
        assert job.retryable is False
        # 提交未知不可直接重试（即使带安全门也需真实证据，见 TestRetryGate）
        assert service.retry_with_bounds("job-ut") is None

    def test_invalid_structure_failed_not_retryable(self):
        service, repo = make_service()
        job, final = run_adapter(service, repo, "job-is", "invalid_structure")
        assert final == JobStatus.FAILED
        assert job.retryable is False  # 同输入仍会非法：重试无意义

    def test_unexpected_exception_defaults_not_retryable(self):
        service, repo = make_service()
        repo.create_job(make_job("job-ue"))

        class ChaosAdapter(FakeVideoAdapter):
            def analyze_video(self, *a, **k):
                raise RuntimeError("chaos")  # 未分类异常

        final = service.run_job("job-ue", ChaosAdapter(), video_file_id="v",
                                script_version_id="s", rule_version_id="r",
                                model_config_id="c")
        assert final == JobStatus.FAILED
        job = repo.find_by_id("job-ue")
        assert job.retryable is False  # fail-closed：未知异常禁止默认安全

    def test_run_job_on_unclaimed_job_makes_no_writes(self):
        service, repo = make_service()
        repo.create_job(make_job("job-u"))
        service.claim_and_process("job-u")  # 他人先认领
        adapter = FakeVideoAdapter()
        final = service.run_job("job-u", adapter, video_file_id="v",
                                script_version_id="s", rule_version_id="r",
                                model_config_id="c")
        assert final == JobStatus.PROCESSING
        job = repo.find_by_id("job-u")
        assert job.status == JobStatus.PROCESSING
        assert not hasattr(job, "_last_result")
        assert adapter._request_counter == 0  # 未触发分析（防重复计费）


class TestProviderUnknown:
    def test_unknown_status_marks_interrupted_no_retry(self):
        service, repo = make_service()
        job = make_job("job-unk", provider_request_id="prov-1")
        repo.create_job(job)
        service.claim_and_process("job-unk")
        adapter = FakeVideoAdapter()  # query 默认 unknown
        final = service.handle_provider_unknown_timeout(job, adapter)
        assert final == JobStatus.INTERRUPTED
        assert service.retry_with_bounds("job-unk") is None  # 无证据拒重试

    def test_pending_status_marks_interrupted_awaits_query(self):
        service, repo = make_service()
        job = make_job("job-pend", provider_request_id="prov-2")
        repo.create_job(job)
        service.claim_and_process("job-pend")
        adapter = FakeVideoAdapter()
        adapter.query_answers["prov-2"] = "pending"
        final = service.handle_provider_unknown_timeout(job, adapter)
        assert final == JobStatus.INTERRUPTED
        assert "pending" in (repo.find_by_id("job-pend").error_message or "")

    def test_completed_without_fetch_marks_interrupted_not_forged_success(self):
        service, repo = make_service()
        job = make_job("job-done", provider_request_id="prov-3")
        repo.create_job(job)
        service.claim_and_process("job-done")
        adapter = FakeVideoAdapter()
        adapter.query_answers["prov-3"] = "completed"
        final = service.handle_provider_unknown_timeout(job, adapter)
        assert final == JobStatus.INTERRUPTED
        assert repo.find_by_id("job-done").status != JobStatus.SUCCEEDED
        assert "fetch not implemented" in (repo.find_by_id("job-done").error_message or "")

    def test_invalid_query_status_treated_as_unknown(self):
        service, repo = make_service()
        job = make_job("job-bad", provider_request_id="prov-4")
        repo.create_job(job)
        service.claim_and_process("job-bad")
        adapter = FakeVideoAdapter()
        adapter.query_answers["prov-4"] = "garbage-status"
        assert service.handle_provider_unknown_timeout(job, adapter) == JobStatus.INTERRUPTED

    def test_missing_request_id_raises_for_caller_branch(self):
        service, repo = make_service()
        job = make_job("job-noid", provider_request_id=None)
        repo.create_job(job)
        service.claim_and_process("job-noid")
        with pytest.raises(ValueError):
            service.handle_provider_unknown_timeout(job, FakeVideoAdapter())


class TestRetrySafetyGate:
    """P0②：类型化安全门，unknown≠已死，fail-closed。"""

    def test_interrupted_without_gate_rejected(self):
        service, repo = make_service()
        job, _ = run_adapter(service, repo, "job-ig", "unknown_timeout")
        assert job.status == JobStatus.INTERRUPTED
        assert service.retry_with_bounds("job-ig") is None

    def test_interrupted_query_unknown_alone_is_not_confirmation(self):
        """unknown 仅表示查询后仍未知，绝不证明请求已死/未执行。"""
        service, repo = make_service()
        job = make_job("job-un", provider_request_id="prov-9")
        repo.create_job(job)
        service.claim_and_process("job-un")
        repo.mark_interrupted("job-un", "unknown")
        adapter = FakeVideoAdapter()
        adapter.query_answers["prov-9"] = "unknown"
        # 调用方仅凭 query=unknown 构造的“确认”不是证据
        fake_gate = RetrySafetyConfirmation(evidence="query said unknown")
        assert fake_gate.provider_terminal is False
        assert fake_gate.never_submitted is False
        assert fake_gate.idempotent_replay is False
        assert fake_gate.is_confirmed() is False  # 无正向证据
        assert service.retry_with_bounds("job-un", safety=fake_gate) is None

    def test_interrupted_no_request_id_alone_is_not_confirmation(self):
        """无 request_id≠未发送；只有显式 never_submitted 证据才放行。"""
        service, repo = make_service()
        job = make_job("job-nr", provider_request_id=None)
        repo.create_job(job)
        service.claim_and_process("job-nr")
        repo.mark_interrupted("job-nr", "unknown")
        # 仅有 id 缺失事实、无未发送证据：拒绝
        assert service.retry_with_bounds("job-nr", safety=RetrySafetyConfirmation(
            evidence="provider_request_id is None")) is None
        # 显式未发送证据（如连接从未建立的错误记录）：放行
        ok_gate = RetrySafetyConfirmation(
            never_submitted=True,
            evidence="connection refused before submit (provider log)")
        new_job = service.retry_with_bounds("job-nr", safety=ok_gate)
        assert new_job is not None
        assert new_job.retry_of == "job-nr"

    def test_interrupted_with_provider_terminal_evidence_can_retry(self):
        service, repo = make_service()
        job = make_job("job-term", provider_request_id="prov-10")
        repo.create_job(job)
        service.claim_and_process("job-term")
        repo.mark_interrupted("job-term", "unknown")
        gate = RetrySafetyConfirmation(
            provider_terminal=True,
            evidence="provider API confirmed request prov-10 expired, will not process")
        new_job = service.retry_with_bounds("job-term", safety=gate)
        assert new_job is not None
        assert repo.find_by_id("job-term").status == JobStatus.INTERRUPTED  # 原记录不变

    def test_gate_with_positive_flag_but_empty_evidence_rejected(self):
        service, repo = make_service()
        job = make_job("job-ge", provider_request_id="prov-11")
        repo.create_job(job)
        service.claim_and_process("job-ge")
        repo.mark_interrupted("job-ge", "unknown")
        gate = RetrySafetyConfirmation(provider_terminal=True, evidence="   ")
        assert gate.is_confirmed() is False  # 空证据不确认
        assert service.retry_with_bounds("job-ge", safety=gate) is None

    def test_replay_only_gate_is_not_confirmation(self):
        """P0 收窄：仅 idempotent_replay=True（无终止/未发送证据）不得放行。"""
        service, repo = make_service()
        job = make_job("job-rp", provider_request_id="prov-12")
        repo.create_job(job)
        service.claim_and_process("job-rp")
        repo.mark_interrupted("job-rp", "unknown")
        gate = RetrySafetyConfirmation(
            idempotent_replay=True,
            evidence="claim provider supports idempotent replay")  # 无绑定原请求的实际凭证
        assert gate.is_confirmed() is False  # replay 不计入确认
        assert service.retry_with_bounds("job-rp", safety=gate) is None

    def test_terminal_and_never_submitted_still_confirm_after_narrowing(self):
        """收窄后回归：明确终止 / 明确未发出仍可放行。"""
        service, repo = make_service()
        # 供应商确认终止
        job1 = make_job("job-t1", provider_request_id="prov-13")
        repo.create_job(job1)
        service.claim_and_process("job-t1")
        repo.mark_interrupted("job-t1", "unknown")
        assert service.retry_with_bounds("job-t1", safety=RetrySafetyConfirmation(
            provider_terminal=True,
            evidence="provider confirmed request expired, will not process")) is not None
        # 明确未发出
        job2 = make_job("job-t2", provider_request_id=None)
        repo.create_job(job2)
        service.claim_and_process("job-t2")
        repo.mark_interrupted("job-t2", "unknown")
        assert service.retry_with_bounds("job-t2", safety=RetrySafetyConfirmation(
            never_submitted=True,
            evidence="connection refused before submit")) is not None


class TestTerminalStates:
    def test_succeeded_not_downgraded_to_interrupted(self):
        service, repo = make_service()
        repo.create_job(make_job("job-win"))
        service.claim_and_process("job-win")
        service.complete_job("job-win", FakeVideoAdapter().analyze_video("v", "s", "r", "c"))
        assert repo.find_by_id("job-win").status == JobStatus.SUCCEEDED
        assert repo.mark_interrupted("job-win", "crash?") is False
        assert repo.find_by_id("job-win").status == JobStatus.SUCCEEDED

    def test_failed_not_downgraded(self):
        service, repo = make_service()
        repo.create_job(make_job("job-f"))
        service.claim_and_process("job-f")
        repo.mark_failed("job-f", "boom", retryable=True)
        assert repo.mark_succeeded("job-f", object()) is False
        assert repo.find_by_id("job-f").status == JobStatus.FAILED


class TestRetryBounds:
    def test_retryable_failed_creates_linked_new_job(self):
        service, repo = make_service()
        job, _ = run_adapter(service, repo, "job-r", "safe_not_sent")
        assert job.status == JobStatus.FAILED and job.retryable
        new_job = service.retry_with_bounds("job-r", max_retries=3)
        assert new_job is not None
        assert new_job.retry_of == "job-r"
        assert new_job.attempt == 1
        assert new_job.status == JobStatus.QUEUED
        assert repo.find_by_id("job-r").status == JobStatus.FAILED

    def test_non_retryable_failed_rejected(self):
        service, repo = make_service()
        job, _ = run_adapter(service, repo, "job-nr0", "invalid_structure")
        assert job.retryable is False
        assert service.retry_with_bounds("job-nr0") is None

    def test_queued_and_succeeded_rejected(self):
        service, repo = make_service()
        repo.create_job(make_job("job-q2"))
        assert service.retry_with_bounds("job-q2") is None
        repo.create_job(make_job("job-ok"))
        service.claim_and_process("job-ok")
        service.complete_job("job-ok", FakeVideoAdapter().analyze_video("v", "s", "r", "c"))
        assert service.retry_with_bounds("job-ok") is None

    def test_max_retries_boundary(self):
        service, repo = make_service()
        repo.create_job(make_job("job-max", attempt=3))
        service.claim_and_process("job-max")
        repo.mark_failed("job-max", "still failing", retryable=True)
        assert service.retry_with_bounds("job-max", max_retries=3) is None
