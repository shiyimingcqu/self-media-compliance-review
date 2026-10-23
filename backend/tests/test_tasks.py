"""第二增量后端最小集测试：夹具只写 tmp_path 独立库，不触碰 demo 库。"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from app.core.database import connection, migrate


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def env(client, tmp_path):
    """client 的独立库（conftest tmp_path Settings）+ 手工种子夹具。"""
    settings = client.app.state.settings
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        project_id = uuid.uuid4().hex
        db.execute("INSERT INTO brands VALUES (?,?,?)", (uuid.uuid4().hex, "麦咖啡", "麦咖啡".casefold()))
        brand_id = db.execute("SELECT id FROM brands LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO projects (id,name,normalized_name,brand_id,description,platforms_json,mode,"
                   "created_at,updated_at) VALUES (?,?,?,?,?,'[\"douyin\",\"xiaohongshu\"]','demo',?,?)",
                   (project_id, "t15-project", "t15-project", brand_id, "", stamp(), stamp()))
        brief_id = uuid.uuid4().hex
        file_id = uuid.uuid4().hex
        source_id = uuid.uuid4().hex
        db.execute("INSERT INTO file_assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (file_id, "b.pdf", "pdf", "application/pdf", 10, "0" * 64, f"x/{file_id}.pdf", "brief",
                    "verified", "unverified", "demo", stamp()))
        db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) VALUES (?,?,?,NULL,?)",
                   (brief_id, project_id, "小红书 Brief", stamp()))
        db.execute("INSERT INTO brief_source_versions VALUES (?,?,?,?,?,?,?)",
                   (source_id, brief_id, file_id, "ready", 2, "[]", stamp()))
        db.commit()
    return {"client": client, "settings": settings, "project_id": project_id,
            "brief_id": brief_id, "source_id": source_id}


def seed_version(env, *, state="enabled", platform="xiaohongshu", phase="seeding",
                 conflict=False, importance="must", confirmation="confirmed") -> str:
    settings = env["settings"]
    version_id = uuid.uuid4().hex
    requirement_id = uuid.uuid4().hex
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,state,revision,created_at) "
                   "VALUES (?,?,?,?,?,1,?)",
                   (version_id, env["brief_id"], env["source_id"], "v-" + version_id[:6], state, stamp()))
        db.execute("INSERT INTO requirements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (requirement_id, version_id, uuid.uuid4().hex, "禁止功效表达", "禁用表达", importance,
                    json.dumps([platform]), json.dumps(["video"]), json.dumps([phase]),
                    json.dumps(["script", "video"]), "", "{}", confirmation))
        if conflict:
            db.execute("INSERT INTO rule_conflicts VALUES (?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, version_id, json.dumps([requirement_id]), "15s/30s 重叠", "open", None, None))
        db.commit()
    return version_id


def create_task(env, platform="xiaohongshu", phase="seeding", version=None, key=None):
    client = env["client"]
    version = version or seed_version(env)
    response = client.post(f"/api/v1/projects/{env['project_id']}/tasks",
                           json={"creatorName": "达人A", "platform": platform, "contentType": "video",
                                 "campaignPhase": phase, "ruleVersionId": version},
                           headers={"Idempotency-Key": key or uuid.uuid4().hex})
    return response


def test_create_task_requires_enabled_compatible_version(env):
    # 无任何 enabled 数据（业务未接入）时恒明确阻断——不伪启用
    missing = create_task(env, version=seed_version(env, state="pending_confirmation"))
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "RULES_NOT_ENABLED"
    ok = create_task(env)
    assert ok.status_code == 201, ok.text
    task = ok.json()["data"]
    assert task["stage"] == "requirements" and task["revision"] == 1 and task["mode"] == "demo"


def test_binding_matrix_douyin_rule_rejects_xiaohongshu_task(env):
    douyin_rule = seed_version(env, platform="douyin")
    rejected = create_task(env, platform="xiaohongshu", version=douyin_rule)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "RULE_SCOPE_MISMATCH"
    general = seed_version(env, platform="xiaohongshu", phase="general")
    assert create_task(env, platform="xiaohongshu", phase="warmup", version=general).status_code == 201


def test_conflict_or_no_confirmed_must_blocks(env):
    conflicted = seed_version(env, conflict=True)
    r = create_task(env, version=conflicted)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RULES_NOT_ENABLED"
    unconfirmed = seed_version(env, confirmation="pending")
    r2 = create_task(env, version=unconfirmed)
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "RULE_SCOPE_MISMATCH"
    suggested_only = seed_version(env, importance="suggested")
    r3 = create_task(env, version=suggested_only)
    assert r3.status_code == 409  # 仅建议项不构成适用必须项


def test_create_idempotent_duplicate_ack_and_no_extra_rows(env):
    key = uuid.uuid4().hex
    version = seed_version(env)
    first = create_task(env, key=key, version=version)
    assert first.status_code == 201
    replay = create_task(env, key=key, version=version)
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    dup = create_task(env, version=version)  # 同达人同平台（新 key，未确认）
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE_SUSPECTED"
    ack_key = uuid.uuid4().hex
    ack = env["client"].post(f"/api/v1/projects/{env['project_id']}/tasks",
                             json={"creatorName": "达人A", "platform": "xiaohongshu", "contentType": "video",
                                   "campaignPhase": "seeding", "ruleVersionId": version,
                                   "acknowledgeDuplicate": True},
                             headers={"Idempotency-Key": ack_key})
    assert ack.status_code == 201, ack.text  # 确认后创建独立任务
    ack_replay = env["client"].post(f"/api/v1/projects/{env['project_id']}/tasks",
                                    json={"creatorName": "达人A", "platform": "xiaohongshu", "contentType": "video",
                                          "campaignPhase": "seeding", "ruleVersionId": version,
                                          "acknowledgeDuplicate": True},
                                    headers={"Idempotency-Key": ack_key})
    assert ack_replay.status_code == 201 and ack_replay.json()["data"] == ack.json()["data"]
    with connection(env["settings"].database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM creator_tasks").fetchone()[0] == 2  # 重试不多建


def test_patch_idempotent_replay_conflict_and_key_required(env):
    task = create_task(env).json()["data"]
    key = uuid.uuid4().hex
    no_key = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人B"}, headers={"If-Match": "1"})
    assert no_key.status_code == 400  # 缺 Idempotency-Key
    good = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人B"},
                               headers={"If-Match": "1", "Idempotency-Key": key})
    assert good.status_code == 200 and good.json()["data"]["revision"] == 2
    replay = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人B"},
                                 headers={"If-Match": "1", "Idempotency-Key": key})
    # 重放必须在新写 revision 检查前命中：旧 If-Match=1 仍回原 200 快照（revision 已=2）
    assert replay.status_code == 200 and replay.json()["data"] == good.json()["data"]
    conflict = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人C"},
                                   headers={"If-Match": "2", "Idempotency-Key": key})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with connection(env["settings"].database_path) as db:
        assert db.execute("SELECT revision FROM creator_tasks").fetchone()[0] == 2  # 回放/冲突不再改


def test_phase_change_emits_invalidation_and_resets_stage(env):
    task = create_task(env).json()["data"]  # seeding
    with connection(env["settings"].database_path) as db:  # 预置旧资格阶段，验证回退
        db.execute("UPDATE creator_tasks SET stage='completed' WHERE id=?", (task["id"],))
    name_only = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "改名"},
                                    headers={"If-Match": "1", "Idempotency-Key": uuid.uuid4().hex})
    assert name_only.status_code == 200
    assert name_only.json()["data"]["stage"] == "completed"  # 仅名称不失效
    with connection(env["settings"].database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events").fetchone()[0] == 0
    phase = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"campaignPhase": "warmup"},
                                headers={"If-Match": "2", "Idempotency-Key": uuid.uuid4().hex})
    # warmup 与 seeding-only 规则不兼容 → 409（不换阶段），改绑 general 规则后再验回退
    assert phase.status_code == 409
    general = seed_version(env, phase="general")
    rebind = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                                 json={"ruleVersionId": general, "reason": "通用口径", "acknowledgeInvalidation": True},
                                 headers={"If-Match": "2", "Idempotency-Key": uuid.uuid4().hex})
    assert rebind.status_code == 200 and rebind.json()["data"]["stage"] == "requirements"  # 旧 completed 不保留
    changed = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"campaignPhase": "warmup"},
                                  headers={"If-Match": "3", "Idempotency-Key": uuid.uuid4().hex})
    assert changed.status_code == 200 and changed.json()["data"]["stage"] == "requirements"
    events = env["client"].get(f"/api/v1/tasks/{task['id']}/invalidations").json()["data"]
    kinds = [e["kind"] for e in events]
    assert "rebind" in kinds and "phase_change" in kinds  # 同一 invalidation 流


def test_rebind_same_rule_is_noop_and_idempotent(env):
    version = seed_version(env)
    task = create_task(env, version=version).json()["data"]
    noop_key = uuid.uuid4().hex
    noop = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                              json={"ruleVersionId": version, "reason": "同规则", "acknowledgeInvalidation": True},
                              headers={"If-Match": "1", "Idempotency-Key": noop_key})
    assert noop.status_code == 200
    assert noop.json()["data"]["revision"] == 1  # 不 bump
    assert noop.json()["data"] == task  # 原资源原样（Envelope[CreatorTask] 不加临时字段）
    with connection(env["settings"].database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM rule_binding_history").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM idempotency_records WHERE key=?", (noop_key,)).fetchone()[0] == 1
    replay = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                                json={"ruleVersionId": version, "reason": "同规则", "acknowledgeInvalidation": True},
                                headers={"If-Match": "1", "Idempotency-Key": noop_key})
    assert replay.status_code == 200 and replay.json()["data"] == task


def test_rebind_real_idempotency_and_archived_gates(env):
    old, new = seed_version(env), seed_version(env)
    task = create_task(env, version=old).json()["data"]
    key = uuid.uuid4().hex
    first = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                               json={"ruleVersionId": new, "reason": "口径更新", "acknowledgeInvalidation": True},
                               headers={"If-Match": "1", "Idempotency-Key": key})
    assert first.status_code == 200
    replay = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                                json={"ruleVersionId": new, "reason": "口径更新", "acknowledgeInvalidation": True},
                                headers={"If-Match": "1", "Idempotency-Key": key})
    assert replay.status_code == 200 and replay.json()["data"] == first.json()["data"]  # 旧 If-Match 重放不被 revision 挡
    with connection(env["settings"].database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM task_invalidation_events WHERE kind='rebind'").fetchone()[0] == 1
    conflict = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                                  json={"ruleVersionId": new, "reason": "换理由", "acknowledgeInvalidation": True},
                                  headers={"If-Match": "2", "Idempotency-Key": key})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    # 归档项目禁止新写，但旧成功仍回放
    with connection(env["settings"].database_path) as db:
        db.execute("UPDATE projects SET status='archived' WHERE id=?", (env["project_id"],))
    new_write = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                                   json={"ruleVersionId": seed_version(env), "reason": "归档后", "acknowledgeInvalidation": True},
                                   headers={"If-Match": "2", "Idempotency-Key": uuid.uuid4().hex})
    assert new_write.status_code == 409 and new_write.json()["error"]["code"] == "TASK_ARCHIVED"
    archived_replay = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                                         json={"ruleVersionId": new, "reason": "口径更新", "acknowledgeInvalidation": True},
                                         headers={"If-Match": "1", "Idempotency-Key": key})
    assert archived_replay.status_code == 200 and archived_replay.json()["data"] == first.json()["data"]
    patch_after_archive = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "x"},
                                              headers={"If-Match": "2", "Idempotency-Key": uuid.uuid4().hex})
    assert patch_after_archive.status_code == 409


def test_duplicate_rejected_without_ack_creates_nothing(env):
    version = seed_version(env)
    create_task(env, version=version)
    dup = create_task(env, version=version)
    assert dup.status_code == 409
    with connection(env["settings"].database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM creator_tasks").fetchone()[0] == 1


def test_patch_requires_if_match_and_revision_conflict(env):
    task = create_task(env).json()["data"]
    missing = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人B"},
                                  headers={"Idempotency-Key": uuid.uuid4().hex})
    assert missing.status_code == 428
    stale = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人B"},
                                headers={"If-Match": "99", "Idempotency-Key": uuid.uuid4().hex})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
    good = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"creatorName": "达人B"},
                               headers={"If-Match": "1", "Idempotency-Key": uuid.uuid4().hex})
    assert good.status_code == 200 and good.json()["data"]["revision"] == 2
    # 阶段变更重新校验绑定
    phase_change = env["client"].patch(f"/api/v1/tasks/{task['id']}", json={"campaignPhase": "warmup"},
                                       headers={"If-Match": "2", "Idempotency-Key": uuid.uuid4().hex})
    # 原绑定 seeding-only：warmup 无适用必须项 → 拒绝
    assert phase_change.status_code == 409


def test_rebind_history_shape(env):
    old = seed_version(env)
    task = create_task(env, version=old).json()["data"]
    new = seed_version(env)
    unack = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                               json={"ruleVersionId": new, "reason": "口径更新", "acknowledgeInvalidation": False},
                               headers={"If-Match": "1", "Idempotency-Key": uuid.uuid4().hex})
    assert unack.status_code == 400
    done = env["client"].post(f"/api/v1/tasks/{task['id']}/rebind",
                              json={"ruleVersionId": new, "reason": "口径更新", "acknowledgeInvalidation": True},
                              headers={"If-Match": "1", "Idempotency-Key": uuid.uuid4().hex})
    assert done.status_code == 200, done.text
    history = env["client"].get(f"/api/v1/tasks/{task['id']}/history").json()["data"]
    assert len(history["bindings"]) == 1 and history["jobs"] == [] and history["humanReviews"] == []


def test_list_and_detail_eligibility(env):
    task = create_task(env).json()["data"]
    listing = env["client"].get(f"/api/v1/projects/{env['project_id']}/tasks")
    assert listing.status_code == 200  # 501 桩已删，真实实现接管
    assert listing.json()["total"] == 1
    detail = env["client"].get(f"/api/v1/tasks/{task['id']}").json()["data"]
    assert detail["eligibility"]["allowed"] is True
    # 规则被换向后 eligibility 走真实查询
    other = seed_version(env, platform="douyin")
    bad_task = create_task(env, platform="douyin", phase="seeding", version=other)
    # douyin 项目平台合法；general? seeding matches phase list ['douyin'?]—other rule platform=douyin phase=seeding
    assert bad_task.status_code == 201
    not_found = env["client"].get("/api/v1/tasks/does-not-exist")
    assert not_found.status_code == 404
