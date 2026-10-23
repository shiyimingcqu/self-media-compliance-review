import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.contracts import ErrorDetail, ErrorEnvelope

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, fields: dict[str, str] | None = None,
                 details: dict[str, Any] | None = None):
        self.status, self.code, self.message = status, code, message
        self.fields, self.details = fields, details


def error_response(request: Request, status: int, code: str, message: str,
                   fields: dict[str, str] | None = None, details: dict[str, Any] | None = None) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorDetail(code=code, message=message, field_errors=fields, details=details),
                         request_id=getattr(request.state, "request_id", "unknown"))
    return JSONResponse(status_code=status, content=body.model_dump(by_alias=True, mode="json"))


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(request, exc.status, exc.code, exc.message, exc.fields, exc.details)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = {".".join(str(p) for p in e["loc"][1:]): e["msg"] for e in exc.errors()}
    return error_response(request, 400, "VALIDATION_ERROR", "请检查输入内容", fields)


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(request, exc.status_code, "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                          "资源不存在" if exc.status_code == 404 else str(exc.detail))


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("request failed: %s", getattr(request.state, "request_id", "unknown"))
    return error_response(request, 500, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试")


def not_implemented(module: str) -> None:
    raise ApiError(501, "MODULE_NOT_IMPLEMENTED", "此模块尚未接入，骨架不生成审核结果", details={"module": module})
