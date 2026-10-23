from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse

from app.core.contracts import Envelope
from app.core.errors import ApiError
from . import console_service as service
from .console_schemas import EvidenceRunInput, EvidenceRunView, ModelSettingsInput, ModelSettingsView, VideoPreparationInput, VideoPreparationView

router = APIRouter(tags=["C · evidence-console"])


@router.post('/video-preparations', response_model=Envelope[VideoPreparationView])
def prepare(request: Request, payload: VideoPreparationInput):
    local_only(request)
    from .preprocessing import prepare_video
    metadata, _ = prepare_video(request.app.state.settings, payload.file_id, payload.preset)
    return Envelope(data=VideoPreparationView.model_validate(metadata), request_id=request.state.request_id)


@router.get('/video-preparations/{file_id}/{preset}/content')
def prepared_content(request: Request, file_id: str, preset: str):
    local_only(request)
    from .preprocessing import prepare_video
    _, path = prepare_video(request.app.state.settings, file_id, preset)
    return FileResponse(path, media_type='video/mp4', headers={'Cache-Control': 'private, no-store'})


def local_only(request: Request):
    from urllib.parse import urlsplit
    # 本地单人配置入口；共享部署必须加入身份/授权，不开放任意远程凭证操作。
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise ApiError(403, "LOCAL_ONLY", "模型配置和真实调用测试仅允许本机使用")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ApiError(403, "ORIGIN_NOT_ALLOWED", "来源不允许")


@router.get("/settings/models", response_model=Envelope[ModelSettingsView])
def model_settings(request: Request):
    local_only(request)
    return Envelope(data=service.settings_view(request.app.state.settings), request_id=request.state.request_id)


@router.put("/settings/models", response_model=Envelope[ModelSettingsView])
def update_settings(request: Request, payload: ModelSettingsInput):
    local_only(request)
    return Envelope(data=service.save_settings(request.app.state.settings, payload), request_id=request.state.request_id)


@router.post("/evidence-runs", response_model=Envelope[EvidenceRunView], status_code=202)
def start_run(request: Request, payload: EvidenceRunInput,
              idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    local_only(request)
    if not idempotency_key.strip():
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency-Key 不能为空白")
    settings = request.app.state.settings
    identity, frozen = service.prepare_run(settings, payload, idempotency_key)
    if frozen is not None:
        request.app.state.evidence_executor.submit(service.execute_run, settings, identity, frozen)
    return Envelope(data=service.get_run(settings, identity), request_id=request.state.request_id)


@router.get("/evidence-runs", response_model=Envelope[list[EvidenceRunView]])
def runs(request: Request, taskId: str | None = None):
    local_only(request)
    return Envelope(data=service.list_runs(request.app.state.settings, taskId), request_id=request.state.request_id)


@router.get("/evidence-runs/{run_id}", response_model=Envelope[EvidenceRunView])
def run_detail(request: Request, run_id: str):
    local_only(request)
    return Envelope(data=service.get_run(request.app.state.settings, run_id), request_id=request.state.request_id)


@router.get("/evidence-runs/{run_id}/artifacts/{artifact_id}")
def artifact(request: Request, run_id: str, artifact_id: str):
    local_only(request)
    path, mime = service.artifact_path(request.app.state.settings, run_id, artifact_id)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, no-store"})
