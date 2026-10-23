"""D 独立入口；script/video 复用相同裁决契约。"""
from fastapi import APIRouter
from app.core.errors import not_implemented

router = APIRouter(tags=["D · human-review"])


@router.get("/human-reviews", status_code=501)
def human_reviews():
    not_implemented("human-review.queue")


@router.get("/human-reviews/{review_id}", status_code=501)
def review(review_id: str):
    not_implemented("human-review.detail")


@router.get("/jobs/{job_id}/report", status_code=501)
def report(job_id: str):
    not_implemented("human-review.report")
