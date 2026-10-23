"""D t-34：人工复核持久化测试（tmp_path 独立库；synthetic job 为 fixture 数据非产品声明）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import connection, migrate
from app.main import create_app
from app.modules.human_review import repository as repo
from app.modules.human_review.router_impl import router as human_review_wip_router
from app.modules.human_review.schemas_impl import (
    DecisionBody, DraftBody, IssueDecisionInput, ManualIssueInput,
)

SNAP = {"mode": "demo", "ruleVersionId": "rv-1", "scriptVersionId": None,
        "videoVersionId": None, "modelConfigId": "mc-1", "modelConfigRevision": 1,
        "capabilityReportId": None}
RESULT = {"jobId": "", "snapshot": SNAP, "conclusion": "needs_confirmation",
          "integrity": {"rulesComplete": True, "video": "complete", "audio": "complete"},
          "results": [], "issues": [
              {"id": "issue-1", "requirementId": "r1", "category": "spoken",
               "importance": "must", "description": "功效表达", "suggestion": "",
               "evidence": [], "origin": "ai"}]}


@pytest.fixture
def client(tmp_path):
    """测试专用挂载：WIP router 在 fixture 内注册（main.py 主链保持隔离）。

    这是测试唯一激活点——不修改 app/main.py、不进入 OpenAPI 发布面。
    先移除主链上的原 501 路由，避免同路径被抢先匹配（原 router 仍原样保留）。
    """
    app = create_app(Settings(data_dir=tmp_path))
    app.router.routes = [
        r for r in app.router.routes
        if getattr(r, "path", "") not in (
            "/api/v1/human-reviews", "/api/v1/human-reviews/{review_id}",
            "/api/v1/jobs/{job_id}/report")
    ]
    app.include_router(human_review_wip_router, prefix="/api/v1")
    with TestClient(app) as c:
        yield c


def seed(client: TestClient, tmp_path: Path, tag: str = "a") -> dict:
    """fixture：真实 API 建项目 + synthetic brief/source/file/任务/succeeded job/result。

    注：synthetic job 仅为本测试 fixture 数据，不代表产品已产生真实审核结果。
    同测试内多次调用用不同 tag 避免 UNIQUE 冲突；返回 ID 集合供断言引用。
    """
    r = client.post("/api/v1/projects", json={
        "name": f"复核项目-{tag}", "brandName": f"品牌-{tag}", "platforms": ["douyin"]},
        headers={"Idempotency-Key": f"seed-project-{tag}"})
    assert r.status_code == 201, r.text
    project_id = r.json()["data"]["id"]
    db_path = tmp_path / "workbench.sqlite3"
    tid, rvid = f"task-{tag}", f"rv-{tag}"
    with connection(db_path) as db:
        db.execute("INSERT INTO file_assets (id,original_name,extension,mime,byte_size,"
                   "sha256,storage_key,purpose,signature_state,media_check,mode,created_at) "
                   "VALUES (?,?,?,?,3,?,?,'brief','unverifiable','unverified','demo','2026-01-01')",
                   (f"f-{tag}", "b.txt", "txt", "text/plain", "0" * 64, f"k-{tag}"))
        db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) "
                   "VALUES (?,?,'B',NULL,'2026-01-01')", (f"b-{tag}", project_id))
        db.execute("INSERT INTO brief_source_versions (id,brief_id,file_id,extraction_status,"
                   "page_count,uncovered_sources_json,created_at) "
                   "VALUES (?,?,?,'ready',1,'[]','2026-01-01')",
                   (f"sv-{tag}", f"b-{tag}", f"f-{tag}"))
        db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,"
                   "state,parent_id,revision,created_at) VALUES (?,?,?,'v1','enabled',NULL,1,"
                   "'2026-01-01')", (rvid, f"b-{tag}", f"sv-{tag}"))
        db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,"
                   "content_type,campaign_phase,rule_version_id,stage,revision,archived_at,"
                   "created_at,updated_at) VALUES (?,?,?,'douyin','video','general',?,"
                   "'script',1,NULL,'2026-01-01','2026-01-01')",
                   (tid, project_id, "测试达人", rvid))
        job_id = uuid4().hex
        snap = {**SNAP, "ruleVersionId": rvid}
        result = {**RESULT, "jobId": job_id, "snapshot": snap}
        db.execute("INSERT INTO review_jobs (id,kind,task_id,source_version_id,script_version_id,"
                   "video_file_id,rule_version_id,mode,status,snapshot_json,input_json,"
                   "submission_state,attempt,retry_of,created_at,updated_at) VALUES "
                   "(?, 'script',?,NULL,NULL,NULL,?, 'demo','succeeded',?,?,"
                   "'not_sent',0,NULL,'2026-01-01','2026-01-01')",
                   (job_id, tid, rvid, json.dumps(snap), json.dumps({"fixture": True})))
        db.execute("INSERT INTO jobs_results (job_id,result_json,created_at) VALUES (?,?,"
                   "'2026-01-01')", (job_id, json.dumps(result)))
    return {"job": job_id, "task": tid, "rule": rvid, "creator": "测试达人"}


def make_review(client: TestClient, job_id: str) -> str:
    r = client.post(f"/api/v1/jobs/{job_id}/human-reviews",
                    headers={"Idempotency-Key": f"mk-{uuid4().hex[:12]}"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


class TestCreateReview:
    def test_succeeded_job_creates_pending_review(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        r = client.get(f"/api/v1/human-reviews/{review_id}")
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["state"] == "pending" and d["scope"] == "script"
        assert d["conclusion"] == "needs_confirmation"
        assert len(d["issues"]) == 1

    def test_unique_job_idempotent_same_key_returns_same(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        key = f"same-{uuid4().hex[:12]}"
        r1 = client.post(f"/api/v1/jobs/{job_id}/human-reviews", headers={"Idempotency-Key": key})
        r2 = client.post(f"/api/v1/jobs/{job_id}/human-reviews", headers={"Idempotency-Key": key})
        assert r1.json()["data"]["id"] == r2.json()["data"]["id"]

    def test_same_key_different_endpoint_conflict(self, client, tmp_path):
        job1 = seed(client, tmp_path, "c1")["job"]
        job2 = seed(client, tmp_path, "c2")["job"]
        key = f"conf-{uuid4().hex[:12]}"
        client.post(f"/api/v1/jobs/{job1}/human-reviews", headers={"Idempotency-Key": key})
        r = client.post(f"/api/v1/jobs/{job2}/human-reviews", headers={"Idempotency-Key": key})
        assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_non_succeeded_job_rejected(self, client, tmp_path):
        base = seed(client, tmp_path, "ns")
        db_path = tmp_path / "workbench.sqlite3"
        job_id = uuid4().hex
        with connection(db_path) as db:
            db.execute("INSERT INTO review_jobs (id,kind,task_id,mode,status,snapshot_json,"
                       "input_json,submission_state,attempt,created_at,updated_at) VALUES "
                       "(?, 'script',?,'demo','queued',?,?, 'not_sent',0,"
                       "'2026-01-01','2026-01-01')",
                       (job_id, base["task"], json.dumps(SNAP), json.dumps({})))
        r = client.post(f"/api/v1/jobs/{job_id}/human-reviews",
                        headers={"Idempotency-Key": f"q-{uuid4().hex[:12]}"})
        assert r.status_code == 409 and "succeeded" in r.json()["error"]["message"]

    def test_brief_kind_rejected(self, client, tmp_path):
        base = seed(client, tmp_path, "bk")
        db_path = tmp_path / "workbench.sqlite3"
        job_id = uuid4().hex
        with connection(db_path) as db:
            db.execute("INSERT INTO review_jobs (id,kind,task_id,mode,status,snapshot_json,"
                       "input_json,submission_state,attempt,created_at,updated_at) VALUES "
                       "(?, 'brief',?,'demo','succeeded',?,?, 'not_sent',0,"
                       "'2026-01-01','2026-01-01')",
                       (job_id, base["task"], json.dumps(SNAP), json.dumps({})))
            db.execute("INSERT INTO jobs_results VALUES (?,?,'2026-01-01')",
                       (job_id, json.dumps({**RESULT, "jobId": job_id})))
        r = client.post(f"/api/v1/jobs/{job_id}/human-reviews",
                        headers={"Idempotency-Key": f"b-{uuid4().hex[:12]}"})
        assert r.status_code == 409


class TestListAndDetail:
    def test_list_default_pending_draft_with_pagination(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        make_review(client, job_id)
        r = client.get("/api/v1/human-reviews")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1 and len(body["data"]) == 1
        item = body["data"][0]
        assert item["creatorName"] == "测试达人" and item["platform"] == "douyin"
        assert item["conclusion"] == "needs_confirmation"

    def test_list_filters(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        make_review(client, job_id)
        assert client.get("/api/v1/human-reviews?state=approved").json()["total"] == 0
        assert client.get("/api/v1/human-reviews?q=不存在").json()["total"] == 0
        assert client.get("/api/v1/human-reviews?platform=xiaohongshu").json()["total"] == 0
        assert client.get("/api/v1/human-reviews?q=测试达人").json()["total"] == 1

    def test_detail_stale_after_rebind(self, client, tmp_path):
        base = seed(client, tmp_path)
        review_id = make_review(client, base["job"])
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            # 换绑到真实存在的另一启用版本（FK 要求先建行）
            db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,"
                       "state,parent_id,revision,created_at) VALUES ('rv-stale','b-a','sv-a',"
                       "'v2','enabled',NULL,1,'2026-01-01')")
            db.execute("UPDATE creator_tasks SET rule_version_id='rv-stale' WHERE id=?",
                       (base["task"],))
        r = client.get(f"/api/v1/human-reviews/{review_id}")
        d = r.json()["data"]
        assert d["stale"] is True and "task_rebind_stale" in d["staleReasons"]


class TestDraft:
    def test_draft_save_and_restore_with_revision(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        body = {"issueDecisions": [{"issueId": "issue-1", "action": "false_positive",
                                    "reason": "剧情反转"}],
                "manualIssues": [], "finalChoice": None, "comment": "草稿"}
        r = client.patch(f"/api/v1/human-reviews/{review_id}/draft", json=body,
                         headers={"If-Match": "1", "Idempotency-Key": f"d1-{uuid4().hex[:10]}"})
        assert r.status_code == 200
        assert r.json()["data"]["state"] == "draft"
        assert r.json()["data"]["revision"] == 2
        assert r.json()["data"]["draft"]["issueDecisions"][0]["reason"] == "剧情反转"
        # 过期 revision
        r2 = client.patch(f"/api/v1/human-reviews/{review_id}/draft", json=body,
                          headers={"If-Match": "1", "Idempotency-Key": f"d2-{uuid4().hex[:10]}"})
        assert r2.status_code == 409 and r2.json()["error"]["code"] == "REVISION_CONFLICT"

    def test_draft_on_terminal_rejected(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        approve_body = {"decision": "approved", "comment": "通过",
                        "issueDecisions": [{"issueId": "issue-1", "action": "false_positive",
                                            "reason": "反转"}], "manualIssues": []}
        client.post(f"/api/v1/human-reviews/{review_id}/decision", json=approve_body,
                    headers={"If-Match": "1", "Idempotency-Key": f"a1-{uuid4().hex[:10]}"})
        r = client.patch(f"/api/v1/human-reviews/{review_id}/draft",
                         json={"issueDecisions": [], "manualIssues": [], "comment": ""},
                         headers={"If-Match": "2", "Idempotency-Key": f"d3-{uuid4().hex[:10]}"})
        assert r.status_code == 409


class TestDecision:
    def approve_body(self):
        return {"decision": "approved", "comment": "人工核对通过",
                "issueDecisions": [{"issueId": "issue-1", "action": "false_positive",
                                    "reason": "剧情反转语境"}], "manualIssues": []}

    def test_approve_full_integrity_script_to_video_stage(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                        json=self.approve_body(),
                        headers={"If-Match": "1", "Idempotency-Key": f"ok-{uuid4().hex[:10]}"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["decision"] == "approved" and d["taskStage"] == "video"  # script→video 不完成
        assert d["simulated"] is True  # demo 模拟记录
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            assert db.execute("SELECT stage FROM creator_tasks WHERE id='task-a'"
                              ).fetchone()[0] == "video"
            assert db.execute("SELECT state FROM human_reviews WHERE id=?",
                              (review_id,)).fetchone()[0] == "approved"

    def test_approve_blocked_by_missing_integrity(self, client, tmp_path):
        """video scope 下音频不完整禁 approve（script scope 按 spec 不查音轨）。"""
        base = seed(client, tmp_path)
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            db.execute("UPDATE review_jobs SET kind='video' WHERE id=?", (base["job"],))
            row = db.execute("SELECT result_json FROM jobs_results WHERE job_id=?",
                             (base["job"],)).fetchone()
            result = json.loads(row["result_json"])
            result["integrity"]["audio"] = "incomplete"
            db.execute("UPDATE jobs_results SET result_json=? WHERE job_id=?",
                       (json.dumps(result), base["job"]))
        review_id = make_review(client, base["job"])
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                        json=self.approve_body(),
                        headers={"If-Match": "1", "Idempotency-Key": f"no-{uuid4().hex[:10]}"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "AUDIO_ANALYSIS_INCOMPLETE"

    def test_approve_blocked_when_stale(self, client, tmp_path):
        base = seed(client, tmp_path)
        review_id = make_review(client, base["job"])
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,"
                       "state,parent_id,revision,created_at) VALUES ('rv-stale2','b-a','sv-a',"
                       "'v2','enabled',NULL,1,'2026-01-01')")
            db.execute("UPDATE creator_tasks SET rule_version_id='rv-stale2' WHERE id=?",
                       (base["task"],))
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                        json=self.approve_body(),
                        headers={"If-Match": "1", "Idempotency-Key": f"st-{uuid4().hex[:10]}"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_REFERENCES"

    def test_script_changes_requested_cannot_manual_approve(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            row = db.execute("SELECT result_json FROM jobs_results WHERE job_id=?",
                             (job_id,)).fetchone()
            result = json.loads(row["result_json"])
            result["conclusion"] = "changes_requested"
            db.execute("UPDATE jobs_results SET result_json=? WHERE job_id=?",
                       (json.dumps(result), job_id))
        review_id = make_review(client, job_id)
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                        json=self.approve_body(),
                        headers={"If-Match": "1", "Idempotency-Key": f"cr-{uuid4().hex[:10]}"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "SCRIPT_CONCLUSION_NOT_CONFIRMABLE"

    def test_return_with_reason_allowed_back_to_requirements(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        body = {"decision": "returned", "comment": "功效表达需修改",
                "issueDecisions": [{"issueId": "issue-1", "action": "confirmed",
                                    "reason": "确认违规"}], "manualIssues": []}
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision", json=body,
                        headers={"If-Match": "1", "Idempotency-Key": f"rt-{uuid4().hex[:10]}"})
        assert r.status_code == 200
        assert r.json()["data"]["taskStage"] == "requirements"  # script 退回起点

    def test_video_return_back_to_script(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            db.execute("UPDATE review_jobs SET kind='video' WHERE id=?", (job_id,))
        review_id = make_review(client, job_id)
        body = {"decision": "returned", "comment": "视频需修改",
                "issueDecisions": [], "manualIssues": []}
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision", json=body,
                        headers={"If-Match": "1", "Idempotency-Key": f"vr-{uuid4().hex[:10]}"})
        assert r.status_code == 200
        assert r.json()["data"]["taskStage"] == "script"

    def test_duplicate_issue_decision_rejected(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        body = {"decision": "approved", "comment": "x",
                "issueDecisions": [
                    {"issueId": "issue-1", "action": "confirmed", "reason": "a"},
                    {"issueId": "issue-1", "action": "false_positive", "reason": "b"}],
                "manualIssues": []}
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision", json=body,
                        headers={"If-Match": "1", "Idempotency-Key": f"dp-{uuid4().hex[:10]}"})
        assert r.status_code == 400

    def test_unknown_issue_reference_rejected(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        body = {"decision": "approved", "comment": "x",
                "issueDecisions": [{"issueId": "ghost", "action": "confirmed", "reason": ""}],
                "manualIssues": []}
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision", json=body,
                        headers={"If-Match": "1", "Idempotency-Key": f"uk-{uuid4().hex[:10]}"})
        assert r.status_code == 400

    def test_terminal_decision_cannot_resubmit(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        key = f"tm-{uuid4().hex[:10]}"
        client.post(f"/api/v1/human-reviews/{review_id}/decision", json=self.approve_body(),
                    headers={"If-Match": "1", "Idempotency-Key": key})
        r = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                        json={"decision": "returned", "comment": "改主意",
                              "issueDecisions": [], "manualIssues": []},
                        headers={"If-Match": "2", "Idempotency-Key": f"tm2-{uuid4().hex[:10]}"})
        assert r.status_code == 409

    def test_decision_idempotent_replay(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        key = f"idem-{uuid4().hex[:10]}"
        r1 = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                         json=self.approve_body(),
                         headers={"If-Match": "1", "Idempotency-Key": key})
        r2 = client.post(f"/api/v1/human-reviews/{review_id}/decision",
                         json=self.approve_body(),
                         headers={"If-Match": "1", "Idempotency-Key": key})
        assert r1.json()["data"] == r2.json()["data"]  # 幂等回放一次

    def test_corrupted_result_rejected_not_degraded(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            db.execute("UPDATE jobs_results SET result_json='{bad json' WHERE job_id=?",
                       (job_id,))
        r = client.post(f"/api/v1/jobs/{job_id}/human-reviews",
                        headers={"Idempotency-Key": f"cj-{uuid4().hex[:10]}"})
        assert r.status_code == 422  # 解析失败不降级成空

    def test_audit_trail_written(self, client, tmp_path):
        job_id = seed(client, tmp_path)["job"]
        review_id = make_review(client, job_id)
        client.post(f"/api/v1/human-reviews/{review_id}/decision",
                    json=self.approve_body(),
                    headers={"If-Match": "1", "Idempotency-Key": f"au-{uuid4().hex[:10]}"})
        db_path = tmp_path / "workbench.sqlite3"
        with connection(db_path) as db:
            actions = [r["action"] for r in db.execute(
                "SELECT action FROM audit_events WHERE entity_type='human_review' "
                "AND entity_id=?", (review_id,)).fetchall()]
        assert "created" in actions and "approved" in actions
