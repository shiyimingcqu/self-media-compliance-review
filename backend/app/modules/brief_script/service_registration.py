"""Brief 登记（t-17 首批）：fileId 注册 + TXT 同步可验证解析 + 段落来源映射落库。
诚实边界：
- FileAsset 当前没有项目归属字段：绑定关系只能从 brief_source_versions 引用反推，
  因此区分「未分配文件」（无引用）与「已绑定本项目/他项目」（有引用），不声称三查全部现成。
- PDF/DOC/DOCX 解析未实现：注册直接明确拒绝（501），不入队、不假解析、不建可用文本假象。
- 无 AI 规则提取；本切片只保证 Brief 实际登记与文本来源可验证。"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.errors import ApiError
from app.modules.brief_script.parsing.txt import ParseFailure, parse_txt
from app.modules.foundation import files_service

CREATE_SCOPE = "local-reviewer:demo:POST/briefs"
SUPPORTED_REGISTER = {"txt", "pdf"}  # t-40：PDF 走 pypdf 本地文本层 + 物理页号


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_payload(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    if size > limit:
        raise ApiError(413, "FILE_TOO_LARGE", "文件超出应用限制",
                       details={"limitBytes": limit, "source": "application-policy"})
    return path.read_bytes()


def create_brief(db, settings, project_id: str, payload, idempotency_key: str, *, start_transaction=True,allow_image_only_pdf=False) -> dict:
    """同事务幂等短事务模式（复用 create_project/tasks 风格，不新建作业队列）。
    平台集合规范化（去重+排序）后进入幂等指纹：同 key 换集合→409，仅顺序不同视为同一 payload。"""
    platforms_norm = sorted({p.value for p in payload.platforms})
    serialized = json.dumps({"projectId": project_id, "fileId": payload.file_id,
                             "title": payload.title.strip(), "platforms": platforms_norm}, sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    scope = f"{CREATE_SCOPE}:{project_id}"
    if start_transaction: db.execute("BEGIN IMMEDIATE")
    project = db.execute("SELECT id,status,mode,platforms_json FROM projects WHERE id=? AND mode='demo'",
                         (project_id,)).fetchone()
    if project is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "项目不存在或不属于当前模式")
    prior = db.execute("SELECT request_hash,response_json FROM idempotency_records WHERE scope=? AND key=?",
                       (scope, idempotency_key)).fetchone()
    if prior:
        if prior["request_hash"] != fingerprint:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容（文件/标题/平台集合）")
        return json.loads(prior["response_json"])
    if project["status"] == "archived":
        raise ApiError(409, "PROJECT_ARCHIVED", "归档项目不能登记 Brief")

    # 项目平台子集核查：不匹配返回字段级 400（未知/跨项目平台一并覆盖）
    project_platforms = set(json.loads(project["platforms_json"]))
    unknown = [p for p in platforms_norm if p not in project_platforms]
    if unknown:
        raise ApiError(400, "VALIDATION_ERROR", "平台必须是项目已选平台的子集",
                       fields={"platforms": f"不在项目范围: {', '.join(unknown)}"},
                       details={"projectPlatforms": sorted(project_platforms)})

    asset, file_path = files_service.get_asset(settings, payload.file_id)  # 404 已含模式过滤
    if asset.purpose != "brief":
        raise ApiError(400, "VALIDATION_ERROR", "文件用途不是 brief", fields={"fileId": asset.purpose})
    binding = db.execute(
        "SELECT b.project_id FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id WHERE s.file_id=?",
        (asset.id,)).fetchone()
    if binding:
        where = "本项目" if binding["project_id"] == project_id else "其他项目"
        raise ApiError(409, "FILE_ALREADY_REGISTERED", f"该文件已登记为{where}的 Brief 来源版本",
                       details={"boundToThisProject": binding["project_id"] == project_id})
    if asset.extension not in SUPPORTED_REGISTER:
        raise ApiError(501, "MODULE_NOT_IMPLEMENTED",
                       f"{asset.extension} 解析尚未接入；当前仅支持 TXT 登记",
                       details={"module": "brief-script.parse", "extension": asset.extension})

    brief_id, source_id, stamp = uuid4().hex, uuid4().hex, now()
    parse_error: ParseFailure | None = None
    parsed = None
    pdf_parsed = None
    payload_bytes = _read_payload(file_path, settings.max_document_bytes)
    if asset.extension == "pdf":
        # t-40：真实物理页号 + 页号级未覆盖（文本层可读 ≠ 图片规则已覆盖）
        from app.modules.brief_script.parsing.pdf import PdfFormatError, parse_pdf
        try:
            pdf_parsed = parse_pdf(payload_bytes,allow_image_only=allow_image_only_pdf)
        except PdfFormatError as fail:
            parse_error = ParseFailure(fail.code, fail.message)
    else:
        try:
            parsed = parse_txt(payload_bytes)
        except ParseFailure as fail:
            parse_error = fail  # 空白/乱码=明确失败：invalid 状态+错误如实落库，不产生可用文本

    page_count = pdf_parsed.page_count if pdf_parsed is not None else None
    uncovered = list(pdf_parsed.uncovered_sources) if pdf_parsed is not None else []

    db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at,platforms_json) "
               "VALUES (?,?,?,?,?,?)",
               (brief_id, project_id, payload.title.strip(), source_id, stamp, json.dumps(platforms_norm)))
    # page_count 只对分页文档（PDF）有意义；TXT 段落数≠页数，如实 NULL，段落数由 brief_text_blocks 行计。
    # uncovered_sources 落**页号级**未覆盖清单，供预览与既有覆盖签署 gate 如实阻断。
    db.execute("INSERT INTO brief_source_versions VALUES (?,?,?,?,?,?,?)",
               (source_id, brief_id, asset.id, "invalid" if parse_error else "ready",
                page_count, json.dumps(uncovered), stamp))

    blocks: list[dict] = []
    if parsed is not None:
        for block in parsed.blocks:
            db.execute("INSERT INTO brief_text_blocks VALUES (?,?,?,?,?,?,?,?)",
                       (uuid4().hex, source_id, block.ordinal, block.start_offset, block.end_offset,
                        block.text, stamp, None))
            blocks.append({"paragraphId": block.paragraph_id, "ordinal": block.ordinal,
                           "startOffset": block.start_offset, "endOffset": block.end_offset,
                           "text": block.text, "pageNo": None})
    elif pdf_parsed is not None:
        for block in pdf_parsed.blocks:
            # PDF 无字符偏移概念：offset 置 0 并由 pageNo 定位（消费方不得据 offset 假装精确定位）
            db.execute("INSERT INTO brief_text_blocks VALUES (?,?,?,?,?,?,?,?)",
                       (uuid4().hex, source_id, block.ordinal, 0, 0, block.text, stamp, block.page_no))
            blocks.append({"paragraphId": f"p{block.ordinal + 1}", "ordinal": block.ordinal,
                           "startOffset": 0, "endOffset": 0, "text": block.text, "pageNo": block.page_no})

    result = {"briefId": brief_id, "sourceVersionId": source_id, "fileId": asset.id,
              "platforms": platforms_norm,
              "extractionStatus": "invalid" if parse_error else "ready", "blocks": blocks,
              "pageCount": page_count, "uncoveredSources": uncovered}
    if parsed is not None:
        result["encoding"] = parsed.encoding
    if pdf_parsed is not None and pdf_parsed.warnings:
        result["warnings"] = pdf_parsed.warnings
    if parse_error is not None:
        result["error"] = {"code": parse_error.code, "message": parse_error.message}
    db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)",
               (scope, idempotency_key, fingerprint, json.dumps(result, ensure_ascii=False), stamp))
    db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
               (uuid4().hex, "local-reviewer", "brief", brief_id,
                "registered_invalid" if parse_error else "registered", "demo",
                json.dumps({"fileId": asset.id, "blockCount": len(blocks)}), stamp))
    return result
