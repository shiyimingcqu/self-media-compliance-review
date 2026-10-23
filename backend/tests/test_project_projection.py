"""t-28 项目详情 Brief 概览投影测试（夹具走真实 API；损坏行/归档态用受控 SQL 构造场景）。"""

import io
import sqlite3
import uuid

PROJECT_PLATFORMS = ["xiaohongshu", "douyin"]


def K():
    return uuid.uuid4().hex


def make_project(client):
    r = client.post("/api/v1/projects",
                    json={"name": f"proj-{K()[:6]}", "brandName": "投影品牌", "description": "",
                          "platforms": PROJECT_PLATFORMS},
                    headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def register_brief(client, project_id, text, title="Brief", platforms=("xiaohongshu",)):
    asset = client.post("/api/v1/files",
                        files={"file": (f"{title}.txt", io.BytesIO(text.encode("utf-8")), "text/plain")},
                        data={"purpose": "brief"}, headers={"Idempotency-Key": K()}).json()["data"]
    r = client.post(f"/api/v1/projects/{project_id}/briefs",
                    json={"fileId": asset["id"], "title": title, "platforms": list(platforms)},
                    headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return r.json()["data"], asset["id"]


def make_version(client, brief_data):
    r = client.post(f"/api/v1/briefs/{brief_data['briefId']}/rule-versions",
                    json={"sourceVersionId": brief_data["sourceVersionId"]},
                    headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_empty_briefs_list_is_true_empty_projection(client):
    project_id = make_project(client)
    data = client.get(f"/api/v1/projects/{project_id}").json()["data"]
    assert data["briefs"] == []
    # 列表端点模型不带 briefs 字段（轻量列表不被误读为无 Brief）
    listed = client.get("/api/v1/projects").json()["data"][0]
    assert "briefs" not in listed


def test_detail_shows_multiple_briefs_versions_and_latest_source(client):
    project_id = make_project(client)
    b1, file1 = register_brief(client, project_id, "要求一\n\n要求二", title="B1")
    v1 = make_version(client, b1)
    v2 = make_version(client, b1)  # 同 brief 两版本，历史不全量透出预过滤
    b2, file2 = register_brief(client, project_id, "另一文档", title="B2", platforms=("douyin",))
    data = client.get(f"/api/v1/projects/{project_id}").json()["data"]
    briefs = {b["id"]: b for b in data["briefs"]}
    assert set(briefs) == {b1["briefId"], b2["briefId"]}
    first = briefs[b1["briefId"]]
    assert first["platforms"] == ["xiaohongshu"] and first["title"] == "B1"
    assert first["fileId"] == file1 and first["extractionStatus"] == "ready"
    assert first["latestSourceVersionId"] == b1["sourceVersionId"]
    assert {v["id"] for v in first["versions"]} == {v1["id"], v2["id"]}  # 全量含 draft（同秒创建不依赖 id 序）
    # t-30 source 引用投影：版本指向其真实源与文件
    for v in first["versions"]:
        assert v["source"]["id"] == b1["sourceVersionId"] and v["source"]["fileId"] == file1
        assert v["source"]["extractionStatus"] == "ready"
    assert briefs[b2["briefId"]]["platforms"] == ["douyin"] and briefs[b2["briefId"]]["versions"] == []


def test_version_source_anomaly_pointer_is_null(client):
    """rule_versions.source_version_id 指向别 Brief 的源（异常指针）→ source=null，不借数据。"""
    p1 = make_project(client)
    b1, _f1 = register_brief(client, p1, "源一")
    p2 = make_project(client)
    b2, f2 = register_brief(client, p2, "源二")
    v2 = make_version(client, b2)
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE rule_versions SET source_version_id=? WHERE id=?", (b1["sourceVersionId"], v2["id"]))
        db.commit()
    versions = {v["id"]: v for v in client.get(f"/api/v1/projects/{p2}").json()["data"]["briefs"][0]["versions"]}
    assert versions[v2["id"]]["source"] is None  # 跨 brief 指针：null 不借 f1


def test_legacy_untyped_platforms_and_broken_latest_pointer_not_borrowed(client):
    project_id = make_project(client)
    b1, file1 = register_brief(client, project_id, "旧登记", title="B1")
    b2, _file2 = register_brief(client, project_id, "另一 brief", title="B2")
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE briefs SET platforms_json='[]' WHERE id=?", (b1["briefId"],))
        db.execute("UPDATE briefs SET latest_source_version_id=? WHERE id=?", (b2["sourceVersionId"], b1["briefId"]))
        db.commit()
    data = client.get(f"/api/v1/projects/{project_id}").json()["data"]
    briefs = {b["id"]: b for b in data["briefs"]}
    legacy = briefs[b1["briefId"]]
    assert legacy["platforms"] == []  # 未设置原样返回，不裁剪不伪造
    # latest 指向他 brief 的源：不借数据（file/extraction 为 None），latestSourceVersionId 也归 None 不冒充
    assert legacy["fileId"] is None and legacy["extractionStatus"] is None
    assert legacy["latestSourceVersionId"] is None
    assert briefs[b2["briefId"]]["fileId"] is not None  # 正常 brief 不受影响


def test_archived_project_detail_readable_and_cross_mode_404(client):
    project_id = make_project(client)
    register_brief(client, project_id, "归档前登记")
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE projects SET status='archived' WHERE id=?", (project_id,))
        db.commit()
    archived = client.get(f"/api/v1/projects/{project_id}")
    assert archived.status_code == 200  # 读路径不拒归档，历史可读
    assert len(archived.json()["data"]["briefs"]) == 1
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE projects SET mode='real' WHERE id=?", (project_id,))
        db.commit()
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404  # 跨模式不泄露
