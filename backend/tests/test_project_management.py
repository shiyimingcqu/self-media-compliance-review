"""项目编辑/归档定向测试（t-44 补测）。

归属说明：本批 foundation 项目编辑/归档代码来源未确认（B 缺席，工作树内已有实现），
由 D 补测并承接为团队资产；本文件只验证既有实现的行为，不改产品代码。

写法与仓库既有后端测试一致：tmp_path 独立库 + FastAPI TestClient + 真实 HTTP 路径。
本增量先落 3 个最小用例（PATCH 成功改字段 / revision 不匹配 409 / 归档后新写 409）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as c:
        yield c


def new_project(client: TestClient, *, name: str | None = None) -> dict:
    tag = uuid4().hex[:8]
    r = client.post("/api/v1/projects", json={
        "name": name or f"项目-{tag}", "brandName": f"品牌-{tag}", "platforms": ["douyin"]},
        headers={"Idempotency-Key": f"mk-{uuid4().hex[:12]}"})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def patch_project(client: TestClient, project: dict, body: dict, *,
                  if_match: str | None = None, key: str | None = None):
    headers = {"Content-Type": "application/json",
               "Idempotency-Key": key or f"pt-{uuid4().hex[:12]}"}
    if if_match is not None:
        headers["If-Match"] = if_match
    return client.patch(f"/api/v1/projects/{project['id']}", json=body, headers=headers)


class TestProjectPatch:
    def test_patch_updates_fields(self, client):
        project = new_project(client)
        r = patch_project(client, project, {"name": "改名后的项目", "description": "新说明"},
                          if_match=str(project["revision"]))
        assert r.status_code == 200, r.text
        updated = r.json()["data"]
        assert updated["name"] == "改名后的项目"
        assert updated["description"] == "新说明"
        assert updated["revision"] == project["revision"] + 1

    def test_patch_revision_mismatch_conflict(self, client):
        project = new_project(client)
        stale = project["revision"] - 1 if project["revision"] > 1 else 0
        r = patch_project(client, project, {"name": "不该生效"}, if_match=str(stale))
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "REVISION_CONFLICT"
        # 真值未变
        cur = client.get(f"/api/v1/projects/{project['id']}").json()["data"]
        assert cur["name"] == project["name"]


class TestArchiveGate:
    def test_archived_project_rejects_new_write(self, client):
        project = new_project(client)
        r = client.post(f"/api/v1/projects/{project['id']}/archive",
                        headers={"Idempotency-Key": f"ar-{uuid4().hex[:12]}",
                                 "If-Match": str(project["revision"])})
        assert r.status_code == 200, r.text
        archived = r.json()["data"]
        assert archived["status"] == "archived"

        # 归档后新写（新 key）→ 409 PROJECT_ARCHIVED
        blocked = patch_project(client, archived, {"name": "归档后改名"},
                                if_match=str(archived["revision"]))
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "PROJECT_ARCHIVED"
