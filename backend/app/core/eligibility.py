"""确定性资格服务（单源门禁）：全部条件实查数据库，默认拒绝；无 enabled 数据时恒 false。
业务归属：rules_enabled 语义提案归 B（规则启用），判定实现归基础单写；B/C/D 只消费不复制。"""

import json
import sqlite3
from dataclasses import dataclass

from app.core.database import connection
from app.core.errors import ApiError
from pathlib import Path

APPLICABLE_ALL = "general"


def applicable(requirement: dict, platform: str, phase: str, content_type: str = "video") -> bool:
    """适用范围共享纯谓词（B enable 门禁与基础绑定校验唯一实现，禁止两套逻辑漂移）：
    平台匹配 ∧ 阶段匹配（或通用）∧ 内容形式匹配（或通用）。"""
    return (platform in (requirement.get("platforms") or [])
            and (phase in (requirement.get("phases") or []) or APPLICABLE_ALL in (requirement.get("phases") or []))
            and (content_type in (requirement.get("content_types") or [])
                 or APPLICABLE_ALL in (requirement.get("content_types") or [])))


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    code: str  # OK 时为空
    message: str


DENY = lambda code, msg: Eligibility(False, code, msg)  # noqa: E731


def _json_list(raw: str) -> list:
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def rules_enabled(db: sqlite3.Connection, task_id: str, mode: str = "demo") -> Eligibility:
    """既有任务的资格判定（A 23:36 裁决 B：与"可新绑定"入口分离）：
    1) 任务存在、未归档，且属于当前模式的项目（项目/模式归属）；
    2) 绑定 rule_version 存在且 state IN ('enabled','superseded')——superseded 是历史不可变快照，
       对既有绑定**仍有效**（新启用不自动失效旧任务）；superseded≠撤销≠禁用，真实撤销另行定义；
       可新绑定/换绑目标只认 'enabled'（见 bindable_version），两入口语义由测试分离锁定；
    3) 该版本无未解决冲突（state='open'）；
    4) 存在适用必须项：importance='must' ∧ confirmation='confirmed' ∧
       平台匹配任务平台 ∧ 阶段匹配（任务阶段或通用）；
    5) 规则的平台/阶段适用范围与任务一致（不允许跨平台绑定）。"""
    task = db.execute(
        "SELECT t.*, p.mode AS project_mode, p.status AS project_status, p.platforms_json AS project_platforms "
        "FROM creator_tasks t JOIN projects p ON p.id=t.project_id "
        "WHERE t.id=? AND t.archived_at IS NULL AND p.mode=?", (task_id, mode)).fetchone()
    if task is None:
        return DENY("RESOURCE_NOT_FOUND", "任务不存在或不属于当前模式")
    if task["project_status"] == "archived":
        return DENY("PROJECT_ARCHIVED", "归档项目不能继续审核")
    from app.modules.workflow.standards import sets_for, sets_ready
    independent = sets_for(db, task_id)
    if independent['script'] or independent['video']:
        if sets_ready(db, task):
            return Eligibility(True, "", "")
        return DENY('STANDARDS_NOT_CONFIRMED', '项目脚本与视频规则尚未同时确认，或达人平台不属于项目')
    if not task["rule_version_id"]:
        return DENY("RULES_NOT_ENABLED", "任务尚未绑定规则版本")
    version = db.execute("SELECT id,state,brief_id FROM rule_versions WHERE id=?",
                         (task["rule_version_id"],)).fetchone()
    if version is None:
        return DENY("RULES_NOT_ENABLED", "绑定的规则版本不存在")
    if version["state"] not in ("enabled", "superseded"):
        return DENY("RULES_NOT_ENABLED", "绑定的规则版本未启用（draft/parsing/parse_failed/pending_confirmation 不放行）")
    brief_project = db.execute("SELECT project_id FROM briefs WHERE id=?", (version["brief_id"],)).fetchone()
    if brief_project is None or brief_project["project_id"] != task["project_id"]:
        return DENY("RULE_SCOPE_MISMATCH", "规则版本不属于任务所在项目")
    open_conflicts = db.execute("SELECT COUNT(*) FROM rule_conflicts WHERE rule_version_id=? AND state='open'",
                                (version["id"],)).fetchone()[0]
    if open_conflicts:
        return DENY("RULES_NOT_ENABLED", "规则版本存在未解决冲突")
    rows = db.execute("SELECT platforms_json,phases_json,content_types_json FROM requirements WHERE rule_version_id=? "
                      "AND importance='must' AND confirmation='confirmed'", (version["id"],)).fetchall()
    for row in rows:
        requirement = {"platforms": _json_list(row["platforms_json"]), "phases": _json_list(row["phases_json"]),
                       "content_types": _json_list(row["content_types_json"])}
        if applicable(requirement, task["platform"], task["campaign_phase"], task["content_type"]):
            return Eligibility(True, "", "")
    return DENY("RULE_SCOPE_MISMATCH", "没有与任务平台/阶段/内容形式匹配的已确认必须项")


def rules_enabled_for_db(path: Path, task_id: str, mode: str = "demo") -> Eligibility:
    with connection(path) as db:
        return rules_enabled(db, task_id, mode)


def bindable_version(db: sqlite3.Connection, project_id: str, platform: str, phase: str,
                     content_type: str, rule_version_id: str, mode: str = "demo") -> Eligibility:
    """任务创建/换绑前的静态兼容校验：content_type 由调用方显式传入（不默认忽略），
    平台/阶段/内容形式判定与 rules_enabled 共用 applicable 谓词。"""
    project = db.execute("SELECT id,platforms_json,mode,status FROM projects WHERE id=? AND mode=?",
                         (project_id, mode)).fetchone()
    if project is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在")
    if project["status"] == "archived":
        return DENY("PROJECT_ARCHIVED", "归档项目不能绑定新规则")
    if platform not in _json_list(project["platforms_json"]):
        return DENY("VALIDATION_ERROR", "平台不属于项目已选平台")
    version = db.execute("SELECT id,brief_id,state FROM rule_versions WHERE id=?", (rule_version_id,)).fetchone()
    if version is None:
        return DENY("VALIDATION_ERROR", "规则版本不存在")
    if version["state"] != "enabled":
        return DENY("RULES_NOT_ENABLED", "只能绑定已启用的规则版本")
    brief = db.execute("SELECT b.project_id FROM briefs b JOIN projects p ON p.id=b.project_id "
                       "WHERE b.id=? AND p.mode=?", (version["brief_id"], mode)).fetchone()
    if brief is None or brief["project_id"] != project_id:
        return DENY("RULE_SCOPE_MISMATCH", "规则版本不属于该项目")
    if db.execute("SELECT COUNT(*) FROM rule_conflicts WHERE rule_version_id=? AND state='open'",
                  (version["id"],)).fetchone()[0]:
        return DENY("RULES_NOT_ENABLED", "规则版本存在未解决冲突")
    rows = db.execute("SELECT platforms_json,phases_json,content_types_json FROM requirements "
                      "WHERE rule_version_id=? AND importance='must' AND confirmation='confirmed'",
                      (version["id"],)).fetchall()
    for row in rows:
        requirement = {"platforms": _json_list(row["platforms_json"]), "phases": _json_list(row["phases_json"]),
                       "content_types": _json_list(row["content_types_json"])}
        if applicable(requirement, platform, phase, content_type):
            return Eligibility(True, "", "")
    return DENY("RULE_SCOPE_MISMATCH", "没有与任务平台/阶段/内容形式匹配的已确认必须项")
