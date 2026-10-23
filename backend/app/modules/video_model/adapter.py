"""C video-model adapter protocol (no real provider implementation).

只定义协议与测试用 fake：无 SDK、无网络。真实实现前不得伪造成功。
异常分层（P0③）：SafeRetryableError=明确未发出/供应商受理前拒绝；
UnknownSubmissionError=提交状态未知（可能已收费）；非法结构独立。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.contracts import Conclusion, Verdict
from app.core.review_contracts import Evidence, Issue, RuleResult

# 测试用故障注入模式（FakeVideoAdapter 专用）：
# "none" | "audio_incomplete" | "video_incomplete" | "rules_incomplete"
# | "mismatch_passed_incomplete_facts" | "safe_not_sent" | "unknown_timeout"
# | "invalid_structure"
FailureMode = str


class InvalidAnalysisStructure(Exception):
    """适配器输出未通过 schema/完整性检查（例如缺规则覆盖、时间越界）。

    对应错误表 422 INVALID_MODEL_RESULT：调用方应标记作业 failed
    （不可重试——同样输入仍会产生非法结构），不把非法结构当作
    succeeded+incomplete。
    """


class SafeRetryableError(Exception):
    """明确可安全重试的失败：请求从未发出，或供应商在受理前明确拒绝。

    例如：连接从未建立、429 限流在受理前拒绝。语义 = 未产生供应商侧
    作业，重试不会重复收费。除此之外的一切异常不得抛此类型。
    """


class UnknownSubmissionError(Exception):
    """提交状态未知：请求可能已送达供应商（可能已收费），也可能没有。

    典型：发出后超时/连接中断。调用方必须标 interrupted 并查询，
    严禁默认可安全重试。
    """


@dataclass
class VideoAnalysisResult:
    """Adapter internal result shape before mapping to ReviewResult.

    audio_complete / video_complete / rules_covered 是三个独立事实，
    不得互相推导；任何一项为 False 时服务端将强制 conclusion=INCOMPLETE
    （complete_job 落实，不信任 adapter 自报的乐观结论）。
    """

    conclusion: Conclusion
    results: list[RuleResult]
    issues: list[Issue]
    provider_request_id: str | None = None
    audio_complete: bool = True
    video_complete: bool = True
    rules_covered: bool = True


class ModelAdapterProtocol(ABC):
    """Protocol defining the video model interface. Only protocol + fake test, no SDK."""

    @abstractmethod
    def analyze_video(
        self,
        video_file_id: str,
        script_version_id: str | None,
        rule_version_id: str,
        model_config_id: str,
    ) -> VideoAnalysisResult:
        """Analyze a complete video with full audio track. Must not substitute frame sampling.

        Returns VideoAnalysisResult with provider_request_id for traceability.
        失败必须按语义抛出：SafeRetryableError（明确未发出/受理前拒绝）、
        UnknownSubmissionError（提交状态未知）、InvalidAnalysisStructure
        （结果非法）。适配器自报的 conclusion 不是最终权威 —— 服务端
        complete_job 会按独立完整性事实强制覆盖。
        """
        ...

    @abstractmethod
    def query_request_status(self, provider_request_id: str) -> str:
        """查询既有供应商请求状态：返回 'completed' | 'pending' | 'unknown'。

        unknown 仅表示"查询后仍未知"，绝不证明请求未执行/已终止。
        调用方据此先查不重发。未发送的请求没有 request_id，由调用方
        的 submission_state 显式跟踪，不进本方法。
        """
        ...

    @property
    @abstractmethod
    def supports_full_video(self) -> bool:
        """Whether this adapter supports complete video input (not frame-sampled)."""
        ...

    @property
    @abstractmethod
    def supports_full_audio(self) -> bool:
        """Whether this adapter supports full audio track analysis."""
        ...


class FakeVideoAdapter(ModelAdapterProtocol):
    """Fake adapter for testing only. No network/SDK calls.

    failure_mode 覆盖评审要求的全部场景，含恶意/失配 adapter：
      - "none": 正常返回（默认 needs_confirmation + 1 issue）
      - "audio_incomplete": 分析完成、音轨确实缺失（audio_complete=False）
      - "video_incomplete": 视频分析不完整（video_complete=False）
      - "rules_incomplete": 规则覆盖不全（rules_covered=False）
      - "mismatch_passed_incomplete_facts": 恶意/失配 —— 自报 PASSED 但
        audio_complete=False（测试服务端强制覆盖，不信任乐观结论）
      - "safe_not_sent": 抛 SafeRetryableError（明确未发出，可安全重试）
      - "unknown_timeout": 抛 UnknownSubmissionError（提交未知，可能已收费）
      - "invalid_structure": 抛 InvalidAnalysisStructure（结果非法，不可重试）
    """

    def __init__(self, failure_mode: FailureMode = "none"):
        self._failure_mode = failure_mode
        self._request_counter = 0
        # query_request_status 的可编程应答；未命中时默认 "unknown"
        self.query_answers: dict[str, str] = {}

    @property
    def supports_full_video(self) -> bool:
        return True

    @property
    def supports_full_audio(self) -> bool:
        return True

    def query_request_status(self, provider_request_id: str) -> str:
        return self.query_answers.get(provider_request_id, "unknown")

    def analyze_video(
        self,
        video_file_id: str,
        script_version_id: str | None,
        rule_version_id: str,
        model_config_id: str,
    ) -> VideoAnalysisResult:
        self._request_counter += 1
        request_id = f"fake-{self._request_counter}"

        if self._failure_mode == "safe_not_sent":
            raise SafeRetryableError("fake: connection refused before submit")
        if self._failure_mode == "unknown_timeout":
            raise UnknownSubmissionError("fake: timed out after submit, state unknown")
        if self._failure_mode == "invalid_structure":
            raise InvalidAnalysisStructure("fake invalid structure: rule coverage missing")

        if self._failure_mode == "audio_incomplete":
            return VideoAnalysisResult(
                conclusion=Conclusion.INCOMPLETE,
                results=[], issues=[],
                provider_request_id=request_id,
                audio_complete=False,
            )
        if self._failure_mode == "video_incomplete":
            return VideoAnalysisResult(
                conclusion=Conclusion.INCOMPLETE,
                results=[], issues=[],
                provider_request_id=request_id,
                video_complete=False,
            )
        if self._failure_mode == "rules_incomplete":
            return VideoAnalysisResult(
                conclusion=Conclusion.INCOMPLETE,
                results=[], issues=[],
                provider_request_id=request_id,
                rules_covered=False,
            )
        if self._failure_mode == "mismatch_passed_incomplete_facts":
            # 恶意/失配 adapter：自报 PASSED 但音轨事实不完整。
            # 服务端 complete_job 必须强制 INCOMPLETE —— 本 fake 用于证明。
            return VideoAnalysisResult(
                conclusion=Conclusion.PASSED,
                results=[], issues=[],
                provider_request_id=request_id,
                audio_complete=False,
            )

        # 默认：needs_confirmation + 一条必须项问题
        return VideoAnalysisResult(
            conclusion=Conclusion.NEEDS_CONFIRMATION,
            results=[
                RuleResult(
                    requirement_id="req_1",
                    verdict=Verdict.COMPLIANT,
                    rationale="Mock compliant",
                    evidence=[],
                ),
                RuleResult(
                    requirement_id="req_2",
                    verdict=Verdict.NEEDS_CONFIRMATION,
                    rationale="Mock needs confirmation",
                    evidence=[
                        Evidence(
                            scope="time-range",
                            description="Speaker not wearing brand colors in visible segment",
                            start_ms=30000,
                            end_ms=45000,
                        )
                    ],
                ),
            ],
            issues=[
                Issue(
                    id="issue_1",
                    requirement_id="req_2",
                    category="visual",
                    importance="must",
                    description="Brand color requirement needs human review",
                    suggestion="Confirm whether alternative branding is acceptable",
                    evidence=[],
                    origin="ai",
                )
            ],
            provider_request_id=request_id,
        )
