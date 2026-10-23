"""D 人工复核 WIP 路由实现（t-34；**尚未激活**）。

发布边界（A 2026-10-20 13:06 紧急隔离裁决）：
- 本文件承载 t-34 在制路由/handlers，**不**被 app/main.py 导入，
  因此不进入 OpenAPI 与生成契约——未验接口不阻塞主流程（XLSX）。
- 对外 router.py 恢复已提交 HEAD 的原 501 三接口 shape；完整复核
  router 待 schema/当前性门禁完成评审后，于下一窗口原子激活
  （在 main.py 注册本文件 router，或把内容移回 router.py）。
- 现阶段**不得宣称**公共 API 已发布：本文件仅供专属测试直接调用
  与后续评审使用。
"""
from fastapi import APIRouter, Header, Query, Request

from app.core.contracts import Envelope, PageEnvelope
from app.core.database import connection
from app.core.errors import ApiError, not_implemented
from app.modules.human_review import repository as repo
from app.modules.human_review.schemas_impl import (
    DecisionBody, DecisionResult, DraftBody, ReviewDetail, ReviewItem,
)

# WIP 路由：待评审通过后由 main.py 注册（当前不注册）
router = APIRouter(tags=["D · human-review (WIP, unregistered)"])


@router.get("/human-reviews")
def human_reviews(request: Request,
                  state: str | None = Query(None),
                  q: str | None = Query(None, max_length=100),
                  platform: str | None = Query(None),
                  page: int = Query(1, ge=1),
                  pageSize: int = Query(10, alias="pageSize", ge=1, le=50)):
    if pageSize not in (10, 20, 50):
        raise ApiError(400, "VALIDATION_ERROR", "每页条数仅支持 10、20、50",
                       fields={"pageSize": str(pageSize)})
    with connection(request.app.state.settings.database_path) as db:
        items, total = repo.list_reviews(db, state=state, q=q, platform=platform,
                                         page=page, page_size=pageSize)
    # response_model 待契约窗口评审后补（A 12:33⑥ 同口径）
    return PageEnvelope[ReviewItem](data=items, total=total, page=page,
                                    page_size=pageSize, request_id=request.state.request_id)


@router.get("/human-reviews/{review_id}")
def review(review_id: str, request: Request):
    with connection(request.app.state.settings.database_path) as db:
        detail = repo.get_review(db, review_id)
    return Envelope[ReviewDetail](data=detail, request_id=request.state.request_id)


@router.post("/jobs/{job_id}/human-reviews", status_code=201)
def create_review(job_id: str, request: Request,
                  idempotency_key: str = Header(..., alias="Idempotency-Key",
                                                 min_length=8, max_length=128)):
    with connection(request.app.state.settings.database_path) as db:
        review_id = repo.create_review(db, job_id, idempotency_key)
    return {"data": {"id": review_id}, "requestId": request.state.request_id}


@router.patch("/human-reviews/{review_id}/draft")
def save_draft(review_id: str, request: Request, body: DraftBody,
               if_match: str | None = Header(default=None, alias="If-Match"),
               idempotency_key: str = Header(..., alias="Idempotency-Key",
                                              min_length=8, max_length=128)):
    with connection(request.app.state.settings.database_path) as db:
        detail = repo.save_draft(db, review_id, body, if_match, idempotency_key)
    return Envelope[ReviewDetail](data=detail, request_id=request.state.request_id)


@router.post("/human-reviews/{review_id}/decision")
def submit_decision(review_id: str, request: Request, body: DecisionBody,
                    if_match: str | None = Header(default=None, alias="If-Match"),
                    idempotency_key: str = Header(..., alias="Idempotency-Key",
                                                   min_length=8, max_length=128)):
    with connection(request.app.state.settings.database_path) as db:
        result = repo.submit_decision(db, review_id, body, if_match, idempotency_key)
    return Envelope[DecisionResult](data=result, request_id=request.state.request_id)


@router.get("/jobs/{job_id}/report", status_code=501)
def report(job_id: str):
    not_implemented("human-review.report")
