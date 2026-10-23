"""规则人工确认与启用核心（t-20 修订，A 00:10 收口）：
- 归属/模式先行：get_version JOIN briefs+projects 且 p.mode 过滤，非 demo 一律 404；所有读写经它取归属。
- 派生草稿=同事务真实复制 requirements（新 id、lineage 保留）与 open 冲突（映射新行）；旧启用版零写入。
- validate_source：quote 必须是对应段落规范化子串；manualNote 与文档坐标互斥。
- coverage 签署与补录要求经 B_0005 关联表真实持久化；enable 承认"每条 uncovered 有签署且其关联补录
  项同版本 confirmed"——旧无关联签名不放行。
- 适用判定与 bindable_version 共用 core.eligibility.applicable（平台/阶段/内容形式一套语义）。
- 确认清零=confirmed∨disabled 双路径；PATCH 审核语义变更把 confirmed 重置 pending 并 bump 父版本。
- resolve 原子应用 affectedRequirementChanges（仅限本冲突关联且同版本）；同 key 重放不重复变更。
无 AI 提取端点。"""

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from app.core.eligibility import applicable
from app.core.errors import ApiError

ACTOR = "local-reviewer"
EDITABLE_STATES = ("draft", "pending_confirmation")
PARAGRAPH_ID_RE = re.compile(r"p[1-9][0-9]*")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fp(*parts: str) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()


def replay_or_none(db, scope: str, key: str, fingerprint: str):
    prior = db.execute("SELECT request_hash,response_json FROM idempotency_records WHERE scope=? AND key=?",
                       (scope, key)).fetchone()
    if prior is None:
        return None
    if prior["request_hash"] != fingerprint:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
    return json.loads(prior["response_json"])


def record(db, scope: str, key: str, fingerprint: str, response, stamp: str) -> None:
    db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)",
               (scope, key, fingerprint, json.dumps(response, ensure_ascii=False), stamp))


def get_version(db, version_id: str, mode: str = "demo"):
    """归属+模式先行：非当前模式的项目/ Brief /版本一律 404（不外泄存在性）。"""
    row = db.execute("SELECT r.*, b.project_id AS brief_project_id, b.platforms_json AS brief_platforms, "
                     "p.mode AS project_mode, p.status AS project_status "
                     "FROM rule_versions r JOIN briefs b ON b.id=r.brief_id "
                     "JOIN projects p ON p.id=b.project_id WHERE r.id=?", (version_id,)).fetchone()
    if row is None or row["project_mode"] != mode:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "规则版本不存在")
    return row


def get_editable_version(db, version_id: str, mode: str = "demo"):
    return require_editable(get_version(db, version_id, mode))


def require_editable(version):
    """可写状态检查（顺序：归属/模式 → 幂等回放 → 状态/revision）。"""
    if version["state"] not in EDITABLE_STATES:
        raise ApiError(409, "VERSION_IMMUTABLE", f"版本状态为 {version['state']}，不可写（编辑请派生新草稿）")
    return version


def check_if_match(row, if_match: str | None) -> None:
    if if_match is None:
        raise ApiError(428, "PRECONDITION_REQUIRED", "必须携带 If-Match revision")
    try:
        if int(if_match) != row["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "版本已被更新，请读取新版本")
    except ValueError:
        raise ApiError(400, "VALIDATION_ERROR", "If-Match 必须是整数 revision", fields={"If-Match": if_match})


def bump(db, version_id: str) -> None:
    db.execute("UPDATE rule_versions SET revision=revision+1 WHERE id=?", (version_id,))


def _norm(text: str) -> str:
    return "".join(text.split())


def _canonical_json(raw: str) -> str:
    """规范化语义比较（sort_keys），避免键顺序不同被误判 STALE。"""
    return json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False)


def validate_source(db, source_row, source_json: dict) -> None:
    """来源二选一且互斥：
    文档来源=fileId(=该版本源文件)+paragraphId(实存)+quote(必须为该块规范化子串)；
    品牌补充=manualNote 非空且**不得**携带文档坐标字段。"""
    if not source_json:
        raise ApiError(400, "VALIDATION_ERROR", "规则必须提供来源", fields={"source": "缺失"})
    keys = set(source_json)
    coords = keys & {"fileId", "page", "paragraphId", "sheet", "cell"}
    note = source_json.get("manualNote")
    has_note = isinstance(note, str) and bool(note.strip())
    if has_note:
        if coords:
            raise ApiError(400, "VALIDATION_ERROR", "品牌补充不得携带文档坐标（防伪造来源）",
                           fields={"source": f"manualNote 与 {sorted(coords)} 互斥"})
        return
    file_id, paragraph_id = source_json.get("fileId"), source_json.get("paragraphId")
    quote = source_json.get("quote")
    if not (file_id and paragraph_id and isinstance(quote, str) and quote.strip()):
        raise ApiError(400, "VALIDATION_ERROR", "来源需 fileId+paragraphId+quote 非空，或 manualNote 品牌补充",
                       fields={"source": "不完整"})
    if not isinstance(paragraph_id, str) or not PARAGRAPH_ID_RE.fullmatch(paragraph_id):
        raise ApiError(400, "VALIDATION_ERROR", "paragraphId 必须为 p<正整数> 形式",
                       fields={"source.paragraphId": str(paragraph_id)[:40]})
    if file_id != source_row["file_id"]:
        raise ApiError(400, "VALIDATION_ERROR", "来源文件不是该版本绑定的源文件", fields={"source.fileId": file_id})
    block = db.execute("SELECT text, page_no FROM brief_text_blocks WHERE source_version_id=? AND ordinal=?",
                       (source_row["id"], int(paragraph_id[1:]) - 1)).fetchone()
    if block is None:
        raise ApiError(400, "VALIDATION_ERROR", "paragraphId 不存在于该源文本",
                       fields={"source.paragraphId": paragraph_id})
    if _norm(quote) not in _norm(block["text"]):
        raise ApiError(400, "VALIDATION_ERROR", "quote 必须为对应段落原文引用",
                       fields={"source.quote": "与所指段落内容不符"})
    # t-40：来源若声称了页码，必须与该段落**真实所属页**一致——防止 PDF 证据错页。
    claimed_page = source_json.get("page")
    if claimed_page is not None:
        if not isinstance(claimed_page, int) or isinstance(claimed_page, bool) or claimed_page < 1:
            raise ApiError(400, "VALIDATION_ERROR", "page 必须为 1 基正整数",
                           fields={"source.page": str(claimed_page)[:20]})
        actual_page = block["page_no"]
        if actual_page is None:
            raise ApiError(400, "VALIDATION_ERROR", "该来源无页概念（非分页文档），不得声称页码",
                           fields={"source.page": str(claimed_page)})
        if claimed_page != actual_page:
            raise ApiError(400, "VALIDATION_ERROR", "声称页码与该段落真实所在页不一致",
                           fields={"source.page": f"声称 {claimed_page}，实际 {actual_page}"},
                           details={"actualPage": actual_page})


def platforms_gate(db, brief_row, platforms: list[str]) -> None:
    brief_platforms = set(json.loads(brief_row["platforms_json"] or "[]"))
    if not brief_platforms:
        raise ApiError(409, "BRIEF_PLATFORMS_UNSET", "该 Brief 未记录平台范围（旧登记待补），须重新登记后操作")
    unknown = [p for p in platforms if p not in brief_platforms]
    if unknown:
        raise ApiError(400, "VALIDATION_ERROR", "规则平台不得越出 Brief/项目范围",
                       fields={"platforms": f"越界: {', '.join(unknown)}"},
                       details={"briefPlatforms": sorted(brief_platforms)})


def copy_draft_from(db, parent, new_version_id: str) -> int:
    """派生草稿真实复制：requirements 新 id 保留 lineage_id；open 冲突新 id+requirement_ids 映射。
    旧启用版本零写入。返回复制行数。"""
    mapping: dict[str, str] = {}
    rows = db.execute("SELECT * FROM requirements WHERE rule_version_id=? ORDER BY rowid",
                      (parent["id"],)).fetchall()
    for r in rows:
        new_id = uuid4().hex
        mapping[r["id"]] = new_id
        db.execute("INSERT INTO requirements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (new_id, new_version_id, r["lineage_id"], r["text"], r["category"], r["importance"],
                    r["platforms_json"], r["content_types_json"], r["phases_json"], r["review_stages_json"],
                    r["exceptions"], r["source_json"], r["confirmation"]))
    conflicts = db.execute("SELECT * FROM rule_conflicts WHERE rule_version_id=? AND state='open'",
                           (parent["id"],)).fetchall()
    for c in conflicts:
        ids = [mapping.get(x, x) for x in json.loads(c["requirement_ids_json"])]
        db.execute("INSERT INTO rule_conflicts VALUES (?,?,?,?,?,?,?)",
                   (uuid4().hex, new_version_id, json.dumps(ids), c["explanation"], "open", None, None))
    return len(rows)


def enable_gate(db, version_id: str, brief_row, source_row) -> None:
    """完整门禁（A 00:10 收口 3）：
    源 ready ∧ pending 清零 ∧ 冲突清零 ∧ 每条 uncovered 都有签署**且其关联补录项属本版本且 confirmed**
    ∧ ≥1 条已确认必须项且适用平台含任一项目相关（内容形式 video 与阶段判定与 bindable 共用 applicable）。"""
    if source_row["extraction_status"] != "ready":
        raise ApiError(409, "PARSE_NOT_READY", "源文件解析未就绪，不能启用",
                       details={"extractionStatus": source_row["extraction_status"]})
    pending = db.execute("SELECT COUNT(*) FROM requirements WHERE rule_version_id=? AND confirmation='pending'",
                         (version_id,)).fetchone()[0]
    if pending:
        raise ApiError(409, "RULES_NOT_ENABLED", f"仍有 {pending} 条待确认要求，须确认或明确停用")
    conflicts = db.execute("SELECT COUNT(*) FROM rule_conflicts WHERE rule_version_id=? AND state='open'",
                           (version_id,)).fetchone()[0]
    if conflicts:
        raise ApiError(409, "RULES_NOT_ENABLED", f"仍有 {conflicts} 组未解决冲突")
    uncovered = json.loads(source_row["uncovered_sources_json"] or "[]")
    if uncovered:
        # 逐 item 检查（B_0006）：存在链 ∧ 要求属本版本 ∧ 仍 confirmed
        # ∧ note 非空 ∧ 补录声明未改（source_json 规范化语义比较，避免键序误判 STALE）。
        # 历史 B_0005 关联不构成逐 item 有效覆盖（不自动迁移）。
        rows = db.execute("SELECT l.uncovered_item, l.requirement_id, l.note, l.source_snapshot_json, "
                          "r.confirmation, r.rule_version_id, r.source_json "
                          "FROM coverage_item_links l JOIN requirements r ON r.id=l.requirement_id "
                          "WHERE l.confirmation_id IN (SELECT id FROM coverage_confirmations WHERE rule_version_id=?)",
                          (version_id,)).fetchall()
        by_item: dict[str, list] = {}
        for row in rows:
            by_item.setdefault(row["uncovered_item"], []).append(row)
        missing: list[str] = []
        stale: list[str] = []
        for item in uncovered:
            links = by_item.get(item, [])
            satisfied = False
            for row in links:
                if row["rule_version_id"] != version_id or row["confirmation"] != "confirmed":
                    continue
                if not (row["note"] or "").strip():
                    continue
                try:
                    snapshot_changed = _canonical_json(row["source_snapshot_json"]) != _canonical_json(row["source_json"])
                except (TypeError, ValueError):
                    snapshot_changed = True
                if snapshot_changed:
                    continue
                satisfied = True
                break
            if satisfied:
                continue
            # 有链但全部失效=STALE；从无逐 item 链=MISSING（历史 B_0005 签名属后者）。
            (stale if links else missing).append(item)
        if missing:
            raise ApiError(409, "COVERAGE_UNCONFIRMED",
                           "存在缺少逐 item 人工补录链的未覆盖来源（仅签名或挂任意已确认项不解除）",
                           details={"missing": sorted(set(missing))})
        if stale:
            raise ApiError(409, "COVERAGE_STALE", "补录链关联要求已非已确认、停用或补录声明变更",
                           details={"stale": sorted(set(stale))})
    musts = db.execute("SELECT platforms_json,phases_json,content_types_json FROM requirements "
                       "WHERE rule_version_id=? AND importance='must' AND confirmation='confirmed'",
                       (version_id,)).fetchall()
    if not musts:
        raise ApiError(409, "RULES_NOT_ENABLED", "版本没有任何已确认必须项，不能启用")
    # 与 bindable_version 共用 applicable：至少存在 (平台×阶段) 组合使某条已确认必须项适用（内容形式 video）
    brief_platforms = json.loads(brief_row["platforms_json"] or "[]")
    for platform in brief_platforms:
        for phase in ("warmup", "seeding", "general"):
            for m in musts:
                requirement = {"platforms": json.loads(m["platforms_json"]), "phases": json.loads(m["phases_json"]),
                               "content_types": json.loads(m["content_types_json"])}
                if applicable(requirement, platform, phase, "video"):
                    return
    raise ApiError(409, "NO_APPLICABLE_MUST", "没有适用 Brief 平台×阶段组合的已确认必须项（内容形式含 video/general）")
