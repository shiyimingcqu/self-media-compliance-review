"""t-30 GET /files/:id/preview 只读来源投影测试（tmp_path 独立库）。"""

import io
import sqlite3
import uuid

PDF_DOCX = b"PK\x03\x04" + b"\x00" * 40


def K():
    return uuid.uuid4().hex


def upl(client, text=None, name="b.txt", purpose="brief", raw=None):
    body = raw if raw is not None else (text or "段落甲\n\n段落乙").encode("utf-8")
    return client.post("/api/v1/files", files={"file": (name, io.BytesIO(body), "application/octet-stream")},
                       data={"purpose": purpose}, headers={"Idempotency-Key": K()}).json()["data"]


def project(client):
    return client.post("/api/v1/projects", json={"name": f"p-{K()[:6]}", "brandName": "预览品牌",
                                                 "description": "", "platforms": ["xiaohongshu"]},
                       headers={"Idempotency-Key": K()}).json()["data"]


def register(client, project_id, **kw):
    asset = upl(client, **kw)
    r = client.post(f"/api/v1/projects/{project_id}/briefs",
                    json={"fileId": asset["id"], "title": "预览 Brief", "platforms": ["xiaohongshu"]},
                    headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return asset, r.json()["data"]


def test_ready_roundtrip_offsets_and_ids(client):
    p = project(client)
    text = "必须口播全称\n\n禁止功效词"
    asset, brief = register(client, p["id"], text=text)
    r = client.get(f"/api/v1/files/{asset['id']}/preview", params={"sourceVersionId": brief["sourceVersionId"]})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourceVersionId"] == brief["sourceVersionId"] and data["fileId"] == asset["id"]
    assert data["extractionStatus"] == "ready"
    assert [b["paragraphId"] for b in data["blocks"]] == ["p1", "p2"]
    for b in data["blocks"]:
        assert text[b["startOffset"]:b["endOffset"]] == b["text"]


def test_source_omitted_uses_registered_unique_source(client):
    p = project(client)
    asset, _brief = register(client, p["id"])
    r = client.get(f"/api/v1/files/{asset['id']}/preview")
    assert r.status_code == 200 and r.json()["data"]["blocks"]


def test_invalid_source_state_not_faked(client):
    p = project(client)
    asset, brief = register(client, p["id"], text="   \n  ")  # invalid 登记
    assert brief["extractionStatus"] == "invalid"
    r = client.get(f"/api/v1/files/{asset['id']}/preview", params={"sourceVersionId": brief["sourceVersionId"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PREVIEW_NOT_READY"
    assert r.json()["error"]["details"]["extractionStatus"] == "invalid"


def test_mismatch_cross_project_and_mode_rejected(client):
    p1, p2 = project(client), project(client)
    asset1, brief1 = register(client, p1["id"])
    _asset2, brief2 = register(client, p2["id"])
    # 历史源不被别文件/别项目冒名：source 与路径 fileId 不匹配 → 404
    mismatch = client.get(f"/api/v1/files/{asset1['id']}/preview",
                          params={"sourceVersionId": brief2["sourceVersionId"]})
    assert mismatch.status_code == 404
    wrong_file = client.get(f"/api/v1/files/{K()}/preview", params={"sourceVersionId": brief1["sourceVersionId"]})
    assert wrong_file.status_code == 404
    missing = client.get(f"/api/v1/files/{K()}/preview")
    assert missing.status_code == 404
    # 跨模式：项目转 real 后不可读
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("UPDATE projects SET mode='real' WHERE id=?", (p1["id"],))
        db.commit()
    assert client.get(f"/api/v1/files/{asset1['id']}/preview").status_code == 409  # demo 下无已登记源
    assert client.get(f"/api/v1/files/{asset1['id']}/preview",
                      params={"sourceVersionId": brief1["sourceVersionId"]}).status_code == 404


def test_ready_without_blocks_is_500_not_empty_success(client):
    p = project(client)
    asset, brief = register(client, p["id"])
    with sqlite3.connect(client.app.state.settings.database_path) as db:
        db.execute("DELETE FROM brief_text_blocks WHERE source_version_id=?", (brief["sourceVersionId"],))
        db.commit()
    r = client.get(f"/api/v1/files/{asset['id']}/preview", params={"sourceVersionId": brief["sourceVersionId"]})
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    body = r.text
    assert "storage" not in body and "sqlite" not in body.lower()  # 不泄内部路径/库信息


def test_purpose_gate_and_unregistered_file(client):
    script_asset = upl(client, text="脚本文件", purpose="script")
    r = client.get(f"/api/v1/files/{script_asset['id']}/preview")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    orphan = upl(client, text="未登记")
    r2 = client.get(f"/api/v1/files/{orphan['id']}/preview")
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "PREVIEW_NOT_READY"
