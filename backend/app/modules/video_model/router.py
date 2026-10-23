"""C 独立入口；没有视频能力验证时不得返回通过。"""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.contracts import Envelope
from app.core.database import connection as db_connection
from app.core.errors import ApiError


router = APIRouter(tags=["C · video-model"])


# ---------- DTOs---------- (已删@dataclass)


class VideoVersionRef(BaseModel):
    """视频版本引用记录"""
    id: str
    status: str
    submission_state: str
    video_file_id: str | None
    script_version_id: str | None
    rule_version_id: str | None
    created_at: str
    updated_at: str
    error_message: str | None


class ListVideoVersionsOutput(BaseModel):
    videos: list[VideoVersionRef]
    version_entity_status: str = "not_implemented"


# ---------- 端点实现---------- (用 request.app.state.settings.database_path)


async def get_db(request: Request):
    """与异步列表端点在同一线程创建、使用并关闭请求内连接。"""
    with db_connection(request.app.state.settings.database_path) as conn:
        yield conn


@router.get("/tasks/{task_id}/videos", response_model=Envelope[ListVideoVersionsOutput])
async def list_video_versions(
    task_id: str,
    request: Request,
    db: Annotated[sqlite3.Connection, Depends(get_db)]
):
    """GET /tasks/{task_id}/videos - 视频版本列表
    
    查 review_jobs WHERE task_id=? AND kind='video'
    返回真实字段：id,status,submission_state,video_file_id等
    声明：视频版本实体未实现
    """
    # 校验 task_id 是否存在
    task_row = db.execute(
        "SELECT id FROM creator_tasks WHERE id=?",
        (task_id,)
    ).fetchone()
    
    if not task_row:
        raise ApiError(404, "RESOURCE_NOT_FOUND", f"Task {task_id} does not exist")
    
    # 查询视频作业
    cursor = db.execute(
        """SELECT j.id, j.status, j.submission_state, j.video_file_id, 
                  j.script_version_id, j.rule_version_id, j.created_at, 
                  j.updated_at, j.error_message
           FROM review_jobs j
           WHERE j.task_id=? AND j.kind='video'
           ORDER BY j.created_at DESC""",
        (task_id,)
    )
    rows = cursor.fetchall()
    
    videos = []
    for row in rows:
        videos.append({
            "id": row["id"],
            "status": row["status"],
            "submission_state": row["submission_state"],
            "video_file_id": row["video_file_id"],
            "script_version_id": row["script_version_id"],
            "rule_version_id": row["rule_version_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error_message": row["error_message"]
        })
    
    # 用 Envelope 返回 (项目约定)
    return Envelope(
        data=ListVideoVersionsOutput(videos=videos),
        request_id=request.state.request_id
    )
