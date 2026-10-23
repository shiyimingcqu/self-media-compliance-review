"""C video-model 首批 API 定向测试（t-43）。"""
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    """创建测试客户端 (使用 tmp_path 作为 DB)"""
    db_path = tmp_path / "workbench.sqlite3"
    
    # 初始化数据库表
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # 创建项目表
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, brandId TEXT, mode TEXT NOT NULL)")
    
    # 创建品牌表
    conn.execute("CREATE TABLE brands (id TEXT PRIMARY KEY, name TEXT)")
    
    # 创建达人任务表
    conn.execute("""CREATE TABLE creator_tasks (
        id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), 
        creatorName TEXT, platform TEXT, contentType TEXT, 
        campaignPhase TEXT, ruleVersionId TEXT, stage TEXT, archived INTEGER DEFAULT 0
    )""")
    
    # 创建 Brief 表
    conn.execute("CREATE TABLE briefs (id TEXT PRIMARY KEY, projectId TEXT REFERENCES projects(id))")
    
    # 创建 Brief 源版本表
    conn.execute("CREATE TABLE brief_source_versions (id TEXT PRIMARY KEY, briefId TEXT REFERENCES briefs(id))")
    
    # 创建脚本版本表
    conn.execute("CREATE TABLE script_versions (id TEXT PRIMARY KEY, taskId TEXT REFERENCES creator_tasks(id), versionLabel TEXT, extractionStatus TEXT, mode TEXT)")
    
    # 创建规则版本表
    conn.execute("CREATE TABLE rule_versions (rule_version_id TEXT PRIMARY KEY, briefId TEXT REFERENCES briefs(id), state TEXT, applicablePlatforms TEXT, applicablePhases TEXT)")
    
    # 创建文件资产表
    conn.execute("CREATE TABLE file_assets (id TEXT PRIMARY KEY, purpose TEXT, mode TEXT NOT NULL, sha256 TEXT)")
    
    # 创建 audit_events 表
    conn.execute("CREATE TABLE audit_events (id TEXT PRIMARY KEY, actor TEXT, entityType TEXT, entityId TEXT, action TEXT, mode TEXT, details TEXT, createdAt TEXT)")
    
    # 创建 review_jobs 表 (C_0001) - 显式列名
    conn.execute("""CREATE TABLE review_jobs (
        id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('brief','script','video','capability_test')),
        task_id TEXT REFERENCES creator_tasks(id), source_version_id TEXT REFERENCES brief_source_versions(id),
        script_version_id TEXT, video_file_id TEXT REFERENCES file_assets(id), rule_version_id TEXT,
        mode TEXT NOT NULL CHECK(mode IN ('demo','real')), status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','succeeded','failed','interrupted')),
        snapshot_json TEXT NOT NULL, input_json TEXT NOT NULL, provider_request_id TEXT,
        submission_state TEXT NOT NULL DEFAULT 'not_sent' CHECK(submission_state IN ('not_sent','submitted','unknown')),
        error_message TEXT, retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)),
        attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0), retry_of TEXT REFERENCES review_jobs(id),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK (kind IN ('brief','capability_test') OR task_id IS NOT NULL)
    )""")
    
    # 创建 jobs_results 表
    conn.execute("CREATE TABLE jobs_results (job_id TEXT PRIMARY KEY REFERENCES review_jobs(id), result_json TEXT NOT NULL, created_at TEXT NOT NULL)")
    
    conn.commit()
    conn.close()
    
    # 创建应用并注入 DB 路径
    from app.core.config import Settings
    settings = Settings(
        data_dir=tmp_path,
        timezone="Asia/Hong_Kong",
        max_document_bytes=20971520,
        allow_real_provider_calls=False,
        provider_model_id="",
        provider_endpoint_base="",
        provider_config_version=""
    )
    
    app = create_app(settings=settings)
    return TestClient(app)


class TestListVideoVersions:
    """测试 GET /tasks/{task_id}/videos"""
    
    def test_returns_empty_when_no_videos(self, client, tmp_path):
        """无视频登记时返回列表为空"""
        db_path = tmp_path / "workbench.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # 插入有效 task
        conn.execute("INSERT INTO projects VALUES ('proj-1', 'test', NULL, 'demo')")
        conn.execute("INSERT INTO creator_tasks VALUES ('task-1', 'proj-1', 'name', 'douyin', 'video', 'launch', 'rv-1', 'requirements', 0)")
        
        # 显式列名插入空作业列表
        conn.commit()
        conn.close()
        
        response = client.get("/api/v1/tasks/task-1/videos")
        
        assert response.status_code == 200
        data = response.json()
        # Envelope 包装：data 包含 ListVideoVersionsOutput
        assert data["data"]["videos"] == []
        assert data["data"]["version_entity_status"] == "not_implemented"
        # request_id 存在
        assert "requestId" in data or "request_id" in data
    
    def test_returns_registered_videos(self, client, tmp_path):
        """列出已登记的视频引用"""
        db_path = tmp_path / "workbench.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        conn.execute("INSERT INTO projects VALUES ('proj-1', 'test', NULL, 'demo')")
        conn.execute("INSERT INTO creator_tasks VALUES ('task-1', 'proj-1', 'name', 'douyin', 'video', 'launch', 'rv-1', 'requirements', 0)")
        
        # 插入 demo 模式视频作业 - 显式列名
        conn.execute("""INSERT INTO review_jobs (id, kind, task_id, source_version_id, script_version_id, video_file_id, rule_version_id, mode, status, snapshot_json, input_json, submission_state, retryable, attempt, retry_of, created_at, updated_at)
            VALUES ('job-1', 'video', 'task-1', NULL, 'scr-1', 'vid-1', 'rv-1', 'demo', 'queued', '{}', '{}', 'not_sent', 0, 0, NULL, '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')""")
        
        conn.commit()
        conn.close()
        
        response = client.get("/api/v1/tasks/task-1/videos")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["videos"]) == 1
        assert data["data"]["videos"][0]["id"] == "job-1"
        assert data["data"]["videos"][0]["status"] == "queued"
        assert data["data"]["videos"][0]["submission_state"] == "not_sent"
        assert data["data"]["videos"][0]["video_file_id"] == "vid-1"
    
    def test_returns_404_when_task_not_found(self, client, tmp_path):
        """不存在的任务返回 404"""
        # 不创建任何数据
        response = client.get("/api/v1/tasks/nonexistent-task/videos")
        
        assert response.status_code == 404
        # 错误格式是顶层 {"error": {"code": ...}}
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
