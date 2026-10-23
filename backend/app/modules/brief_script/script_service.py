"""脚本不可变版本登记（t-25 首增量，A 01:02/01:04 口径）：
- 请求本身非法（空/纯空白粘贴、超字符界、file/text 互斥错、purpose 错、跨项目文件、未支持格式）
  → 字段级 400 / 501，零版本、零 ordinal、零失效事件。
- 通过输入校验但解析失败的真实提交 → 建 invalid 不可变版本，消耗 ordinal、可审计，
  同样追加 new_script_version 事件并回退阶段（invalid 成为最新版本时不得保留旧通过资格）。
- 任何新建版本同事务追加失效事件 + 阶段回 script（重审起点；不保留 completed）。
  下游脚本/视频/人工消费失效未接线，如实明示；本批不接 script-jobs、不排无消费者 queued、无 AI 结果。
- 同 key 重放不递增不发事件；ordinal 由 UNIQUE(task_id,ordinal)+事务领取保证并发唯一。
- 粘贴保留原文（strip 仅用于判空），sourceMap 偏移一律指向所存原文，trim 规则前后一致。
"""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.core.errors import ApiError
from app.modules.brief_script.parsing.txt import ParseFailure, parse_txt
from app.core.database import connection

MAX_TEXT_CHARS = 50_000
MIN_TEXT_CHARS = 1
SUPPORTED_PARSE_EXTENSIONS = {"txt", "xlsx", "docx"}
# 旧 OLE .doc 需系统级转换器（LibreOffice）才能解析；未安装前明确未支持，不自研 OLE 解析器。
UNVERIFIED_PARSER_EXTENSIONS = {"doc", "xls", "pdf"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fp(*parts: str) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()


def authorize_task(db, task_id: str, mode: str = "demo"):
    """GET/读路径只做模式归属（归档任务历史版本仍可见，不 409）。"""
    row = db.execute("SELECT t.*, p.mode AS project_mode, p.status AS project_status, "
                     "p.platforms_json AS project_platforms FROM creator_tasks t "
                     "JOIN projects p ON p.id=t.project_id WHERE t.id=?", (task_id,)).fetchone()
    if row is None or row["project_mode"] != mode:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "任务不存在")
    return row


def require_task_writable(task) -> None:
    """新写专属：归档项目/任务禁止创建，但不拦截幂等重放（由调用方顺序保证）。"""
    if task["project_status"] == "archived" or task["archived_at"] is not None:
        raise ApiError(409, "TASK_ARCHIVED", "归档项目或任务不能提交脚本版本")


def _next_ordinal(db, task_id: str) -> int:
    current = db.execute("SELECT MAX(ordinal) FROM script_versions WHERE task_id=?", (task_id,)).fetchone()[0]
    return int(current or 0) + 1


def _file_binding(db, file_id: str, task) -> None:
    """FileAsset 无归属字段：绑定从引用反推（brief_source_versions、script_versions→任务→项目）。
    策略（A 01:38 裁决）：
    - 任何 Brief 来源引用 → 拒（跨用途）；
    - 任一"其他项目"的脚本引用（含 real mode 项目）→ 拒（跨项目/跨 mode）；
      必须 EXISTS 命中任意他项目行即拒——不能 fetchone 先取到同项目行而漏他项目行；
    - 同项目多任务显式共享同一不可变 fileId → 允许，各自生成独立版本/来源/审计/失效事件；
      绝不按文件名自动配对（引用只认 fileId）。"""
    brief_ref = db.execute("SELECT b.project_id FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id "
                           "WHERE s.file_id=?", (file_id,)).fetchone()
    if brief_ref is not None:
        raise ApiError(409, "FILE_ALREADY_REGISTERED", "该文件已登记为 Brief 来源版本",
                       details={"boundProject": brief_ref["project_id"], "boundKind": "brief"})
    cross = db.execute("SELECT t.project_id FROM script_versions v JOIN creator_tasks t ON t.id=v.task_id "
                       "WHERE v.file_id=? AND t.project_id<>?", (file_id, task["project_id"])).fetchone()
    if cross is not None:
        raise ApiError(409, "FILE_ALREADY_REGISTERED", "该文件已登记为其他项目的脚本版本",
                       details={"boundProject": cross["project_id"], "boundKind": "script", "crossProject": True})


def _blocks_of(source_text: str) -> list[dict]:
    """偏移一律指向传入文本本体（text[start:end]==块文本可回验）。
    粘贴路径传入原文字符串（不经过字节编解码，BOM/CRLF 不产生偏移漂移）；
    文件路径传入严格解码后的文本（utf-8-sig/utf-16 的 BOM 被解码消费，不在文本内）。"""
    from app.modules.brief_script.parsing.txt import split_paragraphs
    return [{"paragraphId": b.paragraph_id, "ordinal": b.ordinal, "startOffset": b.start_offset,
             "endOffset": b.end_offset, "text": source_text[b.start_offset:b.end_offset]}
            for b in split_paragraphs(source_text)]


def _parse_payload(payload: bytes) -> tuple[str, list[dict]]:
    parsed = parse_txt(payload)
    return parsed.text, _blocks_of(parsed.text)


def _parse_real_format(payload: bytes, extension: str) -> tuple[list[dict], dict]:
    """XLSX/DOCX 真实解析 → (sourceMap 块, parseMeta)。块带结构化 location（非字符偏移）。
    parseMeta 持久化 warnings/uncoveredMedia/partial，供后续页面与审核不得误判"文本完整"。"""
    from app.modules.brief_script.parsing.doc import DocFormatError
    if extension == "xlsx":
        from app.modules.brief_script.parsing.xlsx import parse_xlsx
        parsed = parse_xlsx(payload)
    elif extension == "docx":
        from app.modules.brief_script.parsing.docx import parse_docx
        parsed = parse_docx(payload)
    else:  # 调用方已按白名单过滤，此处防御
        raise DocFormatError("MODULE_NOT_IMPLEMENTED", "该格式解析未接入")
    blocks = [{"paragraphId": b.paragraph_id, "ordinal": b.ordinal, "location": b.location, "text": b.text}
              for b in parsed.blocks]
    # 非图片类未覆盖（无缓存公式/超限截断）并入 warnings，避免改契约 shape
    warnings = list(parsed.warnings) + [f"未覆盖：{r}" for r in parsed.uncovered_other]
    meta = {"warnings": warnings, "uncoveredMedia": parsed.uncovered_media,
            "partial": parsed.partial, "coverage": parsed.coverage}
    return blocks, meta


def create_script_version(db, settings, task_id: str, payload, idempotency_key: str) -> dict:
    """payload: ScriptCreate（input_type/file_id/text）。
    事务内顺序（A 01:25③）：BEGIN IMMEDIATE → 模式归属(404) → 幂等回放(旧成功不受后续归档阻断)
    → 归档新写检查(409) → 形状/门禁校验 → 领取 ordinal。竞态下重读事务内当前任务状态。"""
    request_shape = {"taskId": task_id, "inputType": payload.input_type,
                     "fileId": payload.file_id, "text": payload.text}
    fingerprint = fp("script", json.dumps(request_shape, sort_keys=True, ensure_ascii=False))
    scope = f"local-reviewer:demo:POST/scripts:{task_id}"

    db.execute("BEGIN IMMEDIATE")
    task = authorize_task(db, task_id)  # 事务内读取，archive/rebind 竞态不读旧状态
    prior = db.execute("SELECT request_hash,response_json FROM idempotency_records WHERE scope=? AND key=?",
                       (scope, idempotency_key)).fetchone()
    if prior is not None:
        if prior["request_hash"] != fingerprint:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容")
        return json.loads(prior["response_json"])  # 重放：不递增不发事件，不查归档
    require_task_writable(task)

    # ---- 请求形状校验（失败=零版本零 ordinal 零事件）----
    if payload.input_type == "file":
        if payload.text is not None:
            raise ApiError(400, "VALIDATION_ERROR", "file 输入不得同时提供 text", fields={"text": "互斥"})
        if not payload.file_id:
            raise ApiError(400, "VALIDATION_ERROR", "file 输入必须提供 fileId", fields={"fileId": "缺失"})
        from app.modules.foundation import files_service
        try:
            asset, path = files_service.get_asset(settings, payload.file_id)
        except ApiError as exc:
            if exc.status == 404:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "文件不存在或不属于当前模式") from None
            raise
        if asset.purpose != "script":
            raise ApiError(400, "VALIDATION_ERROR", "文件用途不是 script", fields={"fileId": asset.purpose})
        _file_binding(db, asset.id, task)
        if asset.extension in UNVERIFIED_PARSER_EXTENSIONS:
            raise ApiError(501, "MODULE_NOT_IMPLEMENTED",
                           f"{asset.extension} 解析能力未验证，暂不能登记脚本版本；总范围仍含该格式",
                           details={"module": "brief-script.script-parse", "extension": asset.extension})
        if asset.extension not in SUPPORTED_PARSE_EXTENSIONS:
            raise ApiError(501, "MODULE_NOT_IMPLEMENTED", "该格式解析未接入",
                           details={"module": "brief-script.script-parse", "extension": asset.extension})
        raw_bytes = path.read_bytes()
    else:
        if payload.file_id is not None:
            raise ApiError(400, "VALIDATION_ERROR", "粘贴输入不得提供 fileId", fields={"fileId": "互斥"})
        if payload.text is None or not payload.text.strip():
            raise ApiError(400, "VALIDATION_ERROR", "粘贴内容不能为空白", fields={"text": "空白"})
        if len(payload.text) > MAX_TEXT_CHARS:
            raise ApiError(400, "VALIDATION_ERROR", f"粘贴内容超过 {MAX_TEXT_CHARS} 字上限",
                           fields={"text": f"{len(payload.text)} 字"})

    # ---- 真实提交：解析结果决定 ready/invalid；两者都建不可变版本并消耗 ordinal ----
    version_id, stamp = uuid4().hex, now()
    ordinal = _next_ordinal(db, task_id)
    extraction_status = "ready"
    source_map: list[dict] = []
    parse_meta: dict = {}  # XLSX/DOCX: {warnings, uncoveredMedia, partial}；TXT 保持空
    parse_error: str | None = None
    file_id_for_row = payload.file_id if payload.input_type == "file" else None

    if payload.input_type == "text":
        stored_text = payload.text  # 粘贴保留原文；分段直接在原文上做，偏移不漂移（A 01:25④）
        from app.modules.brief_script.parsing.txt import looks_garbled
        if looks_garbled(payload.text):
            extraction_status, parse_error, source_map = "invalid", "粘贴内容控制字符/替换符比例过高，按解析失败处理", []
        else:
            source_map = _blocks_of(payload.text)
            if not source_map:
                extraction_status, parse_error = "invalid", "未提取到任何段落，不视为解析成功"
    else:
        stored_text = None  # file 行不持久原文（体积/来源以 file_id+解析产物为准），charCount=None 不伪 0
        if asset.extension in ("xlsx", "docx"):
            # 真实格式：结构化定位 + 解析元数据（告警/未识读图片/partial）一并持久化
            from app.modules.brief_script.parsing.doc import DocFormatError
            try:
                source_map, parse_meta = _parse_real_format(raw_bytes, asset.extension)
            except DocFormatError as fail:
                extraction_status, parse_error = "invalid", fail.message
        else:
            try:
                decoded_text, blocks = _parse_payload(raw_bytes)
            except ParseFailure as fail:
                extraction_status, parse_error = "invalid", fail.message
            else:
                source_map = blocks

    db.execute("INSERT INTO script_versions (id,task_id,ordinal,version_label,input_type,file_id,text,"
               "extraction_status,source_map_json,parse_meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (version_id, task_id, ordinal, f"s{ordinal}", payload.input_type, file_id_for_row,
                stored_text, extraction_status, json.dumps(source_map, ensure_ascii=False),
                json.dumps(parse_meta, ensure_ascii=False), stamp))

    # 任何新建版本（含 invalid 成为最新版本）都追加失效事件并回退到脚本重审起点。
    db.execute("INSERT INTO task_invalidation_events VALUES (?,?,?,?,?,?,?)",
               (uuid4().hex, task_id, "new_script_version", "新脚本版本提交，下游旧资格不再当前有效",
                json.dumps({"scriptVersionId": version_id, "ordinal": ordinal,
                            "extractionStatus": extraction_status}), "demo", stamp))
    db.execute("UPDATE creator_tasks SET stage='script', revision=revision+1, updated_at=? WHERE id=?",
               (stamp, task_id))

    result = {"scriptVersionId": version_id, "taskId": task_id, "ordinal": ordinal,
              "versionLabel": f"s{ordinal}", "inputType": payload.input_type,
              "fileId": file_id_for_row, "extractionStatus": extraction_status,
              "sourceMap": source_map,
              "charCount": len(stored_text) if stored_text is not None else None,  # file 行无持久原文=None，不伪 0
              "parseMeta": parse_meta,
              "createdAt": stamp}
    if parse_error:
        result["error"] = {"code": "EXTRACTION_FAILED", "message": parse_error}

    db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)",
               (scope, idempotency_key, fingerprint, json.dumps(result, ensure_ascii=False), stamp))
    db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
               (uuid4().hex, "local-reviewer", "script_version", version_id,
                "submitted_ready" if extraction_status == "ready" else "submitted_invalid", "demo",
                json.dumps({"taskId": task_id, "ordinal": ordinal}), stamp))
    return result


def list_script_versions(db, task_id: str, mode: str = "demo") -> list[dict]:
    authorize_task(db, task_id, mode)  # 归档任务历史版本只读可见，不 409（A 01:25②）
    rows = db.execute("SELECT id,ordinal,version_label,input_type,file_id,extraction_status,created_at,"
                      "length(text) AS char_count FROM script_versions WHERE task_id=? ORDER BY ordinal DESC",
                      (task_id,)).fetchall()
    current = max((r["ordinal"] for r in rows), default=None)
    return [{"scriptVersionId": r["id"], "ordinal": r["ordinal"], "versionLabel": r["version_label"],
             "inputType": r["input_type"], "fileId": r["file_id"], "extractionStatus": r["extraction_status"],
             "charCount": r["char_count"], "createdAt": r["created_at"], "isCurrent": r["ordinal"] == current}
            for r in rows]


def script_version_detail(db, version_id: str, mode: str = "demo") -> dict:
    row = db.execute("SELECT v.*, p.mode AS project_mode FROM script_versions v "
                     "JOIN creator_tasks t ON t.id=v.task_id JOIN projects p ON p.id=t.project_id "
                     "WHERE v.id=?", (version_id,)).fetchone()
    if row is None or row["project_mode"] != mode:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "脚本版本不存在")
    top = db.execute("SELECT MAX(ordinal) FROM script_versions WHERE task_id=?", (row["task_id"],)).fetchone()[0]
    return {"scriptVersionId": row["id"], "taskId": row["task_id"], "ordinal": row["ordinal"],
            "versionLabel": row["version_label"], "inputType": row["input_type"], "fileId": row["file_id"],
            "text": row["text"], "extractionStatus": row["extraction_status"],
            "sourceMap": json.loads(row["source_map_json"]),
            # t-38：定位/告警/未识读图片必须来自持久数据，不能只在 created 响应里
            "parseMeta": json.loads(row["parse_meta_json"] or "{}"),
            "isCurrent": row["ordinal"] == top,
            "createdAt": row["created_at"]}


def with_connection(path):
    return connection(path)
