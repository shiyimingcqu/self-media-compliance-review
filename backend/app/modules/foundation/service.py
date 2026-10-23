import hashlib
import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.schemas import (Brand, BriefSummary, Project, ProjectCreate, ProjectDetail,
                                             RuleSourceRef, RuleVersionSummary)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def project_from_row(row: sqlite3.Row) -> Project:
    return Project(id=row["id"], name=row["name"], brand=Brand(id=row["brand_id"], name=row["brand_name"]),
                   description=row["description"], platforms=json.loads(row["platforms_json"]), mode=row["mode"],
                   product_names=json.loads(row['product_names_json']),
                   status=row["status"], revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
                   task_count=row["task_count"], completed_task_count=row["completed_task_count"])


SELECT = ("SELECT p.*, b.name AS brand_name, COALESCE(tc.total,0) AS task_count, "
          "COALESCE(tc.completed,0) AS completed_task_count "
          "FROM projects p JOIN brands b ON b.id=p.brand_id LEFT JOIN "
          "(SELECT project_id, COUNT(*) AS total, SUM(stage='completed') AS completed "
          "FROM creator_tasks WHERE archived_at IS NULL GROUP BY project_id) tc ON tc.project_id=p.id")


def get_project(path: Path, project_id: str) -> Project:
    with connection(path) as db:
        row = db.execute(SELECT + " WHERE p.id=? AND p.mode='demo'", (project_id,)).fetchone()
        if row is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在")
        return project_from_row(row)


def project_detail(path: Path, project_id: str) -> ProjectDetail:
    """详情投影（t-28）：一次批量查询组装 brief→latest source→file/状态→versions，避免 N+1。
    - latest source 必须属于本 brief（SQL 已 join brief_id 约束）；不属于或无源→两字段 None，不借他 brief 数据。
    - brief.platforms 原样返回存储范围；旧数据 [] = 未设置，明确不裁剪不伪造。
    - versions 全量真实状态（含 draft/superseded 历史），不预过滤。
    - 读路径不拒归档项目（历史可读）；跨模式沿用 demo 过滤 404。"""
    with connection(path) as db:
        row = db.execute(SELECT + " WHERE p.id=? AND p.mode='demo'", (project_id,)).fetchone()
        if row is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在")
        project = project_from_row(row)
        briefs = db.execute("SELECT id,title,platforms_json,latest_source_version_id FROM briefs "
                            "WHERE project_id=? ORDER BY created_at DESC,id DESC", (project_id,)).fetchall()
        brief_ids = [b["id"] for b in briefs]
        sources = {}
        if brief_ids:
            marks = ",".join("?" * len(brief_ids))
            for s in db.execute(f"SELECT s.id,s.brief_id,s.extraction_status,s.file_id FROM brief_source_versions s "
                                f"WHERE s.brief_id IN ({marks})", brief_ids):
                sources[s["id"]] = s
        versions: dict[str, list] = {b: [] for b in brief_ids}
        if brief_ids:
            marks = ",".join("?" * len(brief_ids))
            # LEFT JOIN：版本绑定源须同 id 且同 brief、file 实际 demo 模式；异常指针=source None 不借数据
            for v in db.execute(
                    f"SELECT r.id,r.brief_id,r.version_label,r.state,r.revision,"
                    f" s.id AS s_id, s.file_id AS s_file_id, s.extraction_status AS s_status,"
                    f" f.mode AS f_mode"
                    f" FROM rule_versions r"
                    f" LEFT JOIN brief_source_versions s"
                    f"   ON s.id = r.source_version_id AND s.brief_id = r.brief_id"
                    f" LEFT JOIN file_assets f ON f.id = s.file_id"
                    f" WHERE r.brief_id IN ({marks}) ORDER BY r.created_at DESC,r.id DESC", brief_ids):
                source = None
                if v["s_id"] and v["f_mode"] == "demo":
                    source = RuleSourceRef(id=v["s_id"], file_id=v["s_file_id"],
                                           extraction_status=v["s_status"])
                versions[v["brief_id"]].append(RuleVersionSummary(id=v["id"], version_label=v["version_label"],
                                                                  state=v["state"], revision=v["revision"],
                                                                  source=source))
        summaries = []
        for b in briefs:
            source = sources.get(b["latest_source_version_id"]) if b["latest_source_version_id"] else None
            if source is not None and source["brief_id"] != b["id"]:
                source = None  # 双保险：latest 指向非本 brief 源时不借数据
            try:
                platforms = json.loads(b["platforms_json"] or "[]")
            except (TypeError, ValueError):
                platforms = []
            summaries.append(BriefSummary(id=b["id"], title=b["title"], platforms=platforms,
                                          latest_source_version_id=source["id"] if source else None,
                                          file_id=source["file_id"] if source else None,
                                          extraction_status=source["extraction_status"] if source else None,
                                          versions=versions.get(b["id"], [])))
        return ProjectDetail(**project.model_dump(), briefs=summaries)


def create_project(path: Path, payload: ProjectCreate, key: str, *, transaction=None) -> Project:
    serialized = payload.model_dump_json(by_alias=True)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    scope = "local-reviewer:demo:POST/projects"
    with (connection(path) if transaction is None else nullcontext(transaction)) as db:
        if transaction is None:
            db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT * FROM idempotency_records WHERE scope=? AND key=?", (scope, key)).fetchone()
        if prior:
            if prior["request_hash"] != fingerprint:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
            return Project.model_validate_json(prior["response_json"])
        brand_id = payload.brand_id
        if brand_id:
            if not db.execute("SELECT 1 FROM brands WHERE id=?", (brand_id,)).fetchone():
                raise ApiError(400, "VALIDATION_ERROR", "请选择存在的品牌", fields={"brandId": "品牌不存在"})
        else:
            brand_name = payload.brand_name or ""
            brand = db.execute("SELECT id FROM brands WHERE normalized_name=?", (brand_name.casefold(),)).fetchone()
            brand_id = brand["id"] if brand else uuid4().hex
            if not brand:
                db.execute("INSERT INTO brands VALUES (?,?,?)", (brand_id, brand_name, brand_name.casefold()))
        project_id, stamp = uuid4().hex, now()
        try:
            db.execute("INSERT INTO projects (id,name,normalized_name,brand_id,description,platforms_json,mode,created_at,updated_at) VALUES (?,?,?,?,?,?,'demo',?,?)",
                       (project_id, payload.name, payload.name.casefold(), brand_id, payload.description,
                        json.dumps([p.value for p in payload.platforms]), stamp, stamp))
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "PROJECT_EXISTS", "此品牌下已有同名项目", fields={"name": "项目名称重复"}) from exc
        db.execute('UPDATE projects SET product_names_json=? WHERE id=?', (json.dumps(payload.product_names, ensure_ascii=False), project_id))
        row = db.execute(SELECT + " WHERE p.id=?", (project_id,)).fetchone()
        project = project_from_row(row)
        db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)", (scope, key, fingerprint, project.model_dump_json(by_alias=True), stamp))
        db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)", (uuid4().hex, "local-reviewer", "project", project_id, "created", "demo", "{}", stamp))
        return project


def list_projects(path: Path, *, q: str, platform: str | None, status: str | None, brand_id: str | None,
                  page: int, page_size: int) -> tuple[list[Project], int]:
    clauses, values = ["p.mode='demo'"], []
    if q:
        clauses.append("instr(lower(p.name),lower(?))>0")
        values.append(q.strip())
    if platform:
        clauses.append("EXISTS(SELECT 1 FROM json_each(p.platforms_json) WHERE value=?)")
        values.append(platform)
    if status:
        clauses.append("p.status=?")
        values.append(status)
    else:
        clauses.append("p.status!='archived'")
    if brand_id:
        clauses.append("p.brand_id=?")
        values.append(brand_id)
    where = " WHERE " + " AND ".join(clauses)
    with connection(path) as db:
        total = db.execute("SELECT COUNT(*) FROM projects p" + where, values).fetchone()[0]
        rows = db.execute(SELECT + where + " ORDER BY p.updated_at DESC,p.id DESC LIMIT ? OFFSET ?",
                          [*values, page_size, (page - 1) * page_size]).fetchall()
        return [project_from_row(r) for r in rows], total
