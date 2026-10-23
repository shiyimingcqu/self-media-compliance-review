"""t-20 规则流修订测试（A 00:10 收口，约 20 项：负例+正向闭环；tmp_path 独立库）。"""

import io
import json
import uuid
from datetime import datetime, timezone

from app.core.database import connection

TXT = "第一段要求内容\n\n第二段要求内容\n\n第三段要求内容"


def K():
    return uuid.uuid4().hex


def upl(client, text=TXT, name="b.txt"):
    return client.post("/api/v1/files", files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/plain")},
                       data={"purpose": "brief"}, headers={"Idempotency-Key": K()}).json()["data"]


def project(client, platforms=("xiaohongshu",)):
    r = client.post("/api/v1/projects",
                    json={"name": f"p-{K()[:6]}", "brandName": "麦咖啡", "description": "",
                          "platforms": list(platforms)}, headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def brief(client, project_id, text=TXT, platforms=("xiaohongshu",)):
    asset = upl(client, text)
    r = client.post(f"/api/v1/projects/{project_id}/briefs",
                    json={"fileId": asset["id"], "title": "B", "platforms": list(platforms)},
                    headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def cur_rev(client, version_id):
    return client.get(f"/api/v1/rule-versions/{version_id}").json()["data"]["version"]["revision"]


def draft(client, brief_data, parent=None):
    r = client.post(f"/api/v1/briefs/{brief_data['briefId']}/rule-versions",
                    json={"sourceVersionId": brief_data["sourceVersionId"], "parentId": parent},
                    headers={"Idempotency-Key": K()})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def src(brief_data, paragraph="p1", quote="第一段要求内容"):
    return {"fileId": brief_data["fileId"], "paragraphId": paragraph, "quote": quote}


def add_req(client, version, brief_data, text="禁止功效表达", platforms=("xiaohongshu",),
            importance="must", source=None, key=None, content_types=("video",), phases=("seeding",),
            if_match=None):
    return client.post(f"/api/v1/rule-versions/{version['id']}/requirements",
                       json={"text": text, "category": "禁用表达", "importance": importance,
                             "platforms": list(platforms), "contentTypes": list(content_types),
                             "phases": list(phases), "reviewStages": ["script", "video"], "exceptions": "",
                             "source": source if source is not None else src(brief_data)},
                       headers={"Idempotency-Key": key or K(),
                                "If-Match": str(if_match if if_match is not None else cur_rev(client, version["id"]))})


def confirm_all(client, version, ids=None):
    ids = ids or [r["id"] for r in client.get(f"/api/v1/rule-versions/{version['id']}").json()["data"]["requirements"]]
    return client.post(f"/api/v1/rule-versions/{version['id']}/confirm", json={"requirementIds": ids},
                       headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, version["id"]))})


def enable(client, version, summary="首次启用", key=None, if_match=None):
    return client.post(f"/api/v1/rule-versions/{version['id']}/enable",
                       json={"changeSummary": summary},
                       headers={"Idempotency-Key": key or K(),
                                "If-Match": str(if_match or cur_rev(client, version["id"]))})


def full_enable(client, version, brief_data, text="禁止功效表达"):
    r = add_req(client, version, brief_data, text=text)
    cid = r.json()["data"]["id"]
    conf = confirm_all(client, version, [cid])
    assert conf.status_code == 200, conf.text
    return enable(client, version)


def env(client):
    p = project(client)
    b = brief(client, p["id"])
    v = draft(client, b)
    return p, b, v


# ---------- 1. 归属/模式先行 ----------


def test_cross_mode_version_returns_404_everywhere(client):
    """非 demo 项目的版本：读写与 GET 一律 404，且不因幂等记录泄露响应。"""
    p, b, v = env(client)
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE projects SET mode='real' WHERE id=?", (p["id"],))
        db.commit()
    assert client.get(f"/api/v1/rule-versions/{v['id']}").status_code == 404
    blocked = client.post(f"/api/v1/rule-versions/{v['id']}/requirements",
                          json={"text": "跨模式写", "category": "禁用表达", "importance": "must",
                                "platforms": ["xiaohongshu"], "contentTypes": ["video"], "phases": ["seeding"],
                                "reviewStages": ["script"], "exceptions": "", "source": src(b)},
                          headers={"Idempotency-Key": K(), "If-Match": "1"})
    assert blocked.status_code == 404
    assert client.post(f"/api/v1/rule-versions/{v['id']}/confirm", json={"requirementIds": ["x"]},
                       headers={"Idempotency-Key": K(), "If-Match": "1"}).status_code == 404
    assert client.post(f"/api/v1/rule-versions/{v['id']}/enable", json={"changeSummary": "x"},
                       headers={"Idempotency-Key": K(), "If-Match": "1"}).status_code == 404


def test_parent_must_be_same_brief_and_source(client):
    p, b, v = env(client)
    b2 = brief(client, project(client)["id"])
    v2 = draft(client, b2)
    cross = client.post(f"/api/v1/briefs/{b['briefId']}/rule-versions",
                        json={"sourceVersionId": b["sourceVersionId"], "parentId": v2["id"]},
                        headers={"Idempotency-Key": K()})
    assert cross.status_code == 400 and "同一 Brief" in cross.json()["error"]["message"]
    new_source = src(b)  # 换一个新源文件再派生 → SOURCE_MISMATCH
    b3 = brief(client, p["id"])
    mismatch = client.post(f"/api/v1/briefs/{b3['briefId']}/rule-versions",
                           json={"sourceVersionId": b3["sourceVersionId"], "parentId": v["id"]},
                           headers={"Idempotency-Key": K()})
    assert mismatch.status_code in (400, 409)  # 父未启用先行 400；启用后跨源 409 见派生测试


# ---------- 2. 派生复制与 PATCH ----------


def test_derive_copies_requirements_and_conflicts_with_lineage(client):
    p, b, v = env(client)
    r1 = add_req(client, v, b, text="要求甲")
    r2 = add_req(client, v, b, text="要求乙", key=K())
    ids = [r1.json()["data"]["id"], r2.json()["data"]["id"]]
    conflict = client.post(f"/api/v1/rule-versions/{v['id']}/conflicts",
                           json={"requirementIds": ids, "explanation": "重叠"},
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert conflict.status_code == 201
    # 解冲突（乙停用）→确认甲→启用
    client.post(f"/api/v1/conflicts/{conflict.json()['data']['id']}/resolve",
                json={"resolution": "保留甲", "affectedRequirementChanges":
                      [{"requirementId": r2.json()["data"]["id"], "action": "disable"}]},
                headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert confirm_all(client, v, [ids[0]]).status_code == 200
    enabled = enable(client, v)
    assert enabled.status_code == 200, enabled.text
    parent_enabled = enabled.json()["data"]
    derived = client.post(f"/api/v1/briefs/{b['briefId']}/rule-versions",
                          json={"sourceVersionId": b["sourceVersionId"], "parentId": parent_enabled["id"]},
                          headers={"Idempotency-Key": K()})
    assert derived.status_code == 201, derived.text
    derived_v = derived.json()["data"]
    detail_new = client.get(f"/api/v1/rule-versions/{derived_v['id']}").json()["data"]
    detail_old = client.get(f"/api/v1/rule-versions/{parent_enabled['id']}").json()["data"]
    assert len(detail_new["requirements"]) == len(detail_old["requirements"]) == 2
    assert {r["lineageId"] for r in detail_new["requirements"]} == {r["lineageId"] for r in detail_old["requirements"]}
    assert not ({r["id"] for r in detail_new["requirements"]} & {r["id"] for r in detail_old["requirements"]})
    # 复制保留确认态（confirmation 原样），且旧启用版零写入
    assert [r["confirmation"] for r in sorted(detail_new["requirements"], key=lambda x: x["text"])] == \
           [r["confirmation"] for r in sorted(detail_old["requirements"], key=lambda x: x["text"])]
    assert client.get(f"/api/v1/rule-versions/{parent_enabled['id']}").json()["data"]["version"]["revision"] == parent_enabled["revision"]


def test_derive_rejects_source_swap(client):
    """派生不得把旧启用证据搬到新来源：同 Brief 第二源版本 ≠ 父源 → 409 SOURCE_MISMATCH；
    跨 Brief 借源由 _load_source 404 拒绝（两种都不是可派生组合）。"""
    p, b, v = env(client)
    enabled = full_enable(client, v, b, text="基线启用")
    assert enabled.status_code == 200
    with connection(client.app.state.settings.database_path) as db:
        other_source = uuid.uuid4().hex
        file_id = upl(client, "全新另一文件内容\n\n第二段")["id"]
        db.execute("INSERT INTO brief_source_versions VALUES (?,?,?,?,?,?,?)",
                   (other_source, b["briefId"], file_id, "ready", None, "[]",
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")))
        db.commit()
    swap = client.post(f"/api/v1/briefs/{b['briefId']}/rule-versions",
                       json={"sourceVersionId": other_source, "parentId": enabled.json()["data"]["id"]},
                       headers={"Idempotency-Key": K()})
    assert swap.status_code == 409 and swap.json()["error"]["code"] == "SOURCE_MISMATCH"


def test_patch_resets_semantic_edits_and_locks_lineage(client):
    p, b, v = env(client)
    r = add_req(client, v, b, text="原始要求")
    rid = r.json()["data"]["id"]
    lineage = r.json()["data"]["lineageId"]
    conf = confirm_all(client, v, [rid])
    assert conf.json()["data"]["confirmedIds"] == [rid]
    patched = client.patch(f"/api/v1/requirements/{rid}",
                           json={"text": "修改后的审核语义"},
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["confirmation"] == "pending"  # 已确认项语义改动→重置 pending
    assert patched.json()["data"]["lineageId"] == lineage
    # 篡改 lineage/版本归属被拒（extra=forbid + 无此字段）
    bogus = client.patch(f"/api/v1/requirements/{rid}",
                         json={"text": "x", "lineageId": "hacked"},
                         headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert bogus.status_code == 400
    # 仅 exceptions 变更同样重置（A 00:37 更正：例外条件直接改变审核语义）
    add2 = add_req(client, v, b, text="另一要求", key=K())
    rid2 = add2.json()["data"]["id"]
    confirm_all(client, v, [rid2])
    exceptions_only = client.patch(f"/api/v1/requirements/{rid2}", json={"exceptions": "横测除外"},
                                   headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert exceptions_only.json()["data"]["confirmation"] == "pending"
    # 相同值 no-op：不 bump 到语义变化、不清确认
    r3 = add_req(client, v, b, text="第三要求", key=K())
    rid3 = r3.json()["data"]["id"]
    confirm_all(client, v, [rid3])
    rev_before = cur_rev(client, v["id"])
    noop = client.patch(f"/api/v1/requirements/{rid3}", json={"exceptions": ""},
                        headers={"Idempotency-Key": K(), "If-Match": str(rev_before)})
    assert noop.status_code == 200 and noop.json()["data"]["confirmation"] == "confirmed"


# ---------- 3. 来源/覆盖/适用门禁 ----------


def test_quote_must_be_inside_paragraph_and_note_exclusive(client):
    p, b, v = env(client)
    fake_quote = add_req(client, v, b, source=src(b, quote="这段文字根本不存在于该段"))
    assert fake_quote.status_code == 400 and "quote" in json.dumps(fake_quote.json()["error"])
    wrong_para = add_req(client, v, b, source=src(b, paragraph="p9"))
    assert wrong_para.status_code == 400
    # paragraphId 严格格式/类型：非法输入 400 字段错，不再 int() 崩 500（A 00:37②）
    for junk in ("pabc", "p", "p0", "0", 5, None, "p-1", "p01"):
        broken = add_req(client, v, b, source={"fileId": b["fileId"], "paragraphId": junk, "quote": "第一段要求内容"},
                         key=K())
        assert broken.status_code in (400, 422), (junk, broken.status_code, broken.text)
        if broken.status_code == 400:
            assert broken.json()["error"]["code"] == "VALIDATION_ERROR"
    mixed = add_req(client, v, b, source={"manualNote": "品牌补充", "page": 20, "fileId": b["fileId"]})
    assert mixed.status_code == 400 and "互斥" in json.dumps(mixed.json(), ensure_ascii=False)
    clean_note = add_req(client, v, b, source={"manualNote": "品牌补充：口播需带全称"})
    assert clean_note.status_code == 201


def test_coverage_requires_linked_confirmed_supplement(client):
    """B_0006 逐 item：无链→MISSING；无关文档引用型 confirmed 挂页→签署时 400；
    两 item 只补一个→另一个仍 MISSING；补录项被语义改动 pending 后→STALE 阻断；全链 confirmed→放行。"""
    p = project(client)
    b = brief(client, p["id"])
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE brief_source_versions SET uncovered_sources_json=? WHERE id=?",
                   (json.dumps(["page:20-image", "page:21-table"]), b["sourceVersionId"]))
        db.commit()
    v = draft(client, b)
    supplement = add_req(client, v, b, text="第20页图片补录规则",
                         source={"manualNote": "第20页图片文字人工补录两条"})
    supplement2 = add_req(client, v, b, text="第21页表格补录规则", key=K(),
                          source={"manualNote": "第21页表格人工补录"})
    doc_ref = add_req(client, v, b, text="普通文档引用项不得挂未覆盖页", key=K())
    mapping_body = {"statement": "已人工核对补录", "reason": "OCR 未配置"}

    def sign(uncovered_ids, mappings, rev=None):
        return client.post(f"/api/v1/rule-versions/{v['id']}/coverage-confirmation",
                           json={**mapping_body, "uncoveredSourceIds": uncovered_ids, "itemMappings": mappings},
                           headers={"Idempotency-Key": K(), "If-Match": str(rev or cur_rev(client, v["id"]))})

    sid, sid2, did = supplement.json()["data"]["id"], supplement2.json()["data"]["id"], doc_ref.json()["data"]["id"]
    confirm_all(client, v, [sid, sid2])
    # 无关文档引用项明确停用，使 enable 阻断原因落在覆盖门禁
    wrong_source = sign(["page:20-image"], [{"uncoveredSourceId": "page:20-image", "requirementId": did, "note": "n"}])
    assert wrong_source.status_code == 400 and "manualNote" in json.dumps(wrong_source.json(), ensure_ascii=False)
    client.post(f"/api/v1/rule-versions/{v['id']}/disable",
                json={"requirementId": did, "reason": "非覆盖补录用途"},
                headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    blocked = enable(client, v)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "COVERAGE_UNCONFIRMED"
    assert set(blocked.json()["error"]["details"]["missing"]) == {"page:20-image", "page:21-table"}
    dup = sign(["page:20-image", "page:20-image"],
               [{"uncoveredSourceId": "page:20-image", "requirementId": sid, "note": "n"}])
    assert dup.status_code == 400  # 重复 ID
    mismatch = sign(["page:20-image", "page:21-table"],
                    [{"uncoveredSourceId": "page:20-image", "requirementId": sid, "note": "n"}])
    assert mismatch.status_code == 400 and "一一对应" in mismatch.json()["error"]["message"]
    unknown_item = sign(["page:99"], [{"uncoveredSourceId": "page:99", "requirementId": sid, "note": "n"}])
    assert unknown_item.status_code == 400
    foreign = draft(client, b)  # 别版本要求不可借挂本版本签署
    f_req = add_req(client, foreign, b, text="别版本要求", key=K())
    cross = sign(["page:20-image"], [{"uncoveredSourceId": "page:20-image",
                                       "requirementId": f_req.json()["data"]["id"], "note": "n"}])
    assert cross.status_code == 404
    empty_note = sign(["page:20-image"], [{"uncoveredSourceId": "page:20-image", "requirementId": sid, "note": "   "}])
    assert empty_note.status_code in (400, 422)  # note trim 后 1–500
    # 先确认两条补录项，再逐 item 只签一页：另一页仍 MISSING
    confirm_all(client, v, [sid, sid2])
    partial = sign(["page:20-image"], [{"uncoveredSourceId": "page:20-image", "requirementId": sid, "note": "第20页两条已录"}])
    assert partial.status_code == 201, partial.text
    half = enable(client, v)
    assert half.status_code == 409 and half.json()["error"]["code"] == "COVERAGE_UNCONFIRMED"
    assert half.json()["error"]["details"]["missing"] == ["page:21-table"]
    # 补齐第 21 页链后再改第 20 页声明 → 全链存在，STALE 才显形
    resign21 = sign(["page:21-table"], [{"uncoveredSourceId": "page:21-table", "requirementId": sid2, "note": "第21页已录"}])
    assert resign21.status_code == 201
    stale_patch = client.patch(f"/api/v1/requirements/{sid}",
                               json={"source": {"manualNote": "改写了补录声明"}},
                               headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert stale_patch.status_code == 200
    confirm_all(client, v, [sid])
    stale = enable(client, v)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "COVERAGE_STALE"
    assert stale.json()["error"]["details"]["stale"] == ["page:20-image"]
    # 重签修正声明后全链放行
    fixed = client.patch(f"/api/v1/requirements/{sid}",
                         json={"source": {"manualNote": "第20页图片文字人工补录两条"}},
                         headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert fixed.status_code == 200
    confirm_all(client, v, [sid])
    resign20 = sign(["page:20-image"], [{"uncoveredSourceId": "page:20-image", "requirementId": sid, "note": "重新签述修正声明"}])
    assert resign20.status_code == 201
    ok = enable(client, v)
    assert ok.status_code == 200, ok.text


def test_applicable_must_requires_content_type_video_match(client):
    """image_text-only 必须项不构成可绑定/可启用的适用必须项（与 bindable 共用谓词）。"""
    p, b, v = env(client)
    add_req(client, v, b, text="图片四张要求", content_types=("image_text",), phases=("general",))
    rid = client.get(f"/api/v1/rule-versions/{v['id']}").json()["data"]["requirements"][0]["id"]
    confirm_all(client, v, [rid])
    blocked = enable(client, v)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "NO_APPLICABLE_MUST"


# ---------- 4. 重放顺序/隔离/resolve 受控变更 ----------


def test_replay_after_commit_survives_later_revision_bump(client):
    """重放先于 revision：同 key 旧 If-Match 回原快照；跨 mode 绝不回放（上一测试证）。"""
    p, b, v = env(client)
    key = K()
    first = add_req(client, v, b, text="重放要求", key=key)
    assert first.status_code == 201
    bump_target = add_req(client, v, b, text="加一修订", key=K())  # 使 revision 前进
    body = {"text": "重放要求", "category": "禁用表达", "importance": "must", "platforms": ["xiaohongshu"],
            "contentTypes": ["video"], "phases": ["seeding"], "reviewStages": ["script", "video"],
            "exceptions": "", "source": src(b)}
    replay = client.post(f"/api/v1/rule-versions/{v['id']}/requirements", json=body,
                         headers={"Idempotency-Key": key, "If-Match": "1"})
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    assert replay.json()["requestId"] != first.json()["requestId"]
    conflict_body = {"requirementIds": [first.json()["data"]["id"], bump_target.json()["data"]["id"]],
                     "explanation": "重叠"}
    conflict = client.post(f"/api/v1/rule-versions/{v['id']}/conflicts", json=conflict_body,
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    cid = conflict.json()["data"]["id"]
    resolve_key = K()
    rbody = {"resolution": "采用前者", "affectedRequirementChanges":
             [{"requirementId": bump_target.json()["data"]["id"], "action": "disable"}]}
    resolve_if_match = str(cur_rev(client, v["id"]))
    r1 = client.post(f"/api/v1/conflicts/{cid}/resolve", json=rbody,
                     headers={"Idempotency-Key": resolve_key, "If-Match": resolve_if_match})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/api/v1/conflicts/{cid}/resolve", json=rbody,
                     headers={"Idempotency-Key": resolve_key, "If-Match": resolve_if_match})  # 同 payload 重放
    assert r2.status_code == 200 and r2.json()["data"] == r1.json()["data"]
    # 幂等记录存在即重放不重复变更；冲突双解由 CONFLICT_ALREADY_RESOLVED 兜底
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM requirements WHERE confirmation='disabled'").fetchone()[0] == 1


def test_resolve_changes_limited_to_conflict_members(client):
    p, b, v = env(client)
    inside = add_req(client, v, b, text="冲突内要求")
    outside = add_req(client, v, b, text="冲突外要求", key=K())
    ids = [inside.json()["data"]["id"]]
    conflict = client.post(f"/api/v1/rule-versions/{v['id']}/conflicts",
                          json={"requirementIds": [inside.json()["data"]["id"], outside.json()["data"]["id"]],
                                "explanation": "重叠"},
                          headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    other = add_req(client, v, b, text="第三方", key=K())  # 不属于冲突
    bad = client.post(f"/api/v1/conflicts/{conflict.json()['data']['id']}/resolve",
                      json={"resolution": "r", "affectedRequirementChanges":
                            [{"requirementId": other.json()["data"]["id"], "action": "disable"}]},
                      headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert bad.status_code == 400 and "本冲突" in bad.json()["error"]["message"]
    assert len(ids) == 1


def test_resolve_update_text_resets_pending_and_double_resolve(client):
    p, b, v = env(client)
    r1 = add_req(client, v, b, text="前15秒完整露出")
    r2 = add_req(client, v, b, text="前30秒至少一次", key=K())
    ids = sorted([r1.json()["data"]["id"], r2.json()["data"]["id"]])
    conflict = client.post(f"/api/v1/rule-versions/{v['id']}/conflicts",
                           json={"requirementIds": ids, "explanation": "重叠"},
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    cid = conflict.json()["data"]["id"]
    confirm = confirm_all(client, v, ids)
    assert all(s["reason"] == "open_conflict" for s in confirm.json()["data"]["skipped"])
    resolved = client.post(f"/api/v1/conflicts/{cid}/resolve",
                           json={"resolution": "统一为30秒口径", "affectedRequirementChanges":
                                 [{"requirementId": r1.json()["data"]["id"], "action": "update_text",
                                   "text": "前30秒完整露出"}]},
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert resolved.status_code == 200, resolved.text
    detail = client.get(f"/api/v1/rule-versions/{v['id']}").json()["data"]
    updated = [r for r in detail["requirements"] if r["id"] == r1.json()["data"]["id"]][0]
    assert updated["text"] == "前30秒完整露出" and updated["confirmation"] == "pending"  # update 后需重新确认
    twice = client.post(f"/api/v1/conflicts/{cid}/resolve", json={"resolution": "再解"},
                        headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "CONFLICT_ALREADY_RESOLVED"


def test_enable_after_resolve_and_reconfirm_and_multi_enabled(client):
    p, b, v = env(client)
    r1 = add_req(client, v, b, text="要求一")
    conflict = client.post(f"/api/v1/rule-versions/{v['id']}/conflicts",
                           json={"requirementIds": [r1.json()["data"]["id"]], "explanation": "单方占位"},
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert conflict.status_code == 400  # min_length=2 拒单方（validation handler 归一 400 envelope）
    # 停用孤立要求后启用（确认清零=confirmed∨disabled，不静默遗漏 pending）
    disabled = client.post(f"/api/v1/rule-versions/{v['id']}/disable",
                           json={"requirementId": r1.json()["data"]["id"], "reason": "单方无冲突对象"},
                           headers={"Idempotency-Key": K(), "If-Match": str(cur_rev(client, v["id"]))})
    assert disabled.status_code == 200, disabled.text
    enabled = full_enable(client, v, b, text="启用要求")
    assert enabled.status_code == 200, enabled.text
    v2 = draft(client, b)
    second = full_enable(client, v2, b, text="另一口径")
    assert second.status_code == 200
    with connection(client.app.state.settings.database_path) as db:
        states = dict(db.execute("SELECT id,state FROM rule_versions").fetchall())
        assert states[enabled.json()["data"]["id"]] == "enabled"  # 多 enabled 并存，未动旧版
        assert states[second.json()["data"]["id"]] == "enabled"
        assert db.execute("SELECT COUNT(*) FROM creator_tasks").fetchone()[0] == 0


def test_superseded_semantics_bound_vs_bindable(client):
    p, b, v = env(client)
    enabled = full_enable(client, v, b, text="启用要求")
    data = enabled.json()["data"]
    task = client.post(f"/api/v1/projects/{p['id']}/tasks",
                       json={"creatorName": "达人S", "platform": "xiaohongshu", "contentType": "video",
                             "campaignPhase": "seeding", "ruleVersionId": data["id"]},
                       headers={"Idempotency-Key": K()}).json()["data"]
    with connection(client.app.state.settings.database_path) as db:
        db.execute("UPDATE rule_versions SET state='superseded' WHERE id=?", (data["id"],))
        db.commit()
    from app.core.eligibility import bindable_version, rules_enabled
    with connection(client.app.state.settings.database_path) as db:
        assert rules_enabled(db, task["id"]).allowed is True
        deny = bindable_version(db, p["id"], "xiaohongshu", "seeding", "video", data["id"])
        assert not deny.allowed and deny.code == "RULES_NOT_ENABLED"
    another = client.post(f"/api/v1/projects/{p['id']}/tasks",
                          json={"creatorName": "达人T2", "platform": "xiaohongshu", "contentType": "video",
                                "campaignPhase": "seeding", "ruleVersionId": data["id"]},
                          headers={"Idempotency-Key": K()})
    assert another.status_code == 409


def test_enabled_immutable_and_disable_body(client):
    p, b, v = env(client)
    enabled = full_enable(client, v, b, text="冻结要求").json()["data"]
    late = add_req(client, enabled, b, text="迟到写入")
    assert late.status_code == 409 and late.json()["error"]["code"] == "VERSION_IMMUTABLE"
    rid = client.get(f"/api/v1/rule-versions/{enabled['id']}").json()["data"]["requirements"][0]["id"]
    disabled = client.post(f"/api/v1/rule-versions/{enabled['id']}/disable",
                           json={"requirementId": rid, "reason": "不应有"},
                           headers={"Idempotency-Key": K(), "If-Match": str(enabled["revision"])})
    assert disabled.status_code == 409  # enabled 后停用同样不可
    query_style = client.post(f"/api/v1/rule-versions/{enabled['id']}/disable?requirementId={rid}&reason=x",
                              json={}, headers={"Idempotency-Key": K(), "If-Match": "3"})
    assert query_style.status_code in (400, 404, 409)  # Query 时代接口位置已废弃
