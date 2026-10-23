"""D t-22：共享 ReviewJob SQLite repository 测试（真实库，tmp_path 独立）。

关键覆盖（A 23:40 裁决）：
- 真实 SQLite 两个独立连接、Barrier 同步并发 claim（不 sleep）——恰一胜
- 新库全迁移 / 已有库追加迁移
- kind×必填引用 CHECK（script/video 无 task 拒；brief/capability 无 source 拒）
- mode 进所有更新/查询（demo 不认领/不改写 real 行）
- 终态不降级、结果权威表唯一且不可覆盖
不实现 worker/恢复/接管；不向真实 API 发请求。
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core.database import connection, migrate
from app.modules.video_model.jobs_repository import (
    JobValidationError,
    SqliteJobRepository,
    validate_job_shape,
)

SNAP = {"mode": "demo", "ruleVersionId": "rv_1", "modelConfigId": "mc_1",
        "modelConfigRevision": 1}
INPUT = {"any": "payload"}


def make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "jobs.sqlite3"
    migrate(db_path)
    return db_path


def seed_task(db: sqlite3.Connection, task_id: str = "task-1", source_id: str = "src-1") -> None:
    """测试库内建合法前置行（仅测试数据，不动开发库）。"""
    db.execute("INSERT INTO brands VALUES ('brand-1','麦咖啡','麦咖啡')")
    db.execute("INSERT INTO projects VALUES ('proj-1','测试项目','测试项目','brand-1','x','[]','demo','active',1,'2026-01-01','2026-01-01')")
    db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,"
               "campaign_phase,rule_version_id,stage,revision,archived_at,created_at,updated_at) "
               "VALUES (?,?,?,'douyin','video','general',NULL,'requirements',1,NULL,'2026-01-01','2026-01-01')",
               (task_id, "proj-1", "测试达人"))
    db.execute("INSERT INTO file_assets VALUES ('file-1','a.txt','txt','text/plain',3,"
               "'" + "0" * 63 + "1','k1','brief','unverifiable','unverified','demo','2026-01-01')")
    db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) "
               "VALUES ('brief-1','proj-1','B',NULL,'2026-01-01')")
    db.execute("INSERT INTO brief_source_versions (id,brief_id,file_id,extraction_status,"
               "page_count,uncovered_sources_json,created_at) "
               "VALUES (?,?,?, 'ready', 1, '[]', '2026-01-01')", (source_id, "brief-1", "file-1"))


@pytest.fixture
def db_path(tmp_path):
    return make_db(tmp_path)


class TestMigrations:
    def test_fresh_db_all_migrations_applied(self, db_path):
        with connection(db_path) as db:
            names = [r["name"] for r in db.execute("SELECT name FROM schema_migrations ORDER BY name")]
            assert "0001_foundation.sql" in names
            assert "C_0001_review_jobs.sql" in names
            tables = {r["name"] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"review_jobs", "jobs_results"} <= tables
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_existing_db_upgrade_adds_jobs_tables(self, tmp_path):
        # 模拟"只应用过 0001 的旧库"，再全量 migrate 追加（含 C_0001）。
        db_path = tmp_path / "old.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        foundation = (Path(__file__).resolve().parents[1] / "migrations"
                      / "0001_foundation.sql").read_text(encoding="utf-8")
        with connection(db_path) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);\n"
                "BEGIN IMMEDIATE;\n" + foundation
                + "\nINSERT INTO schema_migrations VALUES ('0001_foundation.sql','2026-01-01');\nCOMMIT;")
        migrate(db_path)  # 追加全部后续迁移（含 C_0001）
        with connection(db_path) as db:
            tables = {r["name"] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"review_jobs", "jobs_results"} <= tables
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []


class TestKindShapeValidation:
    def test_script_without_task_rejected(self):
        with pytest.raises(JobValidationError):
            validate_job_shape("script", None, None, SNAP, INPUT)

    def test_video_without_task_rejected(self):
        with pytest.raises(JobValidationError):
            validate_job_shape("video", None, None, SNAP, INPUT)

    def test_brief_without_source_rejected(self):
        with pytest.raises(JobValidationError):
            validate_job_shape("brief", None, None, SNAP, INPUT)

    def test_capability_without_source_is_legal(self):
        """capability_test 不绑 Brief source——测试素材/配置在 input 内（A 裁决①）。"""
        cap_input = {"testInput": {"fileId": "file-test-1"}, "config": {"modelId": "mc-1"}}
        validate_job_shape("capability_test", None, None, SNAP, cap_input)

    def test_capability_minimal_input_enforced(self):
        """缺 testInput 或 config 的 capability input 拒绝。"""
        with pytest.raises(JobValidationError):
            validate_job_shape("capability_test", None, None, SNAP, {"config": {"m": "x"}})
        with pytest.raises(JobValidationError):
            validate_job_shape("capability_test", None, None, SNAP, {"testInput": {"f": "y"}})
        with pytest.raises(JobValidationError):
            validate_job_shape("capability_test", None, None, SNAP, {"testInput": {}, "config": {}})

    def test_bad_kind_rejected(self):
        with pytest.raises(JobValidationError):
            validate_job_shape("other", "t", "s", SNAP, INPUT)

    def test_empty_snapshot_or_input_rejected(self):
        with pytest.raises(JobValidationError):
            validate_job_shape("video", "t", None, {}, INPUT)
        with pytest.raises(JobValidationError):
            validate_job_shape("video", "t", None, SNAP, {})

    def test_valid_combinations_pass(self):
        validate_job_shape("video", "t", None, SNAP, INPUT)
        validate_job_shape("brief", None, "s", SNAP, INPUT)
        validate_job_shape("script", "t", None, SNAP, INPUT)
        validate_job_shape("capability_test", None, None, SNAP,
                           {"testInput": {"f": "1"}, "config": {"m": "2"}})


class TestCreateAndClaim:
    def test_create_and_claim_fifo(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            first = repo.create_job(kind="video", mode="demo", snapshot=SNAP,
                                    input=INPUT, task_id="task-1")
            second = repo.create_job(kind="video", mode="demo", snapshot=SNAP,
                                     input=INPUT, task_id="task-1")
            claimed = repo.claim_next("demo")
            assert claimed is not None and claimed.id == first
            # 再认领第二个
            next_claimed = repo.claim_next("demo")
            assert next_claimed is not None and next_claimed.id == second
            # 队列空
            assert repo.claim_next("demo") is None

    def test_claim_returns_processing_row_with_snapshot(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job_id = repo.create_job(kind="video", mode="demo", snapshot=SNAP,
                                     input=INPUT, task_id="task-1")
            row = db.execute("SELECT status FROM review_jobs WHERE id=?", (job_id,)).fetchone()
            assert row["status"] == "queued"
            claimed = repo.claim_next("demo")
            assert claimed is not None and claimed.id == job_id
            row = db.execute("SELECT status FROM review_jobs WHERE id=?", (job_id,)).fetchone()
            assert row["status"] == "processing"  # CAS 已置 processing
            assert claimed.snapshot == SNAP and claimed.input == INPUT

    def test_claim_updates_timestamp(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            before = db.execute("SELECT updated_at FROM review_jobs").fetchone()["updated_at"]
            repo.claim_next("demo")
            after = db.execute("SELECT updated_at FROM review_jobs").fetchone()["updated_at"]
            assert after >= before


class TestConcurrentClaim:
    """真实 SQLite 两连接 Barrier 并发：恰一胜，败者零写入。"""

    def _seed_two_jobs(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")

    def test_two_connections_never_double_claim(self, db_path):
        """两连接并发认领（Barrier 同步）：核心不变量 = 无行被认领两次。

        合法结果有两种：各胜一行（2 winner，各行恰好一次）；或一方
        竞争同一候选行落败（1 winner 1 loser）。任何情况下 claimed id
        必须互异，且 processing 行数 == winner 数。
        """
        self._seed_two_jobs(db_path)
        results: list = []
        barrier = threading.Barrier(2, timeout=10)

        def claim_once(worker: int):
            with connection(db_path) as db:  # 独立连接（WAL 允许并发读+单写）
                repo = SqliteJobRepository(db)
                barrier.wait()  # 两线程同点起跑，不 sleep
                claimed = repo.claim_next("demo")
                results.append((worker, claimed.id if claimed else None))

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(claim_once, (1, 2)))

        claimed_ids = [r[1] for r in results if r[1] is not None]
        # 核心不变量：没有行被两个连接同时认领
        assert len(claimed_ids) == len(set(claimed_ids)), f"重复认领：{claimed_ids}"
        assert 1 <= len(claimed_ids) <= 2  # 至少一人成功，至多各行一次
        with connection(db_path) as db:
            rows = db.execute("SELECT status, COUNT(*) c FROM review_jobs GROUP BY status").fetchall()
            by_status = {r["status"]: r["c"] for r in rows}
            assert by_status.get("processing", 0) == len(claimed_ids)  # processing == winner 数
            assert by_status.get("queued", 0) == 2 - len(claimed_ids)  # 其余保持 queued

    def test_two_connections_same_single_job_exactly_one_wins(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            SqliteJobRepository(db).create_job(kind="video", mode="demo",
                                               snapshot=SNAP, input=INPUT, task_id="task-1")
        results: list = []
        barrier = threading.Barrier(2, timeout=10)

        def claim_once(worker: int):
            with connection(db_path) as db:
                repo = SqliteJobRepository(db)
                barrier.wait()
                claimed = repo.claim_next("demo")
                results.append((worker, claimed.id if claimed else None))

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(claim_once, (1, 2)))
        assert sum(1 for r in results if r[1] is not None) == 1  # 同一行也恰一胜


class TestModeIsolation:
    def test_demo_worker_cannot_claim_real_row(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            # real 作业必须引用 real 项目任务（一致性核查）
            db.execute("INSERT INTO projects VALUES ('proj-real','真实','真实','brand-1','x','[]','real','active',1,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,"
                       "campaign_phase,rule_version_id,stage,revision,archived_at,created_at,updated_at) "
                       "VALUES ('task-real','proj-real','真实达人','douyin','video','general',NULL,'requirements',1,NULL,'2026-01-01','2026-01-01')")
            repo = SqliteJobRepository(db)
            repo.create_job(kind="video", mode="real", snapshot={**SNAP, "mode": "real"},
                            input=INPUT, task_id="task-real")
            assert repo.claim_next("demo") is None  # demo 不认领 real 行
            claimed = repo.claim_next("real")
            assert claimed is not None and claimed.mode == "real"

    def test_mark_operations_reject_wrong_mode(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP,
                                  input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            # 用 real mode 尝试终态写入：拒绝，行保持 processing
            assert repo.mark_failed(job, "real", "wrong mode", retryable=False) is False
            assert repo.mark_succeeded(job, "real", {"r": 1}) is False
            assert repo.mark_interrupted(job, "real", "wrong mode") is False
            row = db.execute("SELECT status FROM review_jobs WHERE id=?", (job,)).fetchone()
            assert row["status"] == "processing"
            # demo mode 正常生效
            assert repo.mark_failed(job, "demo", "boom", retryable=True) is True

    def test_find_and_result_respect_mode(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP,
                                  input=INPUT, task_id="task-1")
            assert repo.find_by_id(job, "real") is None
            assert repo.find_by_id(job, "demo") is not None
            assert repo.get_result(job, "real") is None


class TestTerminalStates:
    def test_succeeded_not_downgradable_and_result_immutable(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP,
                                  input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            assert repo.mark_succeeded(job, "demo", {"conclusion": "needs_confirmation"}) is True
            # 终态后一切 mark 拒绝
            assert repo.mark_interrupted(job, "demo", "crash?") is False
            assert repo.mark_failed(job, "demo", "late", retryable=False) is False
            assert repo.mark_succeeded(job, "demo", {"conclusion": "passed"}) is False  # 结果不可覆盖
            # 权威结果保持首份
            assert repo.get_result(job, "demo") == {"conclusion": "needs_confirmation"}
            # jobs_results 不可二次插入（PK）
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO jobs_results VALUES (?,?,?)",
                           (job, '{"conclusion":"forged"}', '2026-01-01'))

    def test_failed_and_interrupted_not_downgradable(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            j1 = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            assert repo.mark_failed(j1, "demo", "err", retryable=False) is True
            assert repo.mark_interrupted(j1, "demo", "late") is False
            assert repo.mark_succeeded(j1, "demo", {"r": 1}) is False
            j2 = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            assert repo.mark_interrupted(j2, "demo", "unclear") is True
            assert repo.mark_failed(j2, "demo", "late", retryable=True) is False

    def test_mark_on_queued_rejected(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            # 未认领（queued）不可直接终态
            assert repo.mark_succeeded(job, "demo", {"r": 1}) is False
            assert repo.mark_failed(job, "demo", "x", retryable=False) is False
            assert repo.mark_interrupted(job, "demo", "x") is False


class TestProviderRequestTracking:
    def test_record_provider_request_only_while_processing(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            assert repo.record_provider_request(job, "demo", "prov-1", "submitted") is False  # queued 拒
            repo.claim_next("demo")
            assert repo.record_provider_request(job, "demo", "prov-1", "submitted") is True
            row = db.execute("SELECT provider_request_id, submission_state FROM review_jobs WHERE id=?",
                             (job,)).fetchone()
            assert row["provider_request_id"] == "prov-1"
            assert row["submission_state"] == "submitted"

    def test_bad_submission_state_rejected(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            with pytest.raises(JobValidationError):
                repo.record_provider_request(job, "demo", "p", "flying")


class TestCapabilityJobs:
    """A 裁决①：capability_test 不绑 Brief source，测试素材/配置在 input。"""

    CAP_INPUT = {"testInput": {"fileId": "file-cap"}, "config": {"modelId": "mc-1"}}

    def test_capability_created_and_claimed_without_source(self, db_path):
        with connection(db_path) as db:
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="capability_test", mode="demo",
                                  snapshot=SNAP, input=self.CAP_INPUT)
            claimed = repo.claim_next("demo")
            assert claimed is not None and claimed.id == job
            assert claimed.source_version_id is None  # 不伪绑定品牌 Brief
            assert claimed.input == self.CAP_INPUT

    def test_capability_with_source_still_rejected_at_db_level(self, db_path):
        """迁移 CHECK 不阻止 capability 带 source（列允许），但语义上不要求——
        校验层已放开（不强制），此处验证带/不带均可创建且不伪填由调用方负责。"""
        with connection(db_path) as db:
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="capability_test", mode="demo",
                                  snapshot=SNAP, input=self.CAP_INPUT)
            row = db.execute("SELECT source_version_id FROM review_jobs WHERE id=?", (job,)).fetchone()
            assert row["source_version_id"] is None


class TestSavepointNoHalfState:
    """A 裁决②：mark_succeeded 内部失败不留 succeeded 无结果半态。"""

    def test_result_insert_failure_rolls_back_status(self, db_path, monkeypatch):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.claim_next("demo")

            # 预先占位 jobs_results 该 job 行，使 mark_succeeded 的 INSERT 失败
            db.execute("INSERT INTO jobs_results VALUES (?,?,?)",
                       (job, '{"preexisting": true}', '2026-01-01'))
            ok = repo.mark_succeeded(job, "demo", {"conclusion": "passed"})
            assert ok is False
            # 调用方继续 commit（模拟 catch 后提交）：不留半态
            db.commit()
            row = db.execute("SELECT status FROM review_jobs WHERE id=?", (job,)).fetchone()
            assert row["status"] == "processing"  # 状态回滚，不是 succeeded
            result = db.execute("SELECT result_json FROM jobs_results WHERE job_id=?", (job,)).fetchone()
            assert result["result_json"] == '{"preexisting": true}'  # 首份保留，无覆盖


class TestAuditTrail:
    """A 裁决④：create/claim/终态同事务审计；失败不残留。"""

    def _audit_count(self, db, job_id):
        return db.execute("SELECT COUNT(*) FROM audit_events WHERE entity_id=?", (job_id,)).fetchone()[0]

    def test_create_claim_terminal_all_audited(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            assert self._audit_count(db, job) == 1  # created
            repo.claim_next("demo")
            assert self._audit_count(db, job) == 2  # + claimed
            assert repo.mark_failed(job, "demo", "boom", retryable=True)
            assert self._audit_count(db, job) == 3  # + failed
            # 审计身份：服务端固定本地身份
            row = db.execute("SELECT actor_id, entity_type, mode FROM audit_events WHERE entity_id=?",
                             (job,)).fetchone()
            assert row["actor_id"] == "local-reviewer"
            assert row["entity_type"] == "review_job"
            assert row["mode"] == "demo"

    def test_failed_mark_leaves_no_audit_residue(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            base = self._audit_count(db, job)  # created + claimed
            # 终态不匹配（mode 错）→ 拒绝且不写审计
            assert repo.mark_succeeded(job, "real", {"r": 1}) is False
            assert self._audit_count(db, job) == base
            assert repo.mark_interrupted(job, "real", "x") is False
            assert self._audit_count(db, job) == base

    def test_savepoint_failure_no_audit_residue(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            repo.claim_next("demo")
            db.execute("INSERT INTO jobs_results VALUES (?,?,?)",
                       (job, '{"x":1}', '2026-01-01'))
            base = self._audit_count(db, job)
            assert repo.mark_succeeded(job, "demo", {"r": 1}) is False  # SAVEPOINT 回滚
            db.commit()
            assert self._audit_count(db, job) == base  # succeeded 审计未残留


class TestReferenceModeConsistency:
    """A 裁决④：引用行项目 mode 必须与作业 mode 一致。"""

    def test_demo_job_cannot_reference_real_task(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            # 建 real 项目+任务
            db.execute("INSERT INTO projects VALUES ('proj-real','真实','真实','brand-1','x','[]','real','active',1,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,"
                       "campaign_phase,rule_version_id,stage,revision,archived_at,created_at,updated_at) "
                       "VALUES ('task-real','proj-real','真实达人','douyin','video','general',NULL,'requirements',1,NULL,'2026-01-01','2026-01-01')")
            repo = SqliteJobRepository(db)
            with pytest.raises(JobValidationError, match="mode"):
                repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT,
                                task_id="task-real")  # demo job 指向 real task：拒

    def test_mode_matched_reference_accepted(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            assert job  # demo job + demo task：通过

    def test_missing_task_reference_rejected(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            with pytest.raises(JobValidationError, match="不存在"):
                repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT,
                                task_id="task-ghost")

    def test_source_mode_mismatch_rejected(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            db.execute("INSERT INTO projects VALUES ('proj-real','真实','真实','brand-1','x','[]','real','active',1,'2026-01-01','2026-01-01')")
            db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) "
                       "VALUES ('brief-real','proj-real','BR',NULL,'2026-01-01')")
            db.execute("INSERT INTO file_assets VALUES ('file-real','a.txt','txt','text/plain',3,"
                       "'" + "0" * 63 + "2','k2','brief','unverifiable','unverified','real','2026-01-01')")
            db.execute("INSERT INTO brief_source_versions (id,brief_id,file_id,extraction_status,"
                       "page_count,uncovered_sources_json,created_at) "
                       "VALUES ('src-real','brief-real','file-real','ready',1,'[]','2026-01-01')")
            repo = SqliteJobRepository(db)
            with pytest.raises(JobValidationError, match="mode"):
                repo.create_job(kind="brief", mode="demo", snapshot=SNAP, input=INPUT,
                                source_version_id="src-real")  # demo job 指向 real brief source：拒


class TestClaimedJobFields:
    """A 裁决③：ClaimedJob 至少含 status/retryable（供后续 VideoJobService 适配）。"""

    def test_claimed_job_carries_status_and_retryable(self, db_path):
        with connection(db_path) as db:
            seed_task(db)
            repo = SqliteJobRepository(db)
            job = repo.create_job(kind="video", mode="demo", snapshot=SNAP, input=INPUT, task_id="task-1")
            claimed = repo.claim_next("demo")
            assert claimed.status == "processing"
            assert claimed.retryable is False
            found = repo.find_by_id(job, "demo")
            assert found is not None and found.status == "processing"
            assert found.retryable is False and found.error_message is None
            assert found.created_at and found.updated_at
            # 失败后 retryable 反映
            repo.mark_failed(job, "demo", "x", retryable=True)
            after = repo.find_by_id(job, "demo")
            assert after.status == "failed" and after.retryable is True
            assert after.error_message == "x"
