"""D t-34 WIP 路由 IO 形状（**未激活**；不进入公共契约）。

隔离依据（A 2026-10-20 13:06）：公共 schemas.py 已恢复 HEAD 逐字节一致，
本文件承接 t-34 路由专属形状，仅供 router_impl.py 与专属测试使用。
待评审通过后随完整复核 router 一起原子激活。
"""
from typing import Literal

from pydantic import Field

from app.core.contracts import ContractModel, ReviewScope
from app.core.review_contracts import AnalysisIntegrity, ReviewSnapshot
from app.modules.human_review.schemas import (
    DecisionChoice, IssueDecisionInput, IssueRef, ManualIssueInput,
)


class ReviewItem(ContractModel):
    """复核列表项（基线筛选：q/platform/state/page，默认 pending/draft）。"""

    id: str
    job_id: str
    task_id: str
    scope: ReviewScope
    state: str
    mode: str
    revision: int
    creator_name: str = ""
    platform: str = ""
    conclusion: str | None = None  # 来自 jobs_results（缺结果显式 None）
    created_at: str
    updated_at: str
    stale: bool = False


class ReviewDetail(ContractModel):
    """复核详情：DB 权威组装（jobs_results+review_jobs+creator_tasks）。"""

    id: str
    job_id: str
    task_id: str
    scope: ReviewScope
    state: str
    mode: str
    revision: int
    snapshot: ReviewSnapshot | None = None
    conclusion: str | None = None
    integrity: AnalysisIntegrity | None = None
    issues: list[IssueRef] = Field(default_factory=list)
    draft: dict | None = None
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class DraftBody(ContractModel):
    """PATCH /human-reviews/:id/draft 请求体（If-Match revision + 幂等键）。"""

    issue_decisions: list[IssueDecisionInput] = Field(default_factory=list)
    manual_issues: list[ManualIssueInput] = Field(default_factory=list)
    final_choice: DecisionChoice | None = None
    comment: str = Field(default="", max_length=2000)


class DecisionBody(ContractModel):
    """POST /human-reviews/:id/decision 请求体（If-Match + 幂等键 + gate 全量）。"""

    decision: DecisionChoice
    comment: str = Field(default="", max_length=2000)
    issue_decisions: list[IssueDecisionInput] = Field(default_factory=list)
    manual_issues: list[ManualIssueInput] = Field(default_factory=list)


class DecisionResult(ContractModel):
    """决定提交结果（approved/returned + simulated 标记 + 任务阶段）。"""

    review_id: str
    decision: DecisionChoice
    simulated: bool
    task_stage: str
    revision: int
