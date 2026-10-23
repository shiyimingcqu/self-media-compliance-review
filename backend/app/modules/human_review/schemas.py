"""D 人工复核域输入/输出形状。

仅定义形状与基础校验，不含门禁策略（策略见 decision.py）。
公共字段与枚举引用 core/contracts、core/review_contracts，不复制第二套。
本文件属 D 模块属主；接入端点前保持无 IO 依赖。

⚠ 这些形状不构成客户端自证资格的途径：FinalDecisionPayload 及同类输入
只是服务端门禁评估的载体，调用方必须由服务端 eligibility 服务以 DB 事实
核验（当前有效快照、mode 一致性、scope=script 源结论 needs_confirmation、
完整问题集合）；actorId 由服务端 context（local-reviewer）注入，
不接受客户端提交。
"""
from typing import Literal

from pydantic import Field

from app.core.contracts import ContractModel, ReviewScope
from app.core.review_contracts import AnalysisIntegrity, ReviewSnapshot

DecisionChoice = Literal["approved", "returned"]
IssueAction = Literal["confirmed", "false_positive", "deferred"]
IssueImportance = Literal["must", "suggested"]
IssueOrigin = Literal["ai", "human"]


class IssueRef(ContractModel):
    """门禁评估所需的最小问题引用。

    公共 eligibility 服务可从任意来源（AI 结果、人工补充）构造；
    重要性决定是否参与必须项门禁，来源仅作追溯标记。
    """

    issue_id: str = Field(min_length=1)
    importance: IssueImportance
    origin: IssueOrigin = "ai"


class IssueDecisionInput(ContractModel):
    """对单条问题的人工裁决；追加保留旧裁决，不改写 AI 原文。"""

    issue_id: str = Field(min_length=1)
    action: IssueAction
    reason: str = Field(default="", max_length=1000)


class ManualIssueInput(ContractModel):
    """人工补充问题；时间关系校验（0 ≤ start < end ≤ 时长）在 decision.py。"""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    description: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(default="", max_length=1000)
    importance: IssueImportance = "must"


class FinalDecisionPayload(ContractModel):
    """最终决定门禁评估输入。

    ⚠ 服务端验证边界：本形状不验证 DB 当前状态。端点/eligibility 服务
    必须先核验——snapshot 为该任务当前有效快照（revision 未被替换）、
    mode 与任务/作业一致（demo→real 不可混用）、scope=script 时源作业
    结论确为 needs_confirmation、issues 为该快照下完整问题集合；
    然后才用本形状调用 decision.evaluate_final_decision。
    actorId 不在本形状中：由服务端 context（local-reviewer）注入。

    snapshot 为空表示版本未知；integrity 为空表示分析输入缺失——
    approved 一律拒绝，returned 在快照有效且原因明确时允许（异常待办出口）。
    issues 汇总该快照下全部待裁决问题（含 AI 与人工来源）；
    decisions 为本次提交的裁决集合，同一问题只允许一条。
    """

    scope: ReviewScope
    decision: DecisionChoice | None = None
    comment: str = Field(default="", max_length=2000)
    snapshot: ReviewSnapshot | None = None
    integrity: AnalysisIntegrity | None = None
    issues: list[IssueRef] = Field(default_factory=list)
    decisions: list[IssueDecisionInput] = Field(default_factory=list)


class GateViolation(ContractModel):
    """单条门禁违规；code 稳定，端点层据此映射错误响应。"""

    code: str
    message: str
    issue_id: str | None = None
    field: str | None = None


class FinalDecisionEvaluation(ContractModel):
    """门禁评估结果。

    allowed=False 时 violations 说明全部拒绝原因（默认拒绝）。
    simulated=True 表示该决定只能落为 mode=demo 的模拟记录，
    不产生真实任务资格；demo 不跳过任何结构、快照或完整性门禁。
    """

    allowed: bool
    violations: list[GateViolation] = Field(default_factory=list)
    simulated: bool = False
    decided: DecisionChoice | None = None
