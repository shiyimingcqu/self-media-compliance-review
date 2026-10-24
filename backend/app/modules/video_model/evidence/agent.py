"""LangChain 结构化工具 + Qwen Chat Completions 有界循环；不依赖高层 SDK 改写视频输入。"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tools import EvidenceSession, build_tools


class AnalysisFailure(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def parse_report(content: str) -> AgentReport:
    # Some compatible services wrap otherwise valid JSON in a Markdown fence.
    value = content.strip()
    if value.startswith('```') and value.endswith('```'):
        value = value.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        return AgentReport.model_validate_json(value)
    except ValidationError as exc:
        kinds = {error['type'] for error in exc.errors()}
        if 'json_invalid' in kinds:
            raise AnalysisFailure('FINAL_REPORT_INVALID_JSON', '模型最终回复不是有效 JSON；已保留回复与取证，不会自动重试') from None
        raise AnalysisFailure('FINAL_REPORT_SCHEMA_INVALID', '模型最终 JSON 字段或结论格式不符合报告要求；已保留回复与取证') from None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    rule_id: str
    rule_number: int | None=Field(default=None,ge=1)
    verdict: str = Field(pattern="^(compliant|non_compliant|needs_confirmation|not_applicable)$")
    description: str
    observation_type: Literal['observed', 'absence', 'uncertain'] = 'uncertain'
    evidence_ids: list[str] = Field(default_factory=list)


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding]
    summary: str


def validate_evidence_references(report: AgentReport, artifacts: list[dict], source_sha256: str) -> list[dict[str, str]]:
    """同次调用的原片证据可多对多引用，生成问题归属不改写；不判断证据语义。"""
    available = {artifact['id']: artifact for artifact in artifacts}
    references = []
    for finding in report.findings:
        for identity in dict.fromkeys(finding.evidence_ids):
            artifact = available.get(identity)
            if artifact is None:
                raise AnalysisFailure('FINAL_REPORT_EVIDENCE_MISMATCH', '证据 ID 不属于本次调用或不存在；已保留模型回复与有效取证')
            if not source_sha256 or artifact.get('sourceSha256') != source_sha256:
                raise AnalysisFailure('FINAL_REPORT_EVIDENCE_MISMATCH', '引用证据与本次原视频哈希不一致；已保留模型回复与有效取证')
            references.append({'evidenceId': identity, 'issueId': finding.id,
                               'ruleId': finding.rule_id, 'originIssueId': artifact['issueId']})
    return references


from app.modules.workflow.prompts import prompt

MAX_AUTO_CLIPS_PER_RULE = 3
AUTO_CLIP_MS = 30_000


def component_rules_complete(session) -> bool:
    """所有带 componentPlan 的规则都已调齐组件，才允许用组件结果重建报告。"""
    from .component_runtime import components_enabled
    if not components_enabled():
        return False
    planned = [rule for rule in session.rules.values() if rule.get('componentPlan')]
    if not planned:
        return False
    return all((rule['id'], node['id']) in session.component_results
               for rule in planned for node in rule['componentPlan']['nodes'])


def component_report(session) -> AgentReport:
    """模型最终报告缺失/不完整时，按冻结参数与组件结果重建结论；无组件的规则交人工。"""
    from .component_runtime import aggregate_rule
    findings = []
    for rule in session.rules.values():
        number = rule.get('ruleNumber')
        if rule.get('componentPlan'):
            combination = aggregate_rule(session, rule)
            findings.append(Finding(id=combination['issueId'], rule_id=rule['id'], rule_number=number,
                                    verdict=combination['verdict'], description=combination['reason']))
        else:
            findings.append(Finding(id='issue-' + rule['id'], rule_id=rule['id'], rule_number=number,
                                    verdict='needs_confirmation',
                                    description='模型未给出该规则的结论，且该规则没有可重建的检查组件，需人工核对'))
    return AgentReport(findings=findings,
                       summary='模型最终报告缺失或不完整；组件规则的结论已按冻结参数重建，未通过或待确认项须品牌复核。')


def attach_uncertain_frames(session, report: AgentReport) -> list[str]:
    """组件因为身份未确认停在待确认时，自动截一帧画面供人工确认（不依赖模型是否记得取证）。"""
    created: list[str] = []
    for finding in report.findings:
        rule = session.rules.get(finding.rule_id) or {}
        plan = rule.get('componentPlan')
        if not plan:
            continue
        values = [session.component_results.get((rule['id'], node['id'])) for node in plan['nodes']]
        if any(value is None for value in values):
            continue
        if any(value['verdict'] != 'needs_confirmation' for value in values):
            continue
        intervals = [interval for value in values
                     for interval in ((value.get('metrics') or {}).get('observedOccurrenceIntervals')
                                      or value.get('observedIntervals') or value.get('intervals') or [])]
        if not intervals:
            continue
        start = min(a for a, _ in intervals); end = max(b for _, b in intervals)
        if any(artifact['kind'] == 'image' and artifact['issueId'] == finding.id for artifact in session.artifacts):
            continue
        try:
            artifact = session.screenshot(int((start + end) / 2), finding.id)
        except Exception:
            # 截图是补充说明，失败不影响结论（例如环境缺 ffmpeg）。
            continue
        finding.evidence_ids.append(artifact['id'])
        created.append(artifact['id'])
    return created


def attach_component_evidence(session, report: AgentReport) -> list[str]:
    """对组件判为不合规的规则，按组件区间自动裁片（不依赖模型是否记得取证）。
    每规则最多 3 段、每段不超过 30 秒；裁片失败不阻断结论，仅缺少该段证据。"""
    created: list[str] = []
    for finding in report.findings:
        rule = session.rules.get(finding.rule_id) or {}
        if not rule.get('componentPlan') or finding.verdict != 'non_compliant':
            continue
        existing = sum(1 for artifact in session.artifacts
                       if artifact['kind'] == 'clip' and artifact['issueId'] == finding.id)
        intervals = sorted({(int(start), int(end))
                            for node in rule['componentPlan']['nodes']
                            for value in (session.component_results.get((rule['id'], node['id'])),)
                            if value and value.get('verdict') == 'non_compliant'
                            for start, end in (value.get('intervals') or [])})
        for start, end in intervals:
            if existing >= MAX_AUTO_CLIPS_PER_RULE:
                break
            end = min(end, start + AUTO_CLIP_MS)
            if end <= start:
                continue
            try:
                artifact = session.clip(start, end, finding.id)
            except Exception:
                continue
            if artifact['id'] not in finding.evidence_ids:
                finding.evidence_ids.append(artifact['id'])
                created.append(artifact['id'])
                existing += 1
    return created

SYSTEM = prompt('video-review')['content']


def run_agent(session: EvidenceSession, complete: Callable[[list, list], dict], *, max_rounds: int = 3,
              max_tool_calls: int = 24, max_inline_bytes: int | None = None, video_input: Path | None = None,
              system_prompt: str | None = None, final_prompt: str | None = None,
              batch_observations: bool = False, video_url: str | None = None) -> dict:
    """complete 为受信传输，可用 Mock 或经显式授权的真实 HTTP；无自动重试。

    batch_observations=True：首轮一次性提交全部 check_*（允许并行），工具结果返回后直接出报告；
    仅当首轮有工具执行失败时允许一次修复轮——把轮次从 3-4 降到 2，避免每轮重复投喂整段视频。
    """
    from langchain_core.utils.function_calling import convert_to_openai_tool
    if not 1 <= max_rounds <= 3 or not 1 <= max_tool_calls <= 24:
        raise ValueError("工具循环预算超出上限")
    model_video = video_input or session.video
    if model_video.suffix.lower() != ".mp4":
        raise ValueError("首批完整内联输入只支持 MP4，请先确认完整转码版本")
    if max_inline_bytes is not None and model_video.stat().st_size > max_inline_bytes:
        raise ValueError("完整视频超过本地内联输入上限，请使用后续受控上传适配，不截取替代原片")
    tools = {tool.name: tool for tool in build_tools(session)}
    definitions = [convert_to_openai_tool(tool) for tool in tools.values()]
    if video_url is not None and not video_url.startswith('oss://'):
        raise ValueError('视频引用必须来自受控临时上传')
    media_url = video_url or ('data:video/mp4;base64,' + base64.b64encode(model_video.read_bytes()).decode('ascii'))
    from app.modules.workflow.video_rule_boundaries import assessment_boundaries
    messages = [{"role": "system", "content": system_prompt or SYSTEM}, {"role": "user", "content": [
        {"type": "video_url", "video_url": {"url": media_url}},
        {"type": "text", "text": json.dumps({"rules": list(session.rules.values()),
         "assessmentBoundaries":assessment_boundaries(session.rules.values()),
         "approvedScript": session.script, "media": session.metadata()}, ensure_ascii=False)}]}]
    for reference in session.references:
        messages[1]['content'].extend([{'type':'text','text':json.dumps({k:v for k,v in reference.items() if k!='data'},ensure_ascii=False)},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(reference['data']).decode('ascii')}}])
    used = 0
    usages = []
    request_ids = []
    failed_tools = False
    repair_used = False
    for turn in range(max_rounds + 1):
        if batch_observations:
            tools_allowed = turn == 0 or (turn == 1 and failed_tools and not repair_used and max_rounds >= 2)
        else:
            tools_allowed = turn < max_rounds
        if not tools_allowed:
            messages.append({"role": "user", "content":
                (final_prompt or prompt('video-final-report')['content']) + "\nJSON Schema:" + json.dumps(AgentReport.model_json_schema(), ensure_ascii=False)})
        reply = complete(messages, definitions if tools_allowed else [])
        if reply.get("id"):
            request_ids.append(reply["id"])
        if reply.get("usage"):
            usages.append(reply["usage"])
        message = reply["choices"][0]["message"]
        if len(json.dumps(message)) > 200_000:
            raise ValueError("模型响应超出处理上限")
        calls = message.get("tool_calls") or []
        if not calls:
            recovery = None
            try:
                report = parse_report(message.get("content") or "")
                ids = [f.rule_id for f in report.findings]
                if len(ids) != len(session.rules) or set(ids) != set(session.rules):
                    raise AnalysisFailure("FINAL_REPORT_RULE_MISMATCH", "审核规则缺项、重复或引用无效；已保留模型回复")
            except AnalysisFailure as exc:
                # 只兜住"模型没输出全/格式无效"；结论与组件不一致仍按失败处理，不静默覆盖模型。
                if (exc.code not in ("FINAL_REPORT_INVALID_JSON", "FINAL_REPORT_SCHEMA_INVALID", "FINAL_REPORT_RULE_MISMATCH")
                        or not component_rules_complete(session)):
                    raise
                report = component_report(session)
                recovery = {"method": "component_results", "reason": exc.code,
                            "message": "模型最终报告缺失或不完整；结论由组件计算结果重建，未通过或待确认项仍须品牌复核",
                            "providerResubmitted": False}
            ids = [f.rule_id for f in report.findings]
            if len({f.id for f in report.findings}) != len(report.findings):
                raise AnalysisFailure("FINAL_REPORT_DUPLICATE_ISSUE", "问题 ID 重复；已保留模型回复")
            combinations=[]
            for finding in report.findings:
                rule=session.rules[finding.rule_id]
                number=rule.get('ruleNumber')
                if finding.rule_number is not None and number is not None and finding.rule_number!=number:
                    raise AnalysisFailure('COMPONENT_RULE_NUMBER_MISMATCH','模型返回的规则编号与冻结规则不一致')
                finding.rule_number=number
                from .component_runtime import components_enabled as _components_enabled
                if rule.get('componentPlan') and _components_enabled():
                    from .component_runtime import aggregate_rule
                    try:combination=aggregate_rule(session,rule)
                    except ValueError:
                        raise AnalysisFailure('COMPONENT_CHECK_INCOMPLETE','模型遗漏了品牌配置的检查组件，未生成完整审核资格') from None
                    if combination['verdict']!=finding.verdict or combination['issueId']!=finding.id:
                        raise AnalysisFailure('COMPONENT_RESULT_MISMATCH','最终结论与组件组合结果或问题ID不一致，未生成通过资格')
                    combinations.append(combination)
            auto_evidence = attach_component_evidence(session, report)
            auto_evidence += attach_uncertain_frames(session, report)
            session.evidence_references = validate_evidence_references(report, session.artifacts, session.sha256)
            session.save_manifest()
            rendered_report = report.model_dump()
            from app.modules.workflow.video_layers import layer
            for finding in rendered_report['findings']:
                finding['layer'] = layer(session.rules[finding['rule_id']])
            from .component_runtime import components_enabled as _enabled
            return {"componentMode": "on" if _enabled() else "off", "report": rendered_report, "artifacts": session.artifacts, "usage": usages,
                    "evidenceReferences": session.evidence_references,
                    "autoEvidenceIds": auto_evidence, "recovery": recovery,
                    "componentResults":combinations,"toolCalls":session.calls,
                    "providerRequestIds": request_ids,
                    "conclusion": "incomplete", "coverageVerified": False,
                    "note": "完成工具取证不证明音视频分析完整性；需由品牌核对完整材料并作最终确认"}
        if not tools_allowed or used + len(calls) > max_tool_calls:
            raise AnalysisFailure("TOOL_BUDGET_EXCEEDED", "模型超出工具调用预算，未产生完整审核结果；已保留有效取证")
        if batch_observations and turn == 1:
            repair_used = True
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        for call in calls:
            used += 1
            name = call["function"]["name"]
            try:
                if name not in tools:
                    raise ValueError("工具不在本次白名单")
                arguments = json.loads(call["function"]["arguments"])
                result = tools[name].invoke(arguments)
                observation = {"ok": True, "data": result}
            except Exception:
                # 不回显 FFmpeg、本地路径、模型输入及供应商秘密。
                observation = {"ok": False, "error": "工具执行失败，请核对时间、规则ID、组件ID、观察字段或取证数量；使用冻结参数，同一规则保持同一个issue_id"}
                failed_tools = True
            session.calls.append({"toolCallId": call["id"], "name": name, "ok": observation["ok"]})
            session.save_manifest()
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(observation, ensure_ascii=False)})
    raise ValueError("审核未完成")
