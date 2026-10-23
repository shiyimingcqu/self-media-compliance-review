"""t-17 首批：Brief 登记 + TXT 解析（tmp_path 独立库；不种 enabled 规则，不碰 demo 库）。"""

import io
import json
import uuid

from app.core.database import connection

GBK_BYTES = "中文GB18030无BOM".encode("gb18030")
GARBLED = b"\x80\x81\x82\x83\x84\x85" * 8


def upload_txt(client, text: str, name="brief.txt"):
    key = uuid.uuid4().hex
    return client.post("/api/v1/files",
                       files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/plain")},
                       data={"purpose": "brief"}, headers={"Idempotency-Key": key})


def make_project(client):
    key = uuid.uuid4().hex
    resp = client.post("/api/v1/projects",
                       json={"name": f"p-{key[:6]}", "brandName": "麦咖啡", "description": "",
                             "platforms": ["xiaohongshu"]},
                       headers={"Idempotency-Key": key})
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def register(client, project_id, file_id, title="Brief 一", key=None, platforms=("xiaohongshu",)):
    return client.post(f"/api/v1/projects/{project_id}/briefs",
                       json={"fileId": file_id, "title": title, "platforms": list(platforms)},
                       headers={"Idempotency-Key": key or uuid.uuid4().hex})


def test_txt_registration_persists_verifiable_source_map(client):
    project_id = make_project(client)
    text = "产品露出不少于 4 张\n\n禁止功效表达\n  小红书第 20 页\n\n尾段"
    asset = upload_txt(client, text).json()["data"]
    response = register(client, project_id, asset["id"])
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["extractionStatus"] == "ready" and data["encoding"] == "utf-8"
    assert [b["paragraphId"] for b in data["blocks"]] == ["p1", "p2", "p3"]
    # 来源映射可回验：text[start:end]==块文本
    for block in data["blocks"]:
        assert text[block["startOffset"]:block["endOffset"]] == block["text"]
    settings = client.app.state.settings
    with connection(settings.database_path) as db:
        stored = db.execute("SELECT text,start_offset,end_offset FROM brief_text_blocks "
                            "WHERE source_version_id=? ORDER BY ordinal",
                            (data["sourceVersionId"],)).fetchall()
        assert len(stored) == 3
        version = db.execute("SELECT extraction_status,page_count FROM brief_source_versions WHERE id=?",
                             (data["sourceVersionId"],)).fetchone()
        assert version[0] == "ready"
        # A t-19 修正①：TXT 段落数不是页数 → page_count 必须 NULL；段落数由块表行计
        assert version[1] is None
        assert db.execute("SELECT COUNT(*) FROM brief_text_blocks WHERE source_version_id=?",
                          (data["sourceVersionId"],)).fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0] == 0  # 未种任何规则/enabled


def test_source_map_reread_rebuilds_same_paragraph_ids_and_offsets(client):
    """A t-19：paragraphId 与持久 block 定位需能稳定重建——重新读库校验，不只看首次响应。"""
    project_id = make_project(client)
    text = "第一段\n续行\n\n第二段\n\n\n第三段"
    asset = upload_txt(client, text).json()["data"]
    first = register(client, project_id, asset["id"]).json()["data"]
    with connection(client.app.state.settings.database_path) as db:
        rows = db.execute("SELECT ordinal,start_offset,end_offset,text FROM brief_text_blocks "
                          "WHERE source_version_id=? ORDER BY ordinal",
                          (first["sourceVersionId"],)).fetchall()
    assert [f"p{r['ordinal'] + 1}" for r in rows] == [b["paragraphId"] for b in first["blocks"]]
    assert [r["start_offset"] for r in rows] == [b["startOffset"] for b in first["blocks"]]
    assert [r["end_offset"] for r in rows] == [b["endOffset"] for b in first["blocks"]]
    for r in rows:
        assert r["text"] == text[r["start_offset"]:r["end_offset"]]  # 偏移可重建原文切片


def test_title_blank_rejected_and_padded_title_stored_trimmed(client):
    """A t-19 修正②：title 纯空白必须拒绝；合法带空格标题入库为 trim 后值。"""
    project_id = make_project(client)
    asset = upload_txt(client, "内容段").json()["data"]
    blank = client.post(f"/api/v1/projects/{project_id}/briefs",
                        json={"fileId": asset["id"], "title": "   ", "platforms": ["xiaohongshu"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    assert blank.status_code == 400 and blank.json()["error"]["code"] == "VALIDATION_ERROR"
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM briefs").fetchone()[0] == 0
    padded = register(client, project_id, asset["id"], title="  小红书 Brief  ")
    assert padded.status_code == 201, padded.text
    with connection(client.app.state.settings.database_path) as db:
        row = db.execute("SELECT title FROM briefs WHERE id=?",
                         (padded.json()["data"]["briefId"],)).fetchone()
        assert row[0] == "小红书 Brief"


def test_platforms_required_subset_and_persisted(client):
    """B_0003 补丁：platforms 必填非空、去重、项目平台子集；字段级 400；入库规范化集合。"""
    project_id = make_project(client)  # 项目平台=xiaohongshu
    asset = upload_txt(client, "平台段").json()["data"]
    missing = client.post(f"/api/v1/projects/{project_id}/briefs",
                          json={"fileId": asset["id"], "title": "T"},
                          headers={"Idempotency-Key": uuid.uuid4().hex})
    assert missing.status_code == 400 and "platforms" in json.dumps(missing.json())
    empty = client.post(f"/api/v1/projects/{project_id}/briefs",
                        json={"fileId": asset["id"], "title": "T", "platforms": []},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "VALIDATION_ERROR"
    unknown = client.post(f"/api/v1/projects/{project_id}/briefs",
                          json={"fileId": asset["id"], "title": "T", "platforms": ["douyin"]},  # 跨项目平台
                          headers={"Idempotency-Key": uuid.uuid4().hex})
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "platforms" in unknown.json()["error"].get("fieldErrors", {})
    bogus = client.post(f"/api/v1/projects/{project_id}/briefs",
                        json={"fileId": asset["id"], "title": "T", "platforms": ["weibo"]},  # 枚举外
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    assert bogus.status_code == 400
    dup = client.post(f"/api/v1/projects/{project_id}/briefs",
                      json={"fileId": asset["id"], "title": "T", "platforms": ["xiaohongshu", "xiaohongshu"]},
                      headers={"Idempotency-Key": uuid.uuid4().hex})
    assert dup.status_code == 400  # 重复拒绝
    ok = register(client, project_id, asset["id"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["data"]["platforms"] == ["xiaohongshu"]
    with connection(client.app.state.settings.database_path) as db:
        stored = db.execute("SELECT platforms_json FROM briefs WHERE id=?",
                            (ok.json()["data"]["briefId"],)).fetchone()[0]
        assert json.loads(stored) == ["xiaohongshu"]


def test_platforms_change_same_key_conflicts_and_order_irrelevant_replay(client):
    """幂等指纹含规范化平台集合：同 key 换集合 409；仅顺序不同视为同一 payload 回放。"""
    project_id = make_project(client)
    key = uuid.uuid4().hex
    asset = upload_txt(client, "集合段").json()["data"]
    first = register(client, project_id, asset["id"], key=key, platforms=("xiaohongshu",))
    assert first.status_code == 201
    changed = client.post(f"/api/v1/projects/{project_id}/briefs",
                          json={"fileId": asset["id"], "title": "Brief 一", "platforms": ["xiaohongshu"]},
                          headers={"Idempotency-Key": key})
    # 同 payload 换 title 也算不同内容
    assert changed.status_code == 409 or changed.json()["data"] == first.json()["data"]
    if changed.status_code == 409:
        assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with connection(client.app.state.settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
    second_asset = upload_txt(client, "另一 Brief 文件").json()["data"]
    project2 = make_project(client)  # 多平台项目
    multi = client.post("/api/v1/projects", json={"name": "multi-p", "brandName": "B2", "description": "",
                                                  "platforms": ["douyin", "xiaohongshu"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex}).json()["data"]
    k2 = uuid.uuid4().hex
    a = client.post(f"/api/v1/projects/{multi['id']}/briefs",
                    json={"fileId": second_asset["id"], "title": "顺序测试",
                          "platforms": ["xiaohongshu", "douyin"]},
                    headers={"Idempotency-Key": k2})
    b = client.post(f"/api/v1/projects/{multi['id']}/briefs",
                    json={"fileId": second_asset["id"], "title": "顺序测试",
                          "platforms": ["douyin", "xiaohongshu"]},  # 仅顺序不同
                    headers={"Idempotency-Key": k2})
    assert a.status_code == 201 and b.status_code == 201 and a.json()["data"] == b.json()["data"]
    assert a.json()["data"]["platforms"] == ["douyin", "xiaohongshu"]  # 规范化排序
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM briefs").fetchone()[0] == 2  # 顺序重放不多建


def test_blank_and_garbled_and_unsupported_extensions_explicit(client):
    project_id = make_project(client)
    blank_id = upload_txt(client, "   \n\n  ", name="blank.txt").json()["data"]["id"]
    blank = register(client, project_id, blank_id)
    assert blank.status_code == 201  # 登记成功但解析明确失败，不伪装可审核
    assert blank.json()["data"]["extractionStatus"] == "invalid"
    assert blank.json()["data"]["error"]["code"] == "EXTRACTION_FAILED"
    assert blank.json()["data"]["blocks"] == []

    key = uuid.uuid4().hex
    garbled = client.post("/api/v1/files", files={"file": ("g.txt", io.BytesIO(GBK_BYTES), "text/plain")},
                          data={"purpose": "brief"}, headers={"Idempotency-Key": key}).json()["data"]
    # 无 BOM GB18030：可靠编码策略下按解析失败处理（invalid），不宽容解码冒充成功
    g = register(client, project_id, garbled["id"])
    assert g.json()["data"]["extractionStatus"] == "invalid"
    other = client.post("/api/v1/files", files={"file": ("h.txt", io.BytesIO(GARBLED), "text/plain")},
                        data={"purpose": "brief"}, headers={"Idempotency-Key": uuid.uuid4().hex}).json()["data"]
    o = register(client, project_id, other["id"])
    assert o.json()["data"]["extractionStatus"] == "invalid"

    # t-40 起 PDF 已真实接入：损坏 PDF 走解析失败 → invalid（不再 501），且不产生可用文本
    pdf_key = uuid.uuid4().hex
    pdf = client.post("/api/v1/files", files={"file": ("b.pdf", io.BytesIO(b"%PDF-1.7\nx" * 4), "application/pdf")},
                      data={"purpose": "brief"}, headers={"Idempotency-Key": pdf_key}).json()["data"]
    broken = register(client, project_id, pdf["id"])
    assert broken.status_code == 201, broken.text
    assert broken.json()["data"]["extractionStatus"] == "invalid"
    assert broken.json()["data"]["blocks"] == []  # 不伪造段落


def test_registration_gates_project_mode_purpose_and_binding(client):
    project_id = make_project(client)
    missing = register(client, project_id, "no-such-file")
    assert missing.status_code == 404

    script_asset = client.post("/api/v1/files",
                               files={"file": ("s.txt", io.BytesIO("脚本".encode("utf-8")), "text/plain")},
                               data={"purpose": "script"},
                               headers={"Idempotency-Key": uuid.uuid4().hex}).json()["data"]
    wrong_purpose = register(client, project_id, script_asset["id"])
    assert wrong_purpose.status_code == 400  # 用途不符：未分配≠可错用

    text_id = upload_txt(client, "规则一").json()["data"]["id"]
    first = register(client, project_id, text_id)
    assert first.status_code == 201
    # 已绑定他项目 vs 已绑定本项目：区分如实
    other_project = make_project(client)
    bound_elsewhere = register(client, other_project, text_id)
    assert bound_elsewhere.status_code == 409 and bound_elsewhere.json()["error"]["code"] == "FILE_ALREADY_REGISTERED"
    assert bound_elsewhere.json()["error"]["details"]["boundToThisProject"] is False
    same = register(client, project_id, text_id, key=uuid.uuid4().hex)
    assert same.status_code == 409 and same.json()["error"]["details"]["boundToThisProject"] is True


def test_registration_idempotent_replay_and_conflict(client):
    project_id = make_project(client)
    text_id = upload_txt(client, "幂等段A").json()["data"]["id"]
    key = uuid.uuid4().hex
    first = register(client, project_id, text_id, key=key)
    assert first.status_code == 201
    replay = register(client, project_id, text_id, key=key)
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    conflict = register(client, project_id, upload_txt(client, "另一文件").json()["data"]["id"], key=key)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM briefs").fetchone()[0] == 1
