"""受控文件 API（2.3 第一增量）：仅 POST /files 与 GET /files/:id/content（HEAD 随 GET 语义）。
签名/大小校验≠完整解码或文档可解析性；mediaCheck 固定 unverified，解析归 B 域、媒体探测归 C。"""

from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.core.contracts import Envelope
from app.core.errors import ApiError
from app.modules.foundation import files_service
from app.modules.foundation.files_schemas import FileAsset, PreviewResult
from app.modules.foundation.files_service import range_for

router = APIRouter(tags=["A · foundation(files)"])

VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-msvideo"}


@router.post("/files", response_model=Envelope[FileAsset], status_code=201)
async def upload_file(request: Request, file: UploadFile = File(...), purpose: str = Form(...),
                      idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    if purpose not in ("brief", "script", "video"):
        raise ApiError(400, "VALIDATION_ERROR", "purpose 仅支持 brief/script/video", fields={"purpose": purpose})
    if not idempotency_key.strip():
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency-Key 不能为空白", fields={"Idempotency-Key": "空白"})
    settings = request.app.state.settings
    limit = files_service.purpose_limit(settings, purpose)
    if limit is not None and file.size is not None and file.size > limit:
        raise ApiError(413, "FILE_TOO_LARGE", "文件超出应用限制",
                       details={"limitBytes": limit, "source": "application-policy"})
    asset = await files_service.store_upload(file, purpose, settings, idempotency_key)
    # 回放同样返回 201 与原业务 data 快照；requestId 必须是当前请求的追踪 ID，不复制旧值。
    return Envelope(data=asset, request_id=request.state.request_id)


@router.get("/files/{file_id}/content")
@router.head("/files/{file_id}/content")
def file_content(request: Request, file_id: str, range: str | None = Query(default=None, alias="Range")):
    settings = request.app.state.settings
    asset, path = files_service.get_asset(settings, file_id)
    total = asset.byte_size
    disposition = "inline" if asset.mime in VIDEO_MIMES else "attachment"
    headers_base = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"{disposition}; filename*=utf-8''{quote(asset.original_name)}",
        "X-Request-ID": request.state.request_id,
    }
    if request.scope["method"] == "HEAD":  # HEAD 遵循 GET 语义：同头无实体
        return Response(status_code=200, headers={**headers_base, "Content-Length": str(total)}, media_type=asset.mime)
    span = range_for(request.headers.get("range") or range, total)
    if span is None:
        handle = path.open("rb")
        return StreamingResponse(_chunks(handle), status_code=200, media_type=asset.mime,
                                 headers={**headers_base, "Content-Length": str(total)})
    start, end = span
    handle = path.open("rb")
    handle.seek(start)

    def _partial():
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
        handle.close()

    return StreamingResponse(_partial(), status_code=206, media_type=asset.mime, headers={
        **headers_base,
        "Content-Range": f"bytes {start}-{end}/{total}",
        "Content-Length": str(end - start + 1),
    })


@router.get("/files/{file_id}/preview", response_model=Envelope[PreviewResult])
def file_preview(request: Request, file_id: str,
                 source_version_id: str | None = Query(default=None, alias="sourceVersionId", max_length=64)):
    """t-30 只读持久来源投影：不重解析/不造 ready/不泄路径；详见 files_service.get_preview。"""
    settings = request.app.state.settings
    result = files_service.get_preview(settings, file_id, source_version_id)
    return Envelope(data=result, request_id=request.state.request_id)


def _chunks(handle):
    try:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()
