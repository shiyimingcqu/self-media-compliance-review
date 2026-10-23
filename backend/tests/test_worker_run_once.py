"""D t-24：worker run_once 最小纵向切片测试（tmp_path 独立库；无网络/常驻）。

覆盖 A 四点纠正：
① update_to_processing 显式拒绝零副作用（误用不改队列）
② 三段式短事务：执行期间另连接可创建/认领其他作业（Barrier 不 sleep）
③ (mode,kind) 双键隔离：demo fake 不跑 real；result mode 由 DB 行保证
④ video 入参先验：file 存在/purpose/mode 一致；缺引用 failed 不网络
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core.database import connection, migrate
from app.modules.video_model import jobs_repository as jr
from app.modules.video_model.adapter import FakeVideoAdapter, UnknownSubmissionError
from app.modules.video_model.jobs_adapter import RepoJobAdapter, SnapshotInvalid
from app.modules.video_model.worker import run_once

SNAP = {"mode": "demo", "ruleVersionId": "rv-1", "modelConfigId": "mc_1",
        "modelConfigRevision": 1}
INPUT = {"any": "payload"}


def make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "worker.sqlite3"
    migrate(db_path)
    return db_path


def seed_video_task(db: sqlite3.Connection, *, task_id: str = "task-1",
                    file_id: str = "file-v", file_purpose: str = "video",
                    file_mode: str = "demo", rule_id: str = "rv-1",
                    rule_state: str = "enabled") -> None:
    db.execute("INSERT INTO brands VALUES ('brand-1','麦咖啡','麦咖啡')")
    db.execute("INSERT INTO projects VALUES ('proj-1','测试','测试','brand-1','x','[]',"
               "'demo','active',1,'2026-01-01','2026-01-01')")
    db.execute("INSERT INTO file_assets VALUES (?,?,?,?,3,?,?,'video','unverifiable',"
               "'unverified',?,'2026-01-01')".replace("'video'", f"'{file_purpose}'"),
               (file_id, "v.mp4", "mp4", "video/mp4", "0" * 64, f"k-{file_id}", file_mode))
    # 前置行先行（rule_versions 是 creator_tasks.rule_version_id 的 FK 目标）
    db.execute("INSERT INTO file_assets VALUES ('file-b','a.txt','txt','text/plain',3,"
               "'" + "0" * 64 + "','kb','brief','unverifiable','unverified','demo','2026-01-01')")
    db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) "
               "VALUES ('brief-1','proj-1','B',NULL,'2026-01-01')")
    db.execute("INSERT INTO brief_source_versions (id,brief_id,file_id,extraction_status,"
               "page_count,uncovered_sources_json,created_at) "
               "VALUES ('src-1','brief-1','file-b','ready',1,'[]','2026-01-01')")
    db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,state,"
               "parent_id,revision,confirmed_by,confirmed_at,created_at) "
               "VALUES (?, 'brief-1','src-1','v1',?,NULL,1,'local-reviewer','2026-01-01','2026-01-01')",
               (rule_id, rule_state))
    db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,"
               "campaign_phase,rule_version_id,stage,revision,archived_at,created_at,updated_at) "
               "VALUES (?,?,?,'douyin','video','general',?,'requirements',1,NULL,'2026-01-01','2026-01-01')",
               (task_id, "proj-1", "测试达人", rule_id))


def enqueue_video_job(db_path: Path, **overrides) -> str:
    with connection(db_path) as db:
        repo = jr.SqliteJobRepository(db)
        kwargs = dict(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1",
                      video_file_id="file-v", rule_version_id="rv-1")
        kwargs.update(overrides)
        return repo.create_job(**kwargs)


DEMO_VIDEO_ADAPTERS = {("demo", "video"): FakeVideoAdapter()}


class TestAdapterMisuseGuard:
    """纠正①：update_to_processing 显式拒绝，误用零副作用。"""

    def test_update_to_processing_raises_and_changes_nothing(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path)
        with connection(db_path) as db:
            adapter_repo = RepoJobAdapter(db, "demo")
            with pytest.raises(NotImplementedError):
                adapter_repo.update_to_processing(job)
        # 队列原样：仍是 queued，无新 processing 行
        with connection(db_path) as db:
            row = db.execute("SELECT status FROM review_jobs WHERE id=?", (job,)).fetchone()
            assert row["status"] == "queued"
            total = db.execute("SELECT COUNT(*) FROM review_jobs WHERE status='processing'").fetchone()[0]
            assert total == 0


class TestBasicRunOnce:
    def test_empty_queue_returns_empty(self, tmp_path):
        db_path = make_db(tmp_path)
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.claimed is False and result.detail == "empty"

    def test_demo_video_happy_path_succeeded_with_audit(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path)
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.claimed and result.status == "succeeded" and result.detail == "ok"
        with connection(db_path) as db:
            row = db.execute("SELECT status, provider_request_id, submission_state "
                             "FROM review_jobs WHERE id=?", (job,)).fetchone()
            assert row["status"] == "succeeded"
            assert row["provider_request_id"] == "fake-1"  # 追溯登记
            assert row["submission_state"] == "submitted"
            result_row = db.execute("SELECT result_json FROM jobs_results WHERE job_id=?",
                                    (job,)).fetchone()
            assert result_row is not None
            # snapshot.mode 来自 DB 行（demo），不信 fake 自称（纠正③）
            import json
            payload = json.loads(result_row["result_json"])
            assert payload["snapshot"]["mode"] == "demo"
            audits = db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=?",
                                (job,)).fetchone()[0]
            assert audits == 3  # created + claimed + succeeded

    def test_audio_incomplete_layering_persists_to_sqlite(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path)
        adapters = {("demo", "video"): FakeVideoAdapter(failure_mode="audio_incomplete")}
        result = run_once(db_path, "demo", adapters)
        assert result.status == "succeeded"  # 分析有结果
        with connection(db_path) as db:
            import json
            payload = json.loads(db.execute(
                "SELECT result_json FROM jobs_results WHERE job_id=?", (job,)).fetchone()["result_json"])
        assert payload["conclusion"] == "incomplete"  # t-18 分层穿透到 SQLite
        assert payload["integrity"]["audio"] == "incomplete"

    def test_unknown_timeout_interrupted_not_retried_automatically(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        enqueue_video_job(db_path)
        adapters = {("demo", "video"): FakeVideoAdapter(failure_mode="unknown_timeout")}
        first = run_once(db_path, "demo", adapters)
        assert first.status == "interrupted"  # 提交未知：interrupted 等查询
        # 再次 run_once 不捞 processing（claim_next 只取 queued）——不自动重发
        second = run_once(db_path, "demo", adapters)
        assert second.claimed is False and second.detail == "empty"


class TestModeKindIsolation:
    """纠正①：首批硬边界——real/非 video 即便注册表有适配器也零调用。"""

    def _seed_real_job(self, db_path):
        with connection(db_path) as db:
            db.execute("INSERT INTO brands VALUES ('brand-1','麦咖啡','麦咖啡')")
            db.execute("INSERT INTO projects VALUES ('proj-real','真实','真实','brand-1','x','[]',"
                       "'real','active',1,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,"
                       "campaign_phase,rule_version_id,stage,revision,archived_at,created_at,updated_at) "
                       "VALUES ('task-real','proj-real','真实达人','douyin','video','general',NULL,"
                       "'requirements',1,NULL,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO file_assets VALUES ('file-r','v.mp4','mp4','video/mp4',3,"
                       "'" + "0" * 64 + "','kr','video','unverifiable','unverified','real','2026-01-01')")
        with connection(db_path) as db:
            return jr.SqliteJobRepository(db).create_job(
                kind="video", mode="real",
                snapshot={**SNAP, "mode": "real"}, input=INPUT,
                task_id="task-real", video_file_id="file-r")

    def test_real_rejected_even_with_injected_real_adapter(self, tmp_path):
        """纠正①核心：注册表误注入 ('real','video') fake 也不得执行——零调用。"""
        db_path = make_db(tmp_path)
        job = self._seed_real_job(db_path)

        call_counter = {"n": 0}
        original_analyze = FakeVideoAdapter.analyze_video

        def counting_analyze(self, *a, **k):
            call_counter["n"] += 1
            return original_analyze(self, *a, **k)

        FakeVideoAdapter.analyze_video = counting_analyze
        try:
            result = run_once(db_path, "real", {("real", "video"): FakeVideoAdapter()})
        finally:
            FakeVideoAdapter.analyze_video = original_analyze
        assert result.status == "failed" and result.detail == "out_of_scope"
        assert call_counter["n"] == 0  # 适配器零调用
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
            assert "明确拒绝" in err

    def test_real_job_with_only_demo_adapter_rejected(self, tmp_path):
        db_path = make_db(tmp_path)
        job = self._seed_real_job(db_path)
        result = run_once(db_path, "real", DEMO_VIDEO_ADAPTERS)  # 只有 (demo,video)
        assert result.claimed and result.status == "failed" and result.detail == "out_of_scope"
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
            assert "明确拒绝" in err

    def test_unsupported_kind_rejected(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            db.execute("INSERT INTO brands VALUES ('brand-1','麦咖啡','麦咖啡')")
            db.execute("INSERT INTO projects VALUES ('proj-1','测试','测试','brand-1','x','[]','demo','active',1,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO file_assets VALUES ('file-b','a.txt','txt','text/plain',3,"
                       "'" + "0" * 64 + "','kb','brief','unverifiable','unverified','demo','2026-01-01')")
            db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) "
                       "VALUES ('brief-1','proj-1','B',NULL,'2026-01-01')")
            db.execute("INSERT INTO brief_source_versions (id,brief_id,file_id,extraction_status,"
                       "page_count,uncovered_sources_json,created_at) "
                       "VALUES ('src-1','brief-1','file-b','ready',1,'[]','2026-01-01')")
            job = jr.SqliteJobRepository(db).create_job(
                kind="brief", mode="demo", snapshot=SNAP, input=INPUT, source_version_id="src-1")
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)  # brief 不在首批
        assert result.claimed and result.status == "failed" and result.detail == "out_of_scope"


class TestVideoPrecheck:
    """纠正④：video 入参先验（file 存在/purpose/mode 一致；缺引用 failed）。"""

    def test_missing_video_file_id_failed_without_execution(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path, video_file_id=None)
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.status == "failed" and result.detail == "precondition"
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
            assert "缺 video_file_id" in err

    def test_wrong_purpose_file_rejected(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db, file_id="file-s", file_purpose="script")
        enqueue_video_job(db_path, video_file_id="file-s")
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.status == "failed" and result.detail == "precondition"

    def test_mode_mismatched_file_rejected_at_creation(self, tmp_path):
        """file mode 与作业 mode 不一致：create 阶段即被 repo 引用核查拦截
        （worker 先验是第二道防线；此场景根本到不了 worker）。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db, file_id="file-r", file_mode="real")
        with pytest.raises(jr.JobValidationError, match="mode"):
            enqueue_video_job(db_path, video_file_id="file-r")  # demo 作业引 real 文件：拒

    # 注：file 存在性失效场景（幽灵 file id）在两层防护下不可达——
    # review_jobs.video_file_id 的 FK 拒绝插入不存在的 file；repo create
    # 校验再拦一次；worker 先验的"file 不存在"分支是纵深防御（直接 SQL
    # 绕 FK 亦被 FK 拒绝）。缺 file_id（None）路径由
    # test_missing_video_file_id_failed_without_execution 覆盖。


class TestSnapshotValidationBeforeAdapter:
    """纠正②：快照校验在 adapter 之前；失败零 adapter 调用。"""

    def _adapter_call_counter(self):
        counter = {"n": 0}
        original = FakeVideoAdapter.analyze_video

        def counting(self, *a, **k):
            counter["n"] += 1
            return original(self, *a, **k)

        return counter, counting, original

    def test_invalid_snapshot_fails_without_adapter_call(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            job = jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={"bad": "shape"},  # 无法构造 ReviewSnapshot
                input=INPUT, task_id="task-1", video_file_id="file-v")
        counter, counting, original = self._adapter_call_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "invalid_snapshot"
        assert counter["n"] == 0  # 快照失败零 adapter 调用
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
            assert err == "快照校验失败"  # 固定文案，无输入回显

    def test_snapshot_mode_mismatch_fails_before_adapter(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            job = jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={**SNAP, "mode": "real"},  # 快照自称 real
                input=INPUT, task_id="task-1", video_file_id="file-v")
        counter, counting, original = self._adapter_call_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "snapshot_mode_mismatch"
        assert counter["n"] == 0
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
            assert err == "快照 mode 与作业行不一致"

    def test_snapshot_rule_mismatch_fails(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db, rule_id="rv-1")
        with connection(db_path) as db:
            job = jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={**SNAP, "ruleVersionId": "rv-other"},  # 与行绑定不一致
                input=INPUT, task_id="task-1", video_file_id="file-v",
                rule_version_id="rv-1")
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.status == "failed" and result.detail == "snapshot_rule_mismatch"

    def test_rule_version_not_enabled_fails(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db, rule_state="draft")  # 规则未启用（draft 非 enabled/superseded）
        enqueue_video_job(db_path)
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.status == "failed" and result.detail == "rule_state_invalid"
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs").fetchone()["error_message"]
            assert err == "规则版本状态不可用于既有任务资格"


class TestUnknownSubmissionPersistence:
    """纠正③：无 request_id 的 unknown 也必须持久化 submission_state。"""

    def test_unknown_timeout_without_id_persists_unknown_state(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path)

        class NoIdTimeoutAdapter(FakeVideoAdapter):
            def analyze_video(self, *a, **k):
                exc = UnknownSubmissionError("timed out, no request id available")
                exc.provider_request_id = None  # 无 id
                raise exc

        result = run_once(db_path, "demo", {("demo", "video"): NoIdTimeoutAdapter()})
        assert result.status == "interrupted"
        with connection(db_path) as db:
            row = db.execute("SELECT submission_state, provider_request_id, status "
                             "FROM review_jobs WHERE id=?", (job,)).fetchone()
            assert row["submission_state"] == "unknown"  # 不留 not_sent 误判
            assert row["provider_request_id"] is None
            assert row["status"] == "interrupted"


class TestAdapterCreateJobGuard:
    """纠正④：create_job 显式拒绝，误用零副作用。"""

    def test_create_job_raises_and_changes_nothing(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        enqueue_video_job(db_path)
        with connection(db_path) as db:
            adapter_repo = RepoJobAdapter(db, "demo")
            internal = adapter_repo.find_by_id(
                db.execute("SELECT id FROM review_jobs").fetchone()["id"])
            with pytest.raises(NotImplementedError):
                adapter_repo.create_job(internal)
        # 队列原样：仍一行，无新建行
        with connection(db_path) as db:
            assert db.execute("SELECT COUNT(*) FROM review_jobs").fetchone()[0] == 1


class TestSensitiveValueContainment:
    """纠正①（01:03 轮）：异常原文（密钥/签名 URL/本地路径）绝不落库。"""

    def test_secrets_not_persisted_in_error_messages(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path)

        class LeakyAdapter(FakeVideoAdapter):
            def analyze_video(self, *a, **k):
                raise RuntimeError(
                    "boom sk-secret-AbCd1234567890EfGh "
                    "?sig=aws4&X-Amz-Signature=deadbeef "
                    "C:\\Users\\14580\\secret\\key.pem")

        result = run_once(db_path, "demo", {("demo", "video"): LeakyAdapter()})
        assert result.status == "failed" and result.detail == "unexpected_adapter_error"
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
        assert err == "适配器执行异常，作业标记失败"  # 固定文案
        for secret in ("sk-secret", "X-Amz-Signature", "deadbeef", "key.pem", "C:\\Users"):
            assert secret not in err
            assert secret not in result.detail  # RunOnceResult 亦不含原文

    def test_validation_error_not_echoing_input(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            job = jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={"poison": "xss-payload-12345"},
                input=INPUT, task_id="task-1", video_file_id="file-v")
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.status == "failed" and result.detail == "invalid_snapshot"
        with connection(db_path) as db:
            err = db.execute("SELECT error_message FROM review_jobs WHERE id=?",
                             (job,)).fetchone()["error_message"]
        assert err == "快照校验失败"  # 固定文案，无 Pydantic 输入回显
        assert "poison" not in err and "xss" not in err.lower()


class TestSnapshotCrossValidation:
    """纠正②（01:03 轮）：task/rule/项目/mode 全链实查 + superseded 不误杀。"""

    def _adapter_counter(self):
        counter = {"n": 0}
        original = FakeVideoAdapter.analyze_video

        def counting(self, *a, **k):
            counter["n"] += 1
            return original(self, *a, **k)

        return counter, counting, original

    def test_cross_project_rule_rejected(self, tmp_path):
        """同 mode 跨项目规则：rule 属别的项目 → 拒，零 adapter 调用。

        场景：任务也绑到 rv-2（否则会先触发 task_rebind_stale_job），
        但 rv-2 的 brief 属 proj-2 ≠ task 所在 proj-1。
        """
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)  # proj-1 / task-1 / rv-1
            db.execute("INSERT INTO projects VALUES ('proj-2','项目二','项目二','brand-1','x','[]',"
                       "'demo','active',1,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) "
                       "VALUES ('brief-2','proj-2','B2',NULL,'2026-01-01')")
            db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,state,"
                       "parent_id,revision,confirmed_by,confirmed_at,created_at) "
                       "VALUES ('rv-2','brief-2','src-1','v1','enabled',NULL,1,'u','2026-01-01','2026-01-01')")
            # 任务与作业行一致地绑到 rv-2（排除 stale 干扰），rv-2 属他项目
            db.execute("UPDATE creator_tasks SET rule_version_id='rv-2' WHERE id='task-1'")
            jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={**SNAP, "ruleVersionId": "rv-2"},
                input=INPUT, task_id="task-1", video_file_id="file-v",
                rule_version_id="rv-2")
        counter, counting, original = self._adapter_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "rule_project_mismatch"
        assert counter["n"] == 0

    def test_task_rebind_after_job_creation_rejected(self, tmp_path):
        """任务改绑后旧 job 行与 task 当前绑定失配 → 拒，零调用。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db, rule_id="rv-1")
            db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,state,"
                       "parent_id,revision,confirmed_by,confirmed_at,created_at) "
                       "VALUES ('rv-1b','brief-1','src-1','v1b','enabled',NULL,1,'u','2026-01-01','2026-01-01')")
        enqueue_video_job(db_path)  # 快照与行均 rv-1
        with connection(db_path) as db:
            db.execute("UPDATE creator_tasks SET rule_version_id='rv-1b' WHERE id='task-1'")
        counter, counting, original = self._adapter_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed"
        assert result.detail == "task_rebind_stale_job"  # 任务改绑，旧 job 过期
        assert counter["n"] == 0

    def test_job_row_without_rule_rejected(self, tmp_path):
        """作业行 rule_version_id 为空 → 拒（不条件跳过），零调用。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo", snapshot=SNAP, input=INPUT,
                task_id="task-1", video_file_id="file-v", rule_version_id=None)
        counter, counting, original = self._adapter_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "job_rule_binding_missing"
        assert counter["n"] == 0

    def test_superseded_rule_still_valid_for_existing_task(self, tmp_path):
        """既有任务绑定 superseded 规则：资格有效不误杀（A 裁决）。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db, rule_state="superseded")
        enqueue_video_job(db_path)
        result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        assert result.status == "succeeded"

    def test_script_version_mismatch_rejected(self, tmp_path):
        """快照 scriptVersionId 与行 script_version_id 失配 → 拒，零调用。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={**SNAP, "scriptVersionId": "sv-9"},
                input=INPUT, task_id="task-1", video_file_id="file-v",
                rule_version_id="rv-1", script_version_id="sv-1")
        counter, counting, original = self._adapter_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "snapshot_script_mismatch"
        assert counter["n"] == 0

    def test_row_none_snapshot_nonempty_rejected(self, tmp_path):
        """行 script_version_id=None 但快照自称脚本 → 拒（不条件跳过），零调用。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot={**SNAP, "scriptVersionId": "sv-ghost"},
                input=INPUT, task_id="task-1", video_file_id="file-v",
                rule_version_id="rv-1", script_version_id=None)  # 行空
        counter, counting, original = self._adapter_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "snapshot_script_mismatch"
        assert counter["n"] == 0

    def test_row_nonempty_snapshot_none_rejected(self, tmp_path):
        """行 script_version_id='sv-1' 但快照无脚本 → 拒，零调用。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        with connection(db_path) as db:
            jr.SqliteJobRepository(db).create_job(
                kind="video", mode="demo",
                snapshot=SNAP,  # SNAP 无 scriptVersionId（None）
                input=INPUT, task_id="task-1", video_file_id="file-v",
                rule_version_id="rv-1", script_version_id="sv-1")  # 行非空
        counter, counting, original = self._adapter_counter()
        FakeVideoAdapter.analyze_video = counting
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            FakeVideoAdapter.analyze_video = original
        assert result.status == "failed" and result.detail == "snapshot_script_mismatch"
        assert counter["n"] == 0


class TestShortTransactionBoundaries:
    """纠正②：三段式短事务——执行期间另连接可创建/认领其他作业。"""

    def test_other_connection_can_work_during_adapter_execution(self, tmp_path):
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        enqueue_video_job(db_path)  # 作业 A
        enqueue_video_job(db_path)  # 作业 B

        other_claimed: list = []
        barrier = threading.Barrier(2, timeout=10)

        class GatedAdapter(FakeVideoAdapter):
            """在 analyze_video 中同步：证明此时写锁已释放。"""
            def analyze_video(self, *a, **k):
                barrier.wait()  # 与主线程对齐：此刻另一连接开始工作
                return super().analyze_video(*a, **k)

        def other_worker():
            # 主线程 adapter 计算期间：另一连接创建+认领作业 B
            with connection(db_path) as db:
                repo = jr.SqliteJobRepository(db)
                barrier.wait()  # 等 GatedAdapter 进入计算段
                other_claimed.append(repo.claim_next("demo"))

        with ThreadPoolExecutor(max_workers=2) as pool:
            other = pool.submit(other_worker)
            main = pool.submit(run_once, db_path, "demo",
                               {("demo", "video"): GatedAdapter()})
            main_result = main.result(timeout=15)
            other.result(timeout=15)

        assert main_result.status == "succeeded"  # 作业 A 完成
        assert other_claimed and other_claimed[0] is not None  # 作业 B 被另一连接认领
        with connection(db_path) as db:
            statuses = dict(db.execute(
                "SELECT status, COUNT(*) FROM review_jobs GROUP BY status").fetchall())
        assert statuses.get("succeeded") == 1
        assert statuses.get("processing") == 1  # B 在另一连接手里

    def test_single_claim_per_job(self, tmp_path):
        """run_once 全程对同一作业只有一次 claim（认领后不二次 CAS）。"""
        db_path = make_db(tmp_path)
        with connection(db_path) as db:
            seed_video_task(db)
        job = enqueue_video_job(db_path)
        claim_counter = {"n": 0}
        original = jr.SqliteJobRepository.claim_next

        def counting_claim(self, mode):
            claim_counter["n"] += 1
            return original(self, mode)

        jr.SqliteJobRepository.claim_next = counting_claim
        try:
            result = run_once(db_path, "demo", DEMO_VIDEO_ADAPTERS)
        finally:
            jr.SqliteJobRepository.claim_next = original
        assert result.status == "succeeded"
        assert claim_counter["n"] == 1  # 恰一次 claim
        with connection(db_path) as db:
            assert db.execute("SELECT status FROM review_jobs WHERE id=?",
                              (job,)).fetchone()["status"] == "succeeded"
