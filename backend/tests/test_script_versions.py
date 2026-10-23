"""t-25 脚本版本首增量测试（tmp_path 独立库；确定性序号/互斥/失效联动/幂等）。"""

import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from app.core.database import connection

SCRIPT_TXT = "开场口播：提提神\n\n中段剧情：喝完立刻精神了\n\n收尾：产品特写"


def K():
    return uuid.uuid4().hex


def upl_script(client, text=SCRIPT_TXT, name="s.txt"):
    return client.post("/api/v1/files", files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/plain")},
                       data={"purpose": "script"}, headers={"Idempotency-Key": K()}).json()["data"]


def enabled_rule_version(client, project_id):
    """经正规流程得到 enabled 规则版本：Brief 登记→草稿→确认→启用（不直接写库、不种伪 enabled）。"""
    asset = client.post("/api/v1/files",
                        files={"file": ("r.txt", io.BytesIO("必须口播品牌名\n\n第二段".encode("utf-8")), "text/plain")},
                        data={"purpose": "brief"}, headers={"Idempotency-Key": K()}).json()["data"]
    b = client.post(f"/api/v1/projects/{project_id}/briefs",
                    json={"fileId": asset["id"], "title": "规则源", "platforms": ["xiaohongshu"]},
                    headers={"Idempotency-Key": K()}).json()["data"]
    v = client.post(f"/api/v1/briefs/{b['briefId']}/rule-versions",
                    json={"sourceVersionId": b["sourceVersionId"]}, headers={"Idempotency-Key": K()}).json()["data"]
    rev = str(v["revision"])
    req = client.post(f"/api/v1/rule-versions/{v['id']}/requirements",
                      json={"text": "必须口播品牌名", "category": "口播", "importance": "must",
                            "platforms": ["xiaohongshu"], "contentTypes": ["video"], "phases": ["seeding"],
                            "reviewStages": ["script", "video"], "exceptions": "",
                            "source": {"fileId": asset["id"], "paragraphId": "p1", "quote": "必须口播品牌名"}},
                      headers={"Idempotency-Key": K(), "If-Match": rev}).json()["data"]
    client.post(f"/api/v1/rule-versions/{v['id']}/confirm", json={"requirementIds": [req["id"]]},
                headers={"Idempotency-Key": K(), "If-Match": str(int(rev) + 1)})
    enabled = client.post(f"/api/v1/rule-versions/{v['id']}/enable", json={"changeSummary": "首启用"},
                          headers={"Idempotency-Key": K(), "If-Match": str(int(rev) + 2)})
    assert enabled.status_code == 200, enabled.text
    return enabled.json()["data"]


def task_with_rule(client):
    """项目→启用规则→绑定任务（全走 API 正规流程）。"""
    p = client.post("/api/v1/projects",
                    json={"name": f"p-{K()[:6]}", "brandName": "麦咖啡", "description": "",
                          "platforms": ["xiaohongshu"]}, headers={"Idempotency-Key": K()}).json()["data"]
    rv = enabled_rule_version(client, p["id"])
    task = client.post(f"/api/v1/projects/{p['id']}/tasks",
                       json={"creatorName": "达人甲", "platform": "xiaohongshu", "contentType": "video",
                             "campaignPhase": "seeding", "ruleVersionId": rv["id"]},
                       headers={"Idempotency-Key": K()})
    assert task.status_code == 201, task.text
    return p, task.json()["data"]


def submit(client, task_id, body, key=None):
    return client.post(f"/api/v1/tasks/{task_id}/scripts", json=body,
                       headers={"Idempotency-Key": key or K()})


def test_text_submission_deterministic_ordinals_and_invalidation(client):
    p, task = task_with_rule(client)
    first = submit(client, task["id"], {"inputType": "text", "text": SCRIPT_TXT})
    assert first.status_code == 201, first.text
    created = first.json()["data"]
    assert created["ordinal"] == 1 and created["extractionStatus"] == "ready" and created["inputType"] == "text"
    assert created["fileId"] is None and created["charCount"] == len(SCRIPT_TXT)
    # sourceMap 偏移指向所存原文（含首尾），text[start:end] 精确可回验
    for block in created["sourceMap"]:
        assert SCRIPT_TXT[block["startOffset"]:block["endOffset"]] == block["text"]
    with connection(client.app.state.settings.database_path) as db:
        stage = db.execute("SELECT stage,revision FROM creator_tasks WHERE id=?", (task["id"],)).fetchone()
        assert stage[0] == "script"  # 阶段回脚本重审起点
        events = db.execute("SELECT kind FROM task_invalidation_events WHERE task_id=?", (task["id"],)).fetchall()
        assert [e[0] for e in events] == ["new_script_version"]
    second = submit(client, task["id"], {"inputType": "text", "text": "全新第二版脚本\n\n第二段"})
    assert second.status_code == 201 and second.json()["data"]["ordinal"] == 2
    listing = client.get(f"/api/v1/tasks/{task['id']}/scripts").json()["data"]
    assert [v["ordinal"] for v in listing] == [2, 1]
    assert [v["isCurrent"] for v in listing] == [True, False]


def test_request_shape_errors_create_nothing(client):
    p, task = task_with_rule(client)
    cases = [
        ({"inputType": "text", "text": "   \n  "}, 400),        # 纯空白
        ({"inputType": "text", "text": "x" * 50_001}, 400),     # 超字符界
        ({"inputType": "text", "text": "有内容", "fileId": "f1"}, 400),  # 粘贴伪 fileId
        ({"inputType": "file", "fileId": None}, 400),           # file 缺 fileId
        ({"inputType": "file", "fileId": "nope"}, 404),         # 不存在
        ({"inputType": "text"}, 400),                            # 空白 text 字段缺省=互斥错
    ]
    for body, status in cases:
        r = submit(client, task["id"], body)
        assert r.status_code == status, (body, r.status_code, r.text)
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM script_versions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events").fetchone()[0] == 0
        assert db.execute("SELECT revision FROM creator_tasks").fetchone()[0] == task["revision"]  # 未被误 bump


def test_purpose_and_file_cross_task_gates(client):
    p, task = task_with_rule(client)
    brief_asset = client.post("/api/v1/files", files={"file": ("b.txt", io.BytesIO("品牌补充".encode()), "text/plain")},
                              data={"purpose": "brief"}, headers={"Idempotency-Key": K()}).json()["data"]
    wrong = submit(client, task["id"], {"inputType": "file", "fileId": brief_asset["id"]})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "VALIDATION_ERROR"
    script_asset = upl_script(client)
    ok = submit(client, task["id"], {"inputType": "file", "fileId": script_asset["id"]})
    assert ok.status_code == 201 and ok.json()["data"]["inputType"] == "file"
    p2, task2 = task_with_rule(client)
    cross = submit(client, task2["id"], {"inputType": "file", "fileId": script_asset["id"]})
    assert cross.status_code == 409 and cross.json()["error"]["code"] == "FILE_ALREADY_REGISTERED"
    assert cross.json()["error"]["details"].get("crossProject") is True
    # 同任务重复引用同文件=另一不可变版本（不覆盖旧行）
    again = submit(client, task["id"], {"inputType": "file", "fileId": script_asset["id"]})
    assert again.status_code == 201 and again.json()["data"]["ordinal"] == 2


def test_same_project_tasks_share_script_file_explicitly(client):
    """A 01:38 共享裁决：同项目两任务显式引用同一不可变 fileId→各自版本/失效/审计独立；
    EXISTS 他项目即拒（先有同项目行再出现跨项目行也不能漏）；同 key 回放不多建。"""
    p, task = task_with_rule(client)
    asset = upl_script(client)
    first = submit(client, task["id"], {"inputType": "file", "fileId": asset["id"]})
    assert first.status_code == 201
    # 同项目第二个任务（不同达人）
    rv = enabled_rule_version(client, p["id"])["id"]
    t2 = client.post(f"/api/v1/projects/{p['id']}/tasks",
                     json={"creatorName": "达人乙", "platform": "xiaohongshu", "contentType": "video",
                           "campaignPhase": "seeding", "ruleVersionId": rv},
                     headers={"Idempotency-Key": K()}).json()["data"]
    shared = submit(client, t2["id"], {"inputType": "file", "fileId": asset["id"]})
    assert shared.status_code == 201, shared.text
    assert shared.json()["data"]["scriptVersionId"] != first.json()["data"]["scriptVersionId"]
    assert shared.json()["data"]["ordinal"] == 1  # 独立序号空间按任务
    with connection(client.app.state.settings.database_path) as db:
        events = db.execute("SELECT COUNT(*) FROM task_invalidation_events WHERE kind='new_script_version'").fetchone()[0]
        assert events == 2  # 各自失效事件
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_type='script_version'").fetchone()[0] == 2
    key = K()
    body = {"inputType": "file", "fileId": asset["id"]}
    a = submit(client, task["id"], body, key=key)
    b = submit(client, task["id"], body, key=key)
    assert a.status_code == 201 and b.json()["data"] == a.json()["data"]  # 同 key 回放不多建
    # 存在同项目引用后再跨项目：EXISTS 他项目仍拒（防 fetchone 只取同项目行）
    p3, task3 = task_with_rule(client)
    cross = submit(client, task3["id"], body)
    assert cross.status_code == 409 and cross.json()["error"]["details"].get("crossProject") is True


def test_file_submission_list_and_detail_no_500_char_count_none(client):
    """A 01:25①：file 行 text NULL → 列表/详情不 500，charCount 为 None 不伪 0；sourceMap 可回验解码文本。"""
    p, task = task_with_rule(client)
    asset = upl_script(client)
    created = submit(client, task["id"], {"inputType": "file", "fileId": asset["id"]}).json()["data"]
    assert created["charCount"] is None
    assert created["extractionStatus"] == "ready" and len(created["sourceMap"]) == 3
    listing = client.get(f"/api/v1/tasks/{task['id']}/scripts")
    assert listing.status_code == 200
    row = listing.json()["data"][0]
    assert row["charCount"] is None and row["inputType"] == "file" and row["isCurrent"] is True
    detail = client.get(f"/api/v1/script-versions/{created['scriptVersionId']}")
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["text"] is None and body["inputType"] == "file"
    # 解码文本回验：BOM 由严格解码消费，块偏移指向解码文本（不指向含 BOM 的原始字节）
    bom_body = "第一段BOM\n\n第二段"
    bom = client.post("/api/v1/files",
                      files={"file": ("bom.txt", io.BytesIO(b"\xef\xbb\xbf" + bom_body.encode("utf-8")), "text/plain")},
                      data={"purpose": "script"}, headers={"Idempotency-Key": K()}).json()["data"]
    p2, task2 = task_with_rule(client)
    r2 = submit(client, task2["id"], {"inputType": "file", "fileId": bom["id"]})
    assert r2.status_code == 201, r2.text
    for block in r2.json()["data"]["sourceMap"]:
        assert bom_body[block["startOffset"]:block["endOffset"]] == block["text"]


def test_archived_task_history_readable_but_new_write_blocked(client):
    """A 01:25②③：归档只读历史可见（GET 列表/详情 200），新写 409；归档后旧成功幂等重放仍可回。"""
    p, task = task_with_rule(client)
    key = K()
    body = {"inputType": "text", "text": "归档前版本"}
    first = submit(client, task["id"], body, key=key)
    assert first.status_code == 201
    vid = first.json()["data"]["scriptVersionId"]
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE creator_tasks SET archived_at='2026-01-01T00:00:00Z' WHERE id=?", (task["id"],))
        db.commit()
    listing = client.get(f"/api/v1/tasks/{task['id']}/scripts")
    assert listing.status_code == 200 and listing.json()["data"][0]["scriptVersionId"] == vid
    detail = client.get(f"/api/v1/script-versions/{vid}")
    assert detail.status_code == 200 and detail.json()["data"]["isCurrent"] is True
    blocked = submit(client, task["id"], {"inputType": "text", "text": "归档后新写"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "TASK_ARCHIVED"
    replay = submit(client, task["id"], body, key=key)  # 归档前旧成功重放不被挡（顺序：回放先于归档检查）
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM script_versions").fetchone()[0] == 1


def test_paste_offsets_stable_for_crlf_and_bom_text(client):
    """A 01:25④：粘贴偏移直接定义在所存原文上（含 CRLF），GET 详情 text[start:end] 精确回验。"""
    p, task = task_with_rule(client)
    raw = "第一行含尾随空白   \r\n第二行\r\n\r\n尾段"
    created = submit(client, task["id"], {"inputType": "text", "text": raw}).json()["data"]
    assert created["extractionStatus"] == "ready"
    stored = client.get(f"/api/v1/script-versions/{created['scriptVersionId']}").json()["data"]
    for block in stored["sourceMap"]:
        assert stored["text"][block["startOffset"]:block["endOffset"]] == block["text"]
    assert stored["text"] == raw  # 原文完整保留（含 \r\n 与尾随空白）


def test_unverified_formats_501_without_fabricated_source(client):
    """t-38 起 docx/xlsx 已真实接入；旧 OLE .doc 仍需系统转换器 → 明确 501，且零版本零事件。"""
    p, task = task_with_rule(client)
    doc = client.post("/api/v1/files",
                      files={"file": ("s.doc", io.BytesIO(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 40),
                                      "application/octet-stream")},
                      data={"purpose": "script"}, headers={"Idempotency-Key": K()}).json()["data"]
    r = submit(client, task["id"], {"inputType": "file", "fileId": doc["id"]})
    assert r.status_code == 501 and r.json()["error"]["code"] == "MODULE_NOT_IMPLEMENTED"
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM script_versions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events").fetchone()[0] == 0


def test_corrupt_docx_is_invalid_not_fabricated(client):
    """损坏的 docx：走真实解析失败 → 建 invalid 版本（不 501 也不伪造 ready/段落）。"""
    p, task = task_with_rule(client)
    bad = client.post("/api/v1/files",
                      files={"file": ("bad.docx", io.BytesIO(b"PK\x03\x04" + b"\x00" * 40), "application/octet-stream")},
                      data={"purpose": "script"}, headers={"Idempotency-Key": K()}).json()["data"]
    r = submit(client, task["id"], {"inputType": "file", "fileId": bad["id"]})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["extractionStatus"] == "invalid"
    assert data["sourceMap"] == []  # 不伪造段落


def test_real_xlsx_upload_lists_and_details_with_parse_meta(client):
    """t-38 最短链路：真实 XLSX 上传 → 创建版本 → GET 列表/详情都带 parseMeta 与 location。

    覆盖 A 关切的"定位与 warnings 必须进 ScriptVersionDetail 持久数据"（不只在创建响应），
    以及"有图未纳入 → coverage=partial，不得因 extractionStatus=ready 就被当作完整"。
    """
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "视频脚本"
    ws.append(["时长", "场景", "画面内容", "台词/画外音", "画面参考"])
    ws.append(["6S", "客厅", "妈妈念小说", "女儿：今天很不一样啊", ""])
    buf = io.BytesIO()
    wb.save(buf)
    payload = buf.getvalue()
    # 注入一张媒体图（模拟真实工作簿内嵌图）
    src = zipfile.ZipFile(io.BytesIO(payload))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("xl/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    real_xlsx = out.getvalue()

    p, task = task_with_rule(client)
    asset = client.post("/api/v1/files",
                        files={"file": ("s.xlsx", io.BytesIO(real_xlsx),
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                        data={"purpose": "script"}, headers={"Idempotency-Key": K()}).json()["data"]
    created = submit(client, task["id"], {"inputType": "file", "fileId": asset["id"]})
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    assert body["extractionStatus"] == "ready"           # 文本层就绪
    assert body["parseMeta"]["coverage"] == "partial"    # 但有图未纳入 → 不完整
    assert body["parseMeta"]["partial"] is True
    assert body["parseMeta"]["uncoveredMedia"]
    locs = [b.get("location") for b in body["sourceMap"]]
    assert "视频脚本!D1" in locs and "视频脚本!D2" in locs  # 口播列精确到 D
    assert all(loc for loc in locs)                       # 结构化块都有 location

    # 列表不带 parseMeta（摘要），详情必须带——且来自持久列
    listing = client.get(f"/api/v1/tasks/{task['id']}/scripts").json()["data"]
    assert listing[0]["extractionStatus"] == "ready"
    detail = client.get(f"/api/v1/script-versions/{body['scriptVersionId']}").json()["data"]
    assert detail["parseMeta"]["coverage"] == "partial"
    assert detail["parseMeta"]["uncoveredMedia"] == body["parseMeta"]["uncoveredMedia"]
    assert [b["location"] for b in detail["sourceMap"]] == locs


def test_invalid_parse_still_consumes_ordinal_and_invalidates(client):
    """通过输入校验但解析失败=真实提交：建 invalid 不可变版本、占 ordinal、同样失效。"""
    p, task = task_with_rule(client)
    submit(client, task["id"], {"inputType": "text", "text": "第一版正常"})
    raw = "乱码字节流"
    bad_asset = client.post("/api/v1/files", files={"file": ("bad.txt", io.BytesIO(raw.encode("utf-16-le")), "text/plain")},
                            data={"purpose": "script"}, headers={"Idempotency-Key": K()}).json()["data"]
    r = submit(client, task["id"], {"inputType": "file", "fileId": bad_asset["id"]})
    assert r.status_code == 201, r.text
    created = r.json()["data"]
    assert created["ordinal"] == 2 and created["extractionStatus"] == "invalid"
    assert created["error"]["code"] == "EXTRACTION_FAILED"
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events WHERE kind='new_script_version'").fetchone()[0] == 2


def test_idempotent_replay_and_concurrent_ordinal_uniqueness(client):
    p, task = task_with_rule(client)
    key = K()
    body = {"inputType": "text", "text": "幂等脚本正文"}
    first = submit(client, task["id"], body, key=key)
    assert first.status_code == 201
    replay = submit(client, task["id"], body, key=key)
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    assert replay.json()["requestId"] != first.json()["requestId"]
    conflict = submit(client, task["id"], {"inputType": "text", "text": "同键不同内容"}, key=key)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    p3, task3 = task_with_rule(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(submit, client, task3["id"], {"inputType": "text", "text": f"并发第{i}版"}) for i in range(4)]
        responses = [f.result() for f in futures]
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
    ordinals = sorted(r.json()["data"]["ordinal"] for r in responses)
    assert ordinals == [1, 2, 3, 4]  # 并发唯一序号
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(DISTINCT ordinal) FROM script_versions WHERE task_id=?",
                          (task3["id"],)).fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events WHERE task_id=? AND kind='new_script_version'",
                          (task3["id"],)).fetchone()[0] == 4


def test_archived_and_cross_mode_gates(client):
    p, task = task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE creator_tasks SET archived_at='2026-01-01T00:00:00Z' WHERE id=?", (task["id"],))
        db.commit()
    r = submit(client, task["id"], {"inputType": "text", "text": "归档后提交"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "TASK_ARCHIVED"
    p2, task2 = task_with_rule(client)
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE projects SET mode='real' WHERE id=?", (p2["id"],))
        db.commit()
    assert submit(client, task2["id"], {"inputType": "text", "text": "跨模式"}).status_code == 404
    assert client.get(f"/api/v1/tasks/{task2['id']}/scripts").status_code == 404
    detail = client.get("/api/v1/script-versions/does-not-exist")
    assert detail.status_code == 404
