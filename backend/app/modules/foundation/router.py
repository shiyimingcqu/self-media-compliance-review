from fastapi import APIRouter, Header, Query, Request

from app.core.contracts import Envelope, PageEnvelope, Platform, ProjectStatus
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation import service
from app.modules.foundation.schemas import Brand, Context, Dashboard, ModuleAvailability, Project, ProjectCreate, ProjectDetail, ProjectPatch
from app.modules.foundation.project_management import manage_project

router = APIRouter(tags=["A · foundation"])


@router.get("/context", response_model=Envelope[Context])
def context(request: Request):
    return Envelope(data=Context(timezone=request.app.state.settings.timezone), request_id=request.state.request_id)


@router.get("/modules", response_model=Envelope[list[ModuleAvailability]])
def modules(request: Request):
    data = [ModuleAvailability(id="foundation", owner="A", state="foundation"),
            *[ModuleAvailability(id=m, owner=owner, state="not_implemented") for m, owner in
              [("brief-script", "B"), ("video-model", "C"), ("human-review", "D")]]]
    return Envelope(data=data, request_id=request.state.request_id)


@router.get("/dashboard", response_model=Envelope[Dashboard])
def dashboard(request: Request):
    with connection(request.app.state.settings.database_path) as db:
        count = db.execute("SELECT COUNT(*) FROM projects WHERE mode='demo' AND status='active'").fetchone()[0]
    return Envelope(data=Dashboard(active_projects=count), request_id=request.state.request_id)


@router.get("/brands", response_model=Envelope[list[Brand]])
def brands(request: Request):
    with connection(request.app.state.settings.database_path) as db:
        data = [Brand(id=r["id"], name=r["name"]) for r in db.execute("SELECT id,name FROM brands ORDER BY name")]
    return Envelope(data=data, request_id=request.state.request_id)


@router.get("/projects", response_model=PageEnvelope[Project])
def projects(request: Request, q: str = Query("", max_length=100), platform: Platform | None = None,
             status: ProjectStatus | None = None, brand_id: str | None = Query(None, alias="brandId"),
             page: int = Query(1, ge=1), page_size: int = Query(10, alias="pageSize", ge=1, le=50)):
    if page_size not in (10, 20, 50):
        raise ApiError(400, "VALIDATION_ERROR", "每页条数仅支持 10、20、50", fields={"pageSize": "不支持的每页条数"})
    data, total = service.list_projects(request.app.state.settings.database_path, q=q,
                                        platform=platform.value if platform else None,
                                        status=status.value if status else None, brand_id=brand_id,
                                        page=page, page_size=page_size)
    return PageEnvelope(data=data, total=total, page=page, page_size=page_size, request_id=request.state.request_id)


@router.post("/projects", response_model=Envelope[Project], status_code=201)
def create_project(request: Request, payload: ProjectCreate,
                   idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return Envelope(data=service.create_project(request.app.state.settings.database_path, payload, idempotency_key),
                    request_id=request.state.request_id)


@router.get("/projects/{project_id}", response_model=Envelope[ProjectDetail])
def project_detail(project_id: str, request: Request):
    return Envelope(data=service.project_detail(request.app.state.settings.database_path, project_id), request_id=request.state.request_id)


@router.patch("/projects/{project_id}", response_model=Envelope[Project])
def update_project(project_id: str, request: Request, payload: ProjectPatch,
                   if_match: str | None = Header(default=None, alias="If-Match"),
                   idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return Envelope(data=manage_project(request.app.state.settings.database_path, project_id, "edit", if_match,
                                       idempotency_key, payload), request_id=request.state.request_id)


@router.post("/projects/{project_id}/archive", response_model=Envelope[Project])
def archive_project(project_id: str, request: Request,
                    if_match: str | None = Header(default=None, alias="If-Match"),
                    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return Envelope(data=manage_project(request.app.state.settings.database_path, project_id, "archive", if_match,
                                       idempotency_key), request_id=request.state.request_id)


@router.post("/projects/{project_id}/restore", response_model=Envelope[Project])
def restore_project(project_id: str, request: Request,
                    if_match: str | None = Header(default=None, alias="If-Match"),
                    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return Envelope(data=manage_project(request.app.state.settings.database_path, project_id, "restore", if_match,
                                       idempotency_key), request_id=request.state.request_id)

# GET /projects/{project_id}/tasks 的 501 桩已删除（A 22:16 批准）：真实实现见 tasks_router，
# 保留同名桩会因注册顺序遮挡新路由。
