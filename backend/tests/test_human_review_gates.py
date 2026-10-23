"""D 人工复核门禁策略纯函数测试（不触数据库、不启动服务、不跑整库迁移）。

覆盖 specs/human-review HR-03/04/06/07 场景与协调者裁决细则：
scope 区分、建议项不自动阻断、缺输入/版本未知/理由空白拒绝、
demo 不跳过结构门禁且不产生真实资格。
"""
import pytest

from app.core.contracts import ReviewScope, ReviewSnapshot, RunMode
from app.core.review_contracts import AnalysisIntegrity
from app.modules.human_review import decision
from app.modules.human_review.schemas import (
    FinalDecisionPayload,
    IssueDecisionInput,
    IssueRef,
    ManualIssueInput,
)


def snapshot(mode: RunMode = RunMode.DEMO) -> ReviewSnapshot:
    return ReviewSnapshot(
        mode=mode,
        rule_version_id="rv-1",
        script_version_id="sv-1",
        video_version_id="vv-1",
        model_config_id="mc-1",
        model_config_revision=3,
        capability_report_id="cr-1",
    )


def integrity(
    rules_complete: bool = True,
    video: str = "complete",
    audio: str = "complete",
) -> AnalysisIntegrity:
    return AnalysisIntegrity(
        rules_complete=rules_complete, video=video, audio=audio
    )


def payload(**overrides) -> FinalDecisionPayload:
    base = dict(
        scope=ReviewScope.VIDEO,
        decision="approved",
        comment="已核对全部证据，同意当前版本",
        snapshot=snapshot(),
        integrity=integrity(),
        issues=[],
        decisions=[],
    )
    base.update(overrides)
    return FinalDecisionPayload(**base)


def must_issue(issue_id: str = "i-1", origin: str = "ai") -> IssueRef:
    return IssueRef(issue_id=issue_id, importance="must", origin=origin)


def codes(violations) -> list[str]:
    return [v.code for v in violations]


# ---------- HR-06：最终决定必须主动选择 ----------

def test_missing_decision_rejected_not_defaulting_to_approve():
    result = decision.evaluate_final_decision(payload(decision=None))
    assert not result.allowed
    assert decision.DECISION_REQUIRED in codes(result.violations)


def test_approve_without_comment_rejected():
    result = decision.evaluate_final_decision(payload(comment="  "))
    assert not result.allowed
    assert decision.COMMENT_REQUIRED in codes(result.violations)


def test_return_without_reason_rejected():
    result = decision.evaluate_final_decision(payload(decision="returned", comment=""))
    assert not result.allowed
    assert decision.RETURN_REASON_REQUIRED in codes(result.violations)


def test_clean_approve_allowed_and_simulated_in_demo():
    result = decision.evaluate_final_decision(payload())
    assert result.allowed
    assert result.simulated  # demo 干净通过也只落模拟记录
    assert result.decided == "approved"


# ---------- HR-06：缺输入 / 版本未知一律拒绝 ----------

def test_unknown_snapshot_rejects_both_directions():
    for choice in ("approved", "returned"):
        result = decision.evaluate_final_decision(
            payload(decision=choice, snapshot=None))
        assert not result.allowed
        assert decision.SNAPSHOT_UNKNOWN in codes(result.violations)


def test_missing_integrity_blocks_approve_only():
    # dispute ②：缺分析完整性输入阻断 approved；returned 不因缺 integrity 阻断。
    approved = decision.evaluate_final_decision(payload(integrity=None))
    assert not approved.allowed
    assert decision.ANALYSIS_INPUT_MISSING in codes(approved.violations)


def test_missing_integrity_return_with_valid_snapshot_allowed():
    # 异常待办可能尚无分析结果：有效快照 + 明确原因的退回是合法出口。
    result = decision.evaluate_final_decision(
        payload(decision="returned", comment="作业分析失败且暂无结果，先退回处理",
                integrity=None))
    assert result.allowed
    assert result.decided == "returned"


def test_missing_integrity_return_without_reason_still_rejected():
    result = decision.evaluate_final_decision(
        payload(decision="returned", comment="  ", integrity=None))
    assert not result.allowed
    assert decision.RETURN_REASON_REQUIRED in codes(result.violations)


# ---------- HR-06：完整性门禁（scope 区分） ----------

def test_audio_failure_blocks_video_approve():
    result = decision.evaluate_final_decision(payload(integrity=integrity(audio="failed")))
    assert not result.allowed
    assert decision.AUDIO_ANALYSIS_INCOMPLETE in codes(result.violations)


def test_missing_audio_track_also_blocks_video_approve():
    result = decision.evaluate_final_decision(payload(integrity=integrity(audio="missing")))
    assert not result.allowed
    assert decision.AUDIO_ANALYSIS_INCOMPLETE in codes(result.violations)


def test_incomplete_video_blocks_approve():
    result = decision.evaluate_final_decision(
        payload(integrity=integrity(video="incomplete")))
    assert not result.allowed
    assert decision.VIDEO_ANALYSIS_INCOMPLETE in codes(result.violations)


def test_rules_incomplete_blocks_approve():
    result = decision.evaluate_final_decision(
        payload(integrity=integrity(rules_complete=False)))
    assert not result.allowed
    assert decision.RULES_INCOMPLETE in codes(result.violations)


def test_script_scope_does_not_apply_audio_requirements():
    # script 复核只认规则完整性；即便音轨字段异常也不因音轨阻断。
    result = decision.evaluate_final_decision(
        payload(scope=ReviewScope.SCRIPT, integrity=integrity(audio="failed")))
    assert result.allowed


def test_script_scope_still_requires_rules_complete():
    result = decision.evaluate_final_decision(
        payload(scope=ReviewScope.SCRIPT, integrity=integrity(rules_complete=False)))
    assert not result.allowed
    assert decision.RULES_INCOMPLETE in codes(result.violations)


def test_return_allowed_when_analysis_incomplete():
    # 异常待办的出口：分析不完整时允许退回（带原因），不放行通过。
    result = decision.evaluate_final_decision(
        payload(decision="returned", comment="音轨分析失败，请补充分析后重新提交",
                integrity=integrity(audio="failed")))
    assert result.allowed


# ---------- HR-03：逐条裁决语义 ----------

def test_confirmed_must_issue_blocks_approve():
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="confirmed")]))
    assert not result.allowed
    assert decision.MUST_ISSUE_CONFIRMED in codes(result.violations)


def test_deferred_must_issue_blocks_approve():
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="deferred")]))
    assert not result.allowed
    assert decision.DEFERRED_BLOCKS_APPROVAL in codes(result.violations)


def test_undecided_must_issue_blocks_approve():
    result = decision.evaluate_final_decision(payload(issues=[must_issue()]))
    assert not result.allowed
    assert decision.MUST_ISSUE_UNRESOLVED in codes(result.violations)


def test_false_positive_without_reason_rejected():
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="false_positive",
                                              reason="   ")]))
    assert not result.allowed
    assert decision.FALSE_POSITIVE_REASON_REQUIRED in codes(result.violations)


def test_false_positive_with_reason_resolves_must_issue():
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="false_positive",
                                              reason="原文上下文表明该表达属剧情反转")]))
    assert result.allowed


def test_decision_referencing_unknown_issue_rejected():
    result = decision.evaluate_final_decision(
        payload(decisions=[IssueDecisionInput(issue_id="ghost", action="confirmed")]))
    assert not result.allowed
    assert decision.UNKNOWN_ISSUE_REFERENCE in codes(result.violations)


# ---------- dispute ①：重复裁决与重复问题 ID，与顺序无关 ----------

def test_confirmed_then_false_positive_duplicate_rejected():
    # 原缺陷场景：末项覆盖会令 confirmed 被后续 false_positive 抹掉。
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="confirmed"),
                           IssueDecisionInput(issue_id="i-1", action="false_positive",
                                              reason="上下文表明误判")]))
    assert not result.allowed
    assert decision.DUPLICATE_ISSUE_DECISION in codes(result.violations)


def test_false_positive_then_confirmed_duplicate_also_rejected():
    # 顺序互换：同样拒绝，规则不依赖裁决排列顺序。
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="false_positive",
                                              reason="上下文表明误判"),
                           IssueDecisionInput(issue_id="i-1", action="confirmed")]))
    assert not result.allowed
    assert decision.DUPLICATE_ISSUE_DECISION in codes(result.violations)


def test_duplicate_confirmed_decisions_rejected():
    # 同一动作的重复提交同样拒绝，不给"重放"留口子。
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue()],
                decisions=[IssueDecisionInput(issue_id="i-1", action="confirmed"),
                           IssueDecisionInput(issue_id="i-1", action="confirmed")]))
    assert not result.allowed
    assert decision.DUPLICATE_ISSUE_DECISION in codes(result.violations)


def test_duplicate_issue_ids_in_collection_rejected():
    issues = [must_issue("i-1"), must_issue("i-1")]
    result = decision.evaluate_final_decision(payload(issues=issues))
    assert not result.allowed
    assert decision.DUPLICATE_ISSUE_REFERENCE in codes(result.violations)


def test_distinct_issue_decisions_still_allowed():
    # 不同问题各自一条裁决不受影响。
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue("i-1"), must_issue("i-2")],
                decisions=[IssueDecisionInput(issue_id="i-1", action="false_positive",
                                              reason="依据一"),
                           IssueDecisionInput(issue_id="i-2", action="false_positive",
                                              reason="依据二")]))
    assert result.allowed


# ---------- 协调者裁决：建议项不自动阻断 ----------

def test_suggested_issue_never_blocks_approve():
    issues = [IssueRef(issue_id="s-1", importance="suggested"),
              IssueRef(issue_id="s-2", importance="suggested", origin="human")]
    # 建议项未裁决 / 暂不处理 / 确认成立均不自动阻断。
    result = decision.evaluate_final_decision(payload(issues=issues))
    assert result.allowed
    result = decision.evaluate_final_decision(
        payload(issues=issues, decisions=[
            IssueDecisionInput(issue_id="s-1", action="deferred"),
            IssueDecisionInput(issue_id="s-2", action="confirmed")]))
    assert result.allowed


def test_human_origin_must_issue_still_blocks():
    result = decision.evaluate_final_decision(
        payload(issues=[must_issue(origin="human")]))
    assert not result.allowed
    assert decision.MUST_ISSUE_UNRESOLVED in codes(result.violations)


# ---------- demo：不跳过结构门禁、只产生模拟标记 ----------
# 注意：以下用例仅验证本模块对 payload.snapshot.mode 的 simulated 标志计算；
# demo→real 混用防护（历史模拟记录不得解锁真实任务）属服务端 eligibility
# 的 DB 验证域，不在纯函数测试范围内（见 decision.py 服务端验证边界说明）。

def test_demo_does_not_skip_structural_gates():
    # decision 未选 + 快照未知：结构门禁照常拒绝。
    # （integrity=None 不再无条件触发违规——只阻断 approved，
    #   由 test_missing_integrity_blocks_approve_only 专门验证。）
    result = decision.evaluate_final_decision(
        payload(decision=None, snapshot=None, integrity=None))
    assert not result.allowed
    assert decision.DECISION_REQUIRED in codes(result.violations)
    assert decision.SNAPSHOT_UNKNOWN in codes(result.violations)


def test_demo_clean_pass_is_simulation_only():
    # 仅证明：demo 快照的干净通过也只带 simulated 标志。
    result = decision.evaluate_final_decision(payload())
    assert result.allowed and result.simulated


def test_real_mode_snapshot_not_marked_simulated():
    # 仅证明：real 快照通过时 simulated 为 False；
    # 不构成"模拟记录不会混入真实任务"的证据——该保证由服务端 eligibility 落实。
    result = decision.evaluate_final_decision(
        payload(snapshot=snapshot(mode=RunMode.REAL)))
    assert result.allowed and not result.simulated


# ---------- HR-04：人工补充问题区间与描述 ----------

def test_manual_issue_end_not_greater_than_start_rejected():
    issue = ManualIssueInput(start_ms=1000, end_ms=1000, description="画面问题")
    violations = decision.validate_manual_issue(issue, duration_ms=5000)
    assert decision.TIME_RANGE_INVALID in codes(violations)


def test_manual_issue_end_beyond_duration_rejected():
    issue = ManualIssueInput(start_ms=4000, end_ms=6000, description="画面问题")
    violations = decision.validate_manual_issue(issue, duration_ms=5000)
    assert decision.TIME_RANGE_INVALID in codes(violations)


def test_manual_issue_valid_range_accepted():
    issue = ManualIssueInput(start_ms=0, end_ms=5000, description="画面问题")
    assert decision.validate_manual_issue(issue, duration_ms=5000) == []


def test_manual_issue_blank_description_rejected():
    issue = ManualIssueInput(start_ms=0, end_ms=100, description="   ")
    violations = decision.validate_manual_issue(issue, duration_ms=5000)
    assert decision.DESCRIPTION_REQUIRED in codes(violations)


def test_manual_issue_unknown_duration_rejected():
    issue = ManualIssueInput(start_ms=0, end_ms=100, description="画面问题")
    violations = decision.validate_manual_issue(issue, duration_ms=None)
    assert decision.DURATION_UNKNOWN in codes(violations)
