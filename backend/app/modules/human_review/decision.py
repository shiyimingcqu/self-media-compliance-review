"""人工复核最终决定门禁策略（纯函数，无 IO / 数据库 / 路由依赖）。

供公共 eligibility 服务与 D 域端点调用：默认拒绝——缺输入、版本未知、
必填理由空白、必须项未解决或分析不完整均不放行（协调者裁决 2026-10-19）。

⚠ 服务端验证边界（本模块不负责，必须由 eligibility 服务执行）：
本模块只评估调用方提供的快照/问题集合/裁决输入的结构与语义门禁，
不查询数据库，因此不验证也不得被当作已验证——
- payload.snapshot 是否仍为该任务当前有效快照（revision/版本未被替换）；
- payload.mode 与任务、作业结果的一致性（demo→real 不可混用）；
- scope=script 时源作业结论确为 needs_confirmation；
- payload.issues 是否为该快照下完整问题集合。
调用方（服务端 eligibility/端点层）必须以上述 DB 事实为准构造与核验输入；
actorId 由服务端 context（local-reviewer）注入，不接受客户端提交。

规则依据：openspec/changes/creator-review-workbench/specs/human-review
HR-03/04/06/07/08 及 data-and-api 错误语义，要点：
- script 复核只认有效 needs_confirmation 裁决，不套用视频音轨要求；
  video 复核必须完整音视频分析。
- 建议项（suggested）问题不作为必须项自动阻断通过。
- 退回（returned）在快照有效且原因明确时允许——包括分析完整性输入缺失
  （异常待办可能尚无分析结果，出口是退回而非放行）；approved 仍拒绝。
- demo 模式不跳过结构、快照、完整性门禁；其决定只能落模拟记录，
  不产生真实任务资格。
"""
from app.core.contracts import ReviewScope, RunMode
from app.core.review_contracts import AnalysisIntegrity
from app.modules.human_review.schemas import (
    FinalDecisionEvaluation,
    FinalDecisionPayload,
    GateViolation,
    IssueDecisionInput,
    ManualIssueInput,
)

REASON_MAX_LENGTH = 1000

# 门禁违规码：稳定字符串，端点层据此映射错误响应（不直接抛 HTTP）。
DECISION_REQUIRED = "DECISION_REQUIRED"
COMMENT_REQUIRED = "COMMENT_REQUIRED"
RETURN_REASON_REQUIRED = "RETURN_REASON_REQUIRED"
SNAPSHOT_UNKNOWN = "SNAPSHOT_UNKNOWN"
ANALYSIS_INPUT_MISSING = "ANALYSIS_INPUT_MISSING"
RULES_INCOMPLETE = "RULES_INCOMPLETE"
VIDEO_ANALYSIS_INCOMPLETE = "VIDEO_ANALYSIS_INCOMPLETE"
AUDIO_ANALYSIS_INCOMPLETE = "AUDIO_ANALYSIS_INCOMPLETE"
MUST_ISSUE_UNRESOLVED = "MUST_ISSUE_UNRESOLVED"
MUST_ISSUE_CONFIRMED = "MUST_ISSUE_CONFIRMED"
DEFERRED_BLOCKS_APPROVAL = "DEFERRED_BLOCKS_APPROVAL"
FALSE_POSITIVE_REASON_REQUIRED = "FALSE_POSITIVE_REASON_REQUIRED"
UNKNOWN_ISSUE_REFERENCE = "UNKNOWN_ISSUE_REFERENCE"
DUPLICATE_ISSUE_REFERENCE = "DUPLICATE_ISSUE_REFERENCE"
DUPLICATE_ISSUE_DECISION = "DUPLICATE_ISSUE_DECISION"
TIME_RANGE_INVALID = "TIME_RANGE_INVALID"
DURATION_UNKNOWN = "DURATION_UNKNOWN"
DESCRIPTION_REQUIRED = "DESCRIPTION_REQUIRED"


def validate_issue_decision(decision: IssueDecisionInput) -> list[GateViolation]:
    """单条裁决输入校验：标记误判必须填写依据（HR-03）。"""
    if decision.action == "false_positive" and not decision.reason.strip():
        return [GateViolation(
            code=FALSE_POSITIVE_REASON_REQUIRED,
            message="标记误判必须解释依据，理由不能为空白",
            issue_id=decision.issue_id,
            field="reason",
        )]
    return []


def validate_manual_issue(issue: ManualIssueInput, duration_ms: int | None) -> list[GateViolation]:
    """人工补充问题校验：0 ≤ start < end ≤ 时长，描述非空白（HR-04）。

    duration_ms 为空表示视频元数据未知——缺输入按拒绝处理，
    不得跳过区间校验放行。
    """
    violations: list[GateViolation] = []
    if not issue.description.strip():
        violations.append(GateViolation(
            code=DESCRIPTION_REQUIRED,
            message="问题描述不能为空白",
            field="description",
        ))
    if duration_ms is None:
        violations.append(GateViolation(
            code=DURATION_UNKNOWN,
            message="视频时长未知，无法校验问题区间",
            field="duration",
        ))
    elif not (issue.start_ms < issue.end_ms <= duration_ms):
        violations.append(GateViolation(
            code=TIME_RANGE_INVALID,
            message="问题区间需满足 0 ≤ 开始 < 结束 ≤ 视频时长",
            field="endMs",
        ))
    return violations


def evaluate_final_decision(payload: FinalDecisionPayload) -> FinalDecisionEvaluation:
    """最终决定门禁评估（HR-06/07）。

    返回全部违规而非首个：便于界面一次列出。allowed=False 即拒绝，
    无“默认通过”路径。退回（returned）不检查分析完整性与必须项——
    异常待办的出口是排障或退回，不是放行；但退回同样要求版本已知与原因。
    """
    violations: list[GateViolation] = []

    if payload.decision is None:
        violations.append(GateViolation(
            code=DECISION_REQUIRED,
            message="必须主动选择通过或退回，不自动采用通过",
            field="decision",
        ))

    comment = payload.comment.strip()
    if payload.decision == "approved" and not comment:
        violations.append(GateViolation(
            code=COMMENT_REQUIRED, message="通过必须填写审核意见", field="comment"))
    if payload.decision == "returned" and not comment:
        violations.append(GateViolation(
            code=RETURN_REASON_REQUIRED, message="退回必须说明原因或修改要求", field="comment"))

    if payload.snapshot is None:
        violations.append(GateViolation(
            code=SNAPSHOT_UNKNOWN, message="版本快照未知，不能绑定最终决定", field="snapshot"))
    if payload.integrity is None and payload.decision == "approved":
        # 缺分析完整性输入只阻断通过；退回仍可作为异常待办出口（dispute ②）。
        violations.append(GateViolation(
            code=ANALYSIS_INPUT_MISSING, message="缺少分析完整性输入，不能通过", field="integrity"))

    # 重复问题 ID：同一快照下问题集合必须唯一，避免门禁评估歧义（dispute ①）。
    seen_issues: set[str] = set()
    for issue in payload.issues:
        if issue.issue_id in seen_issues:
            violations.append(GateViolation(
                code=DUPLICATE_ISSUE_REFERENCE,
                message="问题集合中存在重复的问题 ID",
                issue_id=issue.issue_id,
            ))
        seen_issues.add(issue.issue_id)
    known_issues = {issue.issue_id: issue for issue in payload.issues}

    # 同一问题的重复裁决：一律拒绝且与顺序无关——
    # 不允许后一条覆盖前一条（confirmed 被 false_positive 抹掉等场景，dispute ①）。
    seen_decision_issues: set[str] = set()
    for entry in payload.decisions:
        if entry.issue_id in seen_decision_issues:
            violations.append(GateViolation(
                code=DUPLICATE_ISSUE_DECISION,
                message="同一问题存在多条裁决，必须先撤销旧裁决再提交新裁决",
                issue_id=entry.issue_id,
            ))
            continue
        seen_decision_issues.add(entry.issue_id)
        violations.extend(validate_issue_decision(entry))
        if entry.issue_id not in known_issues:
            violations.append(GateViolation(
                code=UNKNOWN_ISSUE_REFERENCE,
                message="裁决引用了不存在的问题",
                issue_id=entry.issue_id,
            ))

    if payload.decision == "approved":
        integrity = payload.integrity
        if integrity is not None:
            if not integrity.rules_complete:
                violations.append(GateViolation(
                    code=RULES_INCOMPLETE, message="规则分析不完整，不能通过"))
            if payload.scope == ReviewScope.VIDEO:
                if integrity.video != "complete":
                    violations.append(GateViolation(
                        code=VIDEO_ANALYSIS_INCOMPLETE, message="完整视频分析未完成，不能通过"))
                if integrity.audio != "complete":
                    violations.append(GateViolation(
                        code=AUDIO_ANALYSIS_INCOMPLETE, message="完整音轨分析未完成，不能通过"))
            # scope=script：只认规则完整性，不套用视频/音轨要求。

        # 重复裁决已在上文必然产生违规（allowed=False），此处字典仅作查询，
        # 其"末项覆盖"行为不可能改变放行结果。
        decisions_by_issue = {entry.issue_id: entry for entry in payload.decisions}
        for issue in payload.issues:
            if issue.importance != "must":
                continue  # 建议项不作为必须项自动阻断（人工仍可选择退回）。
            entry = decisions_by_issue.get(issue.issue_id)
            if entry is None:
                violations.append(GateViolation(
                    code=MUST_ISSUE_UNRESOLVED,
                    message="必须项问题未裁决，不能通过",
                    issue_id=issue.issue_id,
                ))
            elif entry.action == "confirmed":
                violations.append(GateViolation(
                    code=MUST_ISSUE_CONFIRMED,
                    message="确认成立的必须项问题阻止通过",
                    issue_id=issue.issue_id,
                ))
            elif entry.action == "deferred":
                violations.append(GateViolation(
                    code=DEFERRED_BLOCKS_APPROVAL,
                    message="暂不处理的必须项问题仍未解决，不能通过",
                    issue_id=issue.issue_id,
                ))
            # false_positive 且理由非空白 = 已解决；理由空白已在上文记录违规。

    simulated = payload.snapshot is not None and payload.snapshot.mode == RunMode.DEMO
    return FinalDecisionEvaluation(
        allowed=not violations,
        violations=violations,
        simulated=simulated,
        decided=payload.decision,
    )
