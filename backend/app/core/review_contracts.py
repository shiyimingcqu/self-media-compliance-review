"""B/C/D 共同使用的审核数据形状；仅定义契约，不执行 AI 或通过门禁。"""
from typing import Literal

from pydantic import Field, model_validator

from app.core.contracts import Conclusion, ContractModel, JobStatus, ReviewSnapshot, Verdict


class SourceRef(ContractModel):
    file_id: str
    page: int | None = Field(default=None, ge=1)
    paragraph_id: str | None = None
    sheet: str | None = None
    cell: str | None = None
    quote: str = ""
    manual_note: str | None = None


class Evidence(ContractModel):
    scope: Literal["source", "time-range", "whole-video"]
    description: str = Field(min_length=1)
    source_ref: SourceRef | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_location(self):
        if self.scope == "source" and self.source_ref is None:
            raise ValueError("来源证据必须提供 sourceRef")
        if self.scope == "time-range" and (self.start_ms is None or self.end_ms is None or self.end_ms <= self.start_ms):
            raise ValueError("时间证据需要有效的 startMs/endMs")
        return self


class RuleResult(ContractModel):
    requirement_id: str
    verdict: Verdict
    rationale: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)
    not_applicable_reason: str | None = None


class Issue(ContractModel):
    id: str
    requirement_id: str | None = None
    category: Literal["visual", "spoken", "subtitle", "script_consistency", "other"]
    importance: Literal["must", "suggested"]
    description: str = Field(min_length=1)
    suggestion: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    origin: Literal["ai", "human"]


class AnalysisIntegrity(ContractModel):
    rules_complete: bool
    video: Literal["not_applicable", "complete", "incomplete", "failed"]
    audio: Literal["not_applicable", "complete", "incomplete", "failed", "missing"]


class ReviewResult(ContractModel):
    job_id: str
    snapshot: ReviewSnapshot
    conclusion: Conclusion
    integrity: AnalysisIntegrity
    results: list[RuleResult]
    issues: list[Issue]


class JobRecord(ContractModel):
    id: str
    kind: Literal["brief", "script", "video", "capability_test"]
    status: JobStatus
    snapshot: ReviewSnapshot | None = None
    created_at: str
    updated_at: str
    provider_request_id: str | None = None
