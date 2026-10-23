"""项目编辑和归档：短事务、revision、幂等回放，不修改领域模块的审核结果。"""
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.schemas import Project, ProjectPatch
from app.modules.foundation.service import SELECT, now, project_from_row


def manage_project(path: Path, project_id: str, operation: str, revision: str | None,
                   key: str, payload: ProjectPatch | None = None) -> Project:
    if not key.strip():
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency-Key 不能为空白")
    changes = payload.model_dump(mode="json", exclude_unset=True) if payload else {}
    fingerprint = hashlib.sha256(json.dumps([operation, revision, changes], sort_keys=True).encode()).hexdigest()
    scope = f"local-reviewer:demo:project:{project_id}:{operation}"
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(SELECT + " WHERE p.id=? AND p.mode='demo'", (project_id,)).fetchone()
        if row is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在")
        old = db.execute("SELECT * FROM idempotency_records WHERE scope=? AND key=?", (scope, key)).fetchone()
        if old:
            if old["request_hash"] != fingerprint:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
            return Project.model_validate_json(old["response_json"])
        if revision is None:
            raise ApiError(428, "PRECONDITION_REQUIRED", "修改项目必须携带 If-Match revision")
        try:
            current = int(revision)
        except ValueError:
            raise ApiError(400, "VALIDATION_ERROR", "If-Match 必须是整数 revision") from None
        if current != row["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "项目已更新，请刷新后重新操作")
        stamp = now()
        changed = False
        if operation == "edit":
            if row["status"] == "archived":
                raise ApiError(409, "PROJECT_ARCHIVED", "归档项目为只读，请先恢复")
            platforms = changes.get("platforms", json.loads(row["platforms_json"]))
            for removed in set(json.loads(row["platforms_json"])) - set(platforms):
                task = db.execute("SELECT 1 FROM creator_tasks WHERE project_id=? AND platform=? LIMIT 1",
                                  (project_id, removed)).fetchone()
                brief = db.execute("SELECT 1 FROM briefs b JOIN json_each(b.platforms_json) v "
                                   "WHERE b.project_id=? AND v.value=? LIMIT 1", (project_id, removed)).fetchone()
                requirement = db.execute("SELECT 1 FROM requirements r JOIN rule_versions v ON v.id=r.rule_version_id "
                                         "JOIN briefs b ON b.id=v.brief_id JOIN json_each(r.platforms_json) s "
                                         "WHERE b.project_id=? AND s.value=? LIMIT 1", (project_id, removed)).fetchone()
                if task or brief or requirement:
                    raise ApiError(409, "PLATFORM_IN_USE", "该平台已被达人任务或 Brief 使用，不能移除",
                                   fields={"platforms": "保留已有材料使用的平台，避免破坏历史关联"})
            brand_id = row["brand_id"]
            if "brand_name" in changes:
                brand_name = changes["brand_name"]
                brand = db.execute("SELECT id FROM brands WHERE normalized_name=?", (brand_name.casefold(),)).fetchone()
                brand_id = brand["id"] if brand else uuid4().hex
                if not brand:
                    db.execute("INSERT INTO brands VALUES (?,?,?)", (brand_id, brand_name, brand_name.casefold()))
            name = changes.get("name", row["name"])
            description = changes.get("description", row["description"])
            products = changes.get('product_names', json.loads(row['product_names_json']))
            if (name, brand_id, description, platforms, products) != (row["name"], row["brand_id"], row["description"], json.loads(row["platforms_json"]), json.loads(row['product_names_json'])):
                try:
                    db.execute("UPDATE projects SET name=?,normalized_name=?,brand_id=?,description=?,platforms_json=?,product_names_json=?,"
                               "revision=revision+1,updated_at=? WHERE id=?", (name, name.casefold(), brand_id,
                               description, json.dumps(platforms), json.dumps(products, ensure_ascii=False), stamp, project_id))
                except sqlite3.IntegrityError:
                    raise ApiError(409, "PROJECT_EXISTS", "该品牌下已有同名项目", fields={"name": "项目名称重复"}) from None
                changed = True
        else:
            target = "archived" if operation == "archive" else (
                "completed" if row["task_count"] and row["completed_task_count"] == row["task_count"] else "active")
            if operation == "restore" and row["status"] != "archived":
                target = row["status"]
            if target != row["status"]:
                running = db.execute(
                    "SELECT 1 FROM review_jobs j LEFT JOIN creator_tasks t ON t.id=j.task_id "
                    "LEFT JOIN brief_source_versions s ON s.id=j.source_version_id "
                    "LEFT JOIN briefs b ON b.id=s.brief_id WHERE j.mode='demo' "
                    "AND j.status IN ('queued','processing') AND (t.project_id=? OR b.project_id=?) LIMIT 1",
                    (project_id, project_id)).fetchone()
                if operation == "archive" and running:
                    raise ApiError(409, "PROJECT_HAS_RUNNING_JOBS", "项目有排队或执行中的审核，请处理完成后归档")
                db.execute("UPDATE projects SET status=?,revision=revision+1,updated_at=? WHERE id=?",
                           (target, stamp, project_id))
                changed = True
        result = project_from_row(db.execute(SELECT + " WHERE p.id=?", (project_id,)).fetchone())
        if changed:
            db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)", (uuid4().hex, "local-reviewer", "project",
                       project_id, operation, "demo", json.dumps({"fromRevision": row["revision"],
                       "toRevision": result.revision, "fromStatus": row["status"], "toStatus": result.status.value}), stamp))
        db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)",
                   (scope, key, fingerprint, result.model_dump_json(by_alias=True), stamp))
        return result
