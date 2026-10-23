"""受控文件服务：仅本机 demo 模式；跨模式不可读；原件不可覆盖。无认证/项目授权，不宣称安全上线能力。
幂等 pending 带 owner token + 有界租约；finalize/renew/release 全按 token 条件匹配（fencing），
旧 owner 无法覆盖新 owner；丢租停止写入、清理本方件、不发布。"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from app.core.config import Settings
from app.core.database import connection
from app.core.errors import ApiError
from app.core.files import (MIME_BY_EXTENSION, PURPOSE_EXTENSIONS, StreamCounter, check_signature,
                            extension_of, parse_range, sanitize_original_name)
from app.core.idempotency import DEFAULT_LEASE_SECONDS, LostClaim, claim, finalize, parse_token, release, renew, utc_now
from app.modules.foundation.files_schemas import FileAsset, FilePurpose, PreviewBlock, PreviewResult

CHUNK = 1024 * 1024
FILE_SCOPE = "local-reviewer:demo:POST/files"
logger = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def purpose_limit(settings: Settings, purpose: str) -> int | None:
    return settings.max_video_bytes if purpose == "video" else settings.max_document_bytes


def _reject_if_mismatch(state: str, extension: str) -> None:
    if state == "mismatch":
        raise ApiError(415, "UNSUPPORTED_MEDIA", "文件头与扩展名不一致，已拒绝", details={"extension": extension})


def _fingerprint(sha256_hex: str, purpose: str, original: str) -> str:
    """payload 指纹=字节 sha256 + purpose + 规范化（strip）文件名（A 审定）。"""
    payload = json.dumps({"sha256": sha256_hex, "purpose": purpose, "name": original}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _checked_name(upload) -> str:
    return sanitize_original_name((upload.filename or "").strip())


async def _guarded_close(upload) -> None:
    """close 失败不阻断清理，但如实记录（不泄露 storage 路径），不声称总能保留原始异常。"""
    try:
        await upload.close()
    except BaseException as close_exc:
        logger.warning("upload close failed during cleanup: %s", type(close_exc).__name__)


def _cleanup_uncommitted(settings: Settings, scope_key: str, token: str, counter, stored) -> None:
    """未提交状态下尽力执行全部剩余清理：本方 .part/最终件 → owner 条件 release。
    每步独立 try：某步失败仍尝试其余步骤并记录安全错误（异常类型名，不写路径/密钥）。"""
    try:
        if counter is not None:
            counter.abort()  # .part 若仍在（finish 前失败）清掉；rename 后 missing_ok 无害
        if stored is not None:
            Path(stored.storage_key).unlink(missing_ok=True)  # 未提交的最终件
    except BaseException as cleanup_exc:
        logger.error("file cleanup failed (non-fatal): %s", type(cleanup_exc).__name__)
    try:
        release(settings.database_path, FILE_SCOPE, scope_key, token)
    except BaseException as release_exc:
        logger.error("idempotency release failed; lease fencing will reject late finalize: %s", type(release_exc).__name__)


async def _consume(upload, purpose: FilePurpose, settings: Settings, file_id: str, checkpoint=None):
    """流式分块：读一块、计数一块、写一块；白名单/签名/空文件/限额校验。
    任何异常（含取消/丢租）在 finally 清理本次 .part 后原样重抛；成功返回时不动任何文件。"""
    original = _checked_name(upload)
    extension = extension_of(original)
    if extension not in PURPOSE_EXTENSIONS[purpose]:
        raise ApiError(415, "UNSUPPORTED_MEDIA", "文件扩展名与用途不符或不支持",
                       details={"purpose": purpose, "extension": extension or "(none)"})
    limit = purpose_limit(settings, purpose)
    counter = StreamCounter(settings.uploads_dir, file_id, extension, limit)
    try:
        checked = False
        state = "pending"
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            counter.feed(chunk)
            if checkpoint is not None:
                checkpoint()  # 续租检查点：LostClaim 从此抛出，走统一清理路径
            if not checked and len(counter.head) >= 16:
                # 首块凑满 16 字节即校验签名，假扩展名不必等全文件落盘。
                state = check_signature(extension, counter.head)
                _reject_if_mismatch(state, extension)
                checked = True
        if counter.byte_size == 0:
            # 空文件统一 400，先于尾块签名判定（A 审定口径）。
            raise ApiError(400, "VALIDATION_ERROR", "空文件不可作为审核输入", fields={"file": "文件长度为 0"})
        if not checked:
            state = check_signature(extension, counter.head)
            _reject_if_mismatch(state, extension)
        result = (original, extension, state, counter)
        return result
    except BaseException as exc:
        counter.abort()  # ApiError/OverflowError/取消/丢租统一清理本次 .part
        if isinstance(exc, OverflowError):
            raise ApiError(413, "FILE_TOO_LARGE", "文件超出应用限制",
                           details={"limitBytes": limit, "source": "application-policy"}) from None
        raise


async def store_upload(upload, purpose: FilePurpose, settings: Settings, idempotency_key: str, *,
                       clock: Callable[[], datetime] = utc_now,
                       lease_seconds: int = DEFAULT_LEASE_SECONDS) -> FileAsset:
    """幂等 + fencing（按 A 21:50 批准口径）：
    - claim 原子占位（owner=uuid4 token + 有界 lease）；未过期 in_progress 拒并发；过期 takeover 换代原子；
    - _consume 每块检查点：租约过半即 renew，renew 失败（LostClaim）停止写入、不发布；
    - finalize 与 release 都必须逐字匹配当前 token；旧 token 一律无效；
    - completed 记录只被同内容回放读取，绝不被 release 误删。"""
    if purpose not in PURPOSE_EXTENSIONS:
        raise ApiError(400, "VALIDATION_ERROR", "purpose 仅支持 brief/script/video", fields={"purpose": purpose})
    verdict = claim(settings.database_path, FILE_SCOPE, idempotency_key, lease_seconds=lease_seconds, clock=clock)
    if verdict.status == "in_progress":
        raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "相同幂等键的请求正在处理中", details={"retryable": True})

    if verdict.status == "completed":
        # 回放：消费请求体只做指纹探测；一致返回原 data 快照，不一致 409。
        digest = hashlib.sha256()
        total = 0
        limit = purpose_limit(settings, purpose)
        try:
            while True:
                chunk = await upload.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if limit is not None and total > limit:
                    raise ApiError(413, "FILE_TOO_LARGE", "文件超出应用限制",
                                   details={"limitBytes": limit, "source": "application-policy"})
                digest.update(chunk)
            fingerprint = _fingerprint(digest.hexdigest(), purpose, _checked_name(upload))
        finally:
            await upload.close()
        if verdict.request_hash != fingerprint:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同内容（字节/用途/文件名不一致）")
        return FileAsset.model_validate_json(verdict.response_json)

    # 我方持有 pending。
    lease = {"token": verdict.token, "lost": None}
    _, initial_expires = parse_token(verdict.token)
    deadline = {"ts": initial_expires}

    def checkpoint() -> None:
        if lease["lost"] is not None:
            raise lease["lost"]
        remaining = deadline["ts"] - int(clock().timestamp())
        if remaining <= lease_seconds // 2:
            try:
                new_token = renew(settings.database_path, FILE_SCOPE, idempotency_key, lease["token"],
                                  lease_seconds=lease_seconds, clock=clock)
            except LostClaim as exc:
                lease["lost"] = exc
                raise
            lease["token"] = new_token
            _, deadline["ts"] = parse_token(new_token)

    file_id = uuid4().hex
    counter = None
    stored = None
    committed = False
    try:
        original, extension, state, counter = await _consume(upload, purpose, settings, file_id, checkpoint)
        checkpoint()  # 落最终件前最后一次持租确认：丢租则不再 finish，无最终件
        stored = counter.finish(file_id)
        # finish 之后本方即拥有最终件：close/asset 构造/指纹/事务统一在同一保护范围内（A 22:11），
        # 未提交前任何异常（含取消与 close 失败）都清本方最终件并按 owner 条件 release。
        await upload.close()
        stamp = now()
        asset = FileAsset(id=file_id, original_name=original, extension=extension,
                          mime=MIME_BY_EXTENSION[extension], byte_size=stored.byte_size, sha256=stored.sha256,
                          purpose=purpose, signature_state=state, media_check="unverified",
                          mode="demo", created_at=stamp)
        fingerprint = _fingerprint(stored.sha256, purpose, original)
        with connection(settings.database_path) as db:
            db.execute("BEGIN IMMEDIATE")
            finalize(db, FILE_SCOPE, idempotency_key, lease["token"], fingerprint, asset.model_dump_json(by_alias=True))
            db.execute("INSERT INTO file_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (asset.id, asset.original_name, asset.extension, asset.mime, asset.byte_size, asset.sha256,
                        stored.storage_key, asset.purpose, asset.signature_state, asset.media_check, asset.mode, stamp))
            db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                       (uuid4().hex, "local-reviewer", "file_asset", asset.id, "uploaded", "demo",
                        json.dumps({"purpose": purpose, "byteSize": asset.byte_size}), stamp))
        # with 正常退出=COMMIT 成功；此后再无任何可抛异常的清理动作，committed 之后绝不误删已提交原件。
        committed = True
    except BaseException:
        if not committed:
            try:
                await _guarded_close(upload)
            finally:
                _cleanup_uncommitted(settings, idempotency_key, lease["token"], counter, stored)
        else:  # pragma: no cover - 提交后当前无 await 点，防御性兜底
            await _guarded_close(upload)
        raise
    return asset


def get_asset(settings: Settings, file_id: str) -> tuple[FileAsset, Path]:
    with connection(settings.database_path) as db:
        row = db.execute("SELECT * FROM file_assets WHERE id=? AND mode='demo'", (file_id,)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "文件不存在或不属于当前模式")
    path = Path(row["storage_key"])
    # 路径完整性检查：storage_key 必须落在受控 uploads 目录内（防数据库被手工改动后穿越）。
    try:
        resolved = path.resolve()
        uploads_root = settings.uploads_dir.resolve()
    except OSError as exc:
        raise ApiError(500, "INTERNAL_ERROR", "存储服务不可用") from exc
    if uploads_root not in resolved.parents or resolved.name != f"{row['id']}.{row['extension']}":
        raise ApiError(500, "INTERNAL_ERROR", "存储对象校验失败")
    if not resolved.is_file():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "文件实体缺失")
    asset = FileAsset(id=row["id"], original_name=row["original_name"], extension=row["extension"],
                      mime=row["mime"], byte_size=row["byte_size"], sha256=row["sha256"], purpose=row["purpose"],
                      signature_state=row["signature_state"], media_check=row["media_check"], mode=row["mode"],
                      created_at=row["created_at"])
    return asset, resolved


def range_for(header: str | None, total: int) -> tuple[int, int] | None:
    parsed = parse_range(header, total)
    if parsed == "unsatisfiable":
        raise ApiError(416, "RANGE_UNSATISFIABLE", "Range 超出文件长度", details={"totalBytes": total})
    return parsed


SOURCE_JOIN = ("SELECT s.id, s.brief_id, s.file_id, s.extraction_status, s.page_count, "
               "s.uncovered_sources_json, b.project_id, p.mode AS project_mode "
               "FROM brief_source_versions s JOIN briefs b ON b.id=s.brief_id "
               "JOIN projects p ON p.id=b.project_id")


def get_preview(settings: Settings, file_id: str, source_version_id: str | None = None):
    """只读持久来源投影（t-30）：不重解析、不造 ready、不泄露路径/storageKey。
    - 传 sourceVersionId：必须同时命中该源的 file_id 与 Brief/项目当前模式，否则 404（不外泄存在性）；
    - 省略：仅当该文件确有唯一已登记源时可用（file_id 上有唯一索引保证），绝不任取 latest 或跨 Brief；
    - 无已登记源/实际非 ready：409 PREVIEW_NOT_READY 并透出真实状态，不伪 uploaded/ready；
    - ready 但无段落行：数据不一致 → 500（不返回空数组冒充成功）。"""
    with connection(settings.database_path) as db:
        asset = db.execute("SELECT id, purpose, extension FROM file_assets WHERE id=? AND mode='demo'",
                           (file_id,)).fetchone()
        if asset is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "文件不存在或不属于当前模式")
        if asset["purpose"] != "brief":
            raise ApiError(400, "VALIDATION_ERROR", "该文件不是 Brief 来源", fields={"fileId": asset["purpose"]})
        if source_version_id:
            row = db.execute(SOURCE_JOIN + " WHERE s.id=?", (source_version_id,)).fetchone()
            if row is None or row["file_id"] != file_id or row["project_mode"] != "demo":
                raise ApiError(404, "RESOURCE_NOT_FOUND", "来源版本不存在或与该文件/模式不匹配")
        else:
            rows = db.execute(SOURCE_JOIN + " WHERE s.file_id=? AND p.mode='demo'", (file_id,)).fetchall()
            if not rows:
                raise ApiError(409, "PREVIEW_NOT_READY", "该文件没有当前模式下的已登记来源",
                               details={"fileId": file_id})
            row = rows[0]  # file_id 唯一索引：不存在"多源任取"空间
        if row["extraction_status"] != "ready":
            raise ApiError(409, "PREVIEW_NOT_READY",
                           f"来源解析未就绪（实际状态 {row['extraction_status']}），不提供伪预览",
                           details={"extractionStatus": row["extraction_status"], "sourceVersionId": row["id"]})
        blocks = db.execute(
            "SELECT ordinal, start_offset, end_offset, text, page_no FROM brief_text_blocks "
            "WHERE source_version_id=? ORDER BY ordinal", (row["id"],)).fetchall()
        if not blocks:
            raise ApiError(500, "INTERNAL_ERROR", "来源标记就绪但无持久段落（数据不一致）")
        # t-40：分页元数据取**该 source 自己的**持久行（历史各源独立，禁 latest 冒用）。
        try:
            uncovered = json.loads(row["uncovered_sources_json"] or "[]")
        except (TypeError, ValueError):
            uncovered = []
        return PreviewResult(source_version_id=row["id"], file_id=file_id, extraction_status="ready",
                             blocks=[PreviewBlock(paragraph_id=f"p{b['ordinal'] + 1}", ordinal=b["ordinal"],
                                                   start_offset=b["start_offset"], end_offset=b["end_offset"],
                                                   text=b["text"], page_no=b["page_no"]) for b in blocks],
                             page_count=row["page_count"], uncovered_sources=uncovered)
