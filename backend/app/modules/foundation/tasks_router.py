"""达人任务 API（第二增量）：demo 模式；替换 foundation/router.py 的 GET tasks 501 桩。"""

from fastapi import APIRouter, Header, Query, Request

from app.core.contracts import Envelope, PageEnvelope, TaskStage
from app.core.errors import ApiError
from app.modules.foundation import tasks_service
from app.modules.foundation.tasks_schemas import CreatorTask, InvalidationEvent, TaskCreate, TaskPatch, TaskRebind

router = APIRouter(tags=["A · foundation(tasks)"])


def _settings(request: Request):
    return request.app.state.settings


def _require_key(key: str) -> str:
    if not key or not key.strip() or len(key) < 8 or len(key) > 128:
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency-Key 必须为 8–128 个非空白字符",
                       fields={"Idempotency-Key": "缺失或非法"})
    return key


@router.post("/projects/{project_id}/tasks", response_model=Envelope[CreatorTask], status_code=201)
def create_task(project_id: str, request: Request, payload: TaskCreate,
                idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    _require_key(idempotency_key)
    task = tasks_service.create_task(_settings(request).database_path, project_id, payload, idempotency_key)
    return Envelope(data=task, request_id=request.state.request_id)


@router.get("/projects/{project_id}/tasks", response_model=PageEnvelope[CreatorTask])
def task_list(project_id: str, request: Request, stage: TaskStage | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(10, alias="pageSize", ge=1, le=50)):
    if page_size not in (10, 20, 50):
        raise ApiError(400, "VALIDATION_ERROR", "每页条数仅支持 10、20、50", fields={"pageSize": "不支持的每页条数"})
    data, total = tasks_service.list_tasks(_settings(request).database_path, project_id,
                                           stage=stage.value if stage else None, page=page, page_size=page_size)
    return PageEnvelope(data=data, total=total, page=page, page_size=page_size, request_id=request.state.request_id)


@router.get("/tasks/{task_id}", response_model=Envelope[CreatorTask])
def task_detail(task_id: str, request: Request):
    task = tasks_service.task_detail(_settings(request).database_path, task_id)
    return Envelope(data=task, request_id=request.state.request_id)


@router.patch("/tasks/{task_id}", response_model=Envelope[CreatorTask])
def patch_task(task_id: str, request: Request, payload: TaskPatch,
               if_match: str | None = Header(default=None, alias="If-Match"),
               idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    _require_key(idempotency_key)
    task = tasks_service.patch_task(_settings(request).database_path, task_id, payload, if_match, idempotency_key)
    return Envelope(data=task, request_id=request.state.request_id)


@router.post("/tasks/{task_id}/rebind", response_model=Envelope[CreatorTask])
def rebind_task(task_id: str, request: Request, payload: TaskRebind,
                if_match: str | None = Header(default=None, alias="If-Match"),
                idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    _require_key(idempotency_key)
    task = tasks_service.rebind_task(_settings(request).database_path, task_id, payload, if_match, idempotency_key)
    return Envelope(data=task, request_id=request.state.request_id)


@router.get("/tasks/{task_id}/history", response_model=Envelope[dict])
def task_history(task_id: str, request: Request):
    data = tasks_service.task_history(_settings(request).database_path, task_id)
    return Envelope(data=data, request_id=request.state.request_id)


@router.get("/tasks/{task_id}/invalidations", response_model=Envelope[list[InvalidationEvent]])
def invalidations(task_id: str, request: Request):
    """基础失效事件读取入口（B/C/D 消费前置能力；本增量只保证事件可查，不宣称已消费）。"""
    settings = _settings(request)
    from app.core.database import connection
    with connection(settings.database_path) as db:
        tasks_service.get_task(db, task_id)
        rows = db.execute("SELECT id,task_id,kind,reason,mode,created_at FROM task_invalidation_events "
                          "WHERE task_id=? ORDER BY created_at DESC,id DESC", (task_id,)).fetchall()
    return Envelope(data=[dict(r) for r in rows], request_id=request.state.request_id)
