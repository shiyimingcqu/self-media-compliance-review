"""迁移顺序证据（A 22:16）：字典序执行 0001→A_0002→A_0003→A_0004→B_0001，
A_0003 前向引用 rule_versions 仅在全部迁移完成后才可写；两条路径均以 foreign_key_check 为空准入。"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import connection, migrate

EXPECTED_ORDER = ["0001_foundation.sql", "A_0002_file_assets.sql", "A_0003_creator_tasks.sql",
                  "A_0004_invalidation.sql", "A_0005_invalidation_phase_kind.sql", "B_0001_brief_rules.sql",
                  "B_0002_brief_registration.sql", "B_0003_brief_platforms.sql", "B_0004_coverage_confirmations.sql",
                  "B_0005_coverage_links.sql", "B_0006_coverage_item_links.sql", "B_0007_script_versions.sql",
                  "B_0008_script_parse_meta.sql", "B_0009_brief_block_pages.sql",
                  "C_0001_review_jobs.sql", "C_0002_evidence_console.sql", "C_0003_evidence_input_limit.sql",
                  "D_0001_human_reviews.sql", "W_0001_workflow.sql", "W_0002_separate_standards.sql",
                  "W_0003_standards_invalidation.sql", "W_0004_video_reference_images.sql", "W_0005_project_standards.sql", "W_0006_project_image_standards.sql", "W_0007_project_current_brief.sql", "W_0008_brief_visual_runs.sql", "W_0009_image_posts.sql", "W_0010_decomposition_model.sql",
                  "W_0011_brief_facts.sql", "W_0012_brief_processing.sql", "W_0013_brief_project_import.sql", "W_0014_brief_page_reviews.sql"]


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def test_fresh_database_applies_all_then_fk_clean(tmp_path):
    db_path = tmp_path / "fresh.sqlite3"
    migrate(db_path)
    with sqlite3.connect(db_path) as db:
        applied = [r[0] for r in db.execute("SELECT name FROM schema_migrations ORDER BY applied_at,name")]
        assert applied == EXPECTED_ORDER
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        # 前向 FK 在全部迁移后可写且受约束
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO brands VALUES ('b1','品牌','品牌')")
        db.execute("INSERT INTO projects (id,name,normalized_name,brand_id,description,platforms_json,mode,"
                   "created_at,updated_at) VALUES ('p1','n','n','b1','','[]','demo',?,?)", (stamp(), stamp()))
        db.execute("INSERT INTO file_assets VALUES ('f1','x.pdf','pdf','application/pdf',1,?,?,'brief','verified','unverified','demo',?)",
                   ("0" * 64, str(tmp_path / 'x'), stamp()))
        db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) VALUES ('br1','p1','t',NULL,?)", (stamp(),))
        db.execute("INSERT INTO brief_source_versions VALUES ('s1','br1','f1','ready',1,'[]',?)", (stamp(),))
        db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,state,revision,created_at) "
                   "VALUES ('rv1','br1','s1','v1','enabled',1,?)", (stamp(),))
        db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,campaign_phase,"
                   "rule_version_id,stage,revision,created_at,updated_at) VALUES ('t1','p1','c','douyin','video',"
                   "'general','rv1','requirements',1,?,?)", (stamp(), stamp()))
        db.commit()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        # executescript/commit 不改变连接，但 foreign_keys 是连接级开关：显式重开再验证悬空引用
        db.execute("PRAGMA foreign_keys=ON")
        try:  # 悬空引用必须被拒（FK 实际生效而非仅建表成功）
            db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,campaign_phase,"
                       "rule_version_id,stage,revision,created_at,updated_at) VALUES ('t2','p1','c2','douyin','video',"
                       "'general','nonexistent','requirements',1,?,?)", (stamp(), stamp()))
            db.commit()
            assert False, "FK did not enforce"
        except sqlite3.IntegrityError:
            db.rollback()


def test_existing_database_upgrade_fk_clean(tmp_path):
    """既有库（仅 0001/A_0002/A_0003 时代的应用记录+数据）升级 A_0004/A_0005/B_0001 后 fk_check 为空。"""
    db_path = tmp_path / "upgrade.sqlite3"
    migrations_dir = Path("backend/migrations").resolve()
    # 阶段一：只应用当时存在的迁移
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        for name in ["0001_foundation.sql", "A_0002_file_assets.sql", "A_0003_creator_tasks.sql"]:
            script = (migrations_dir / name).read_text(encoding="utf-8")
            db.executescript("BEGIN IMMEDIATE;\n" + script
                             + f"\nINSERT INTO schema_migrations VALUES ('{name}', '{stamp()}');\nCOMMIT;")
        db.execute("INSERT INTO brands VALUES ('b','品牌','品牌')")
        db.execute("INSERT INTO projects (id,name,normalized_name,brand_id,description,platforms_json,mode,created_at,updated_at) "
                   "VALUES ('p','n','n','b','','[]','demo',?,?)", (stamp(), stamp()))
        db.commit()
        partial = [r[0] for r in db.execute("SELECT name FROM schema_migrations")]
    assert partial[-1] == "A_0003_creator_tasks.sql"
    # 阶段二：应用 A_0004 并在旧 kind 集合下写入一条事件，模拟"已有事件库"
    with sqlite3.connect(db_path) as db:
        script = (migrations_dir / "A_0004_invalidation.sql").read_text(encoding="utf-8")
        db.executescript("BEGIN IMMEDIATE;\n" + script
                         + f"\nINSERT INTO schema_migrations VALUES ('A_0004_invalidation.sql', '{stamp()}');\nCOMMIT;")
        db.execute("INSERT INTO creator_tasks (id,project_id,creator_name,platform,content_type,campaign_phase,"
                   "stage,revision,created_at,updated_at) VALUES ('t','p','c','douyin','video','general',"
                   "'requirements',1,?,?)", (stamp(), stamp()))
        db.execute("INSERT INTO task_invalidation_events (id,task_id,kind,reason,mode,created_at) "
                   "VALUES ('e','t','rebind','旧事件','demo',?)", (stamp(),))
        db.commit()
    # 阶段三：migrate 增量 A_0005/B_0001 —— 重建表保全旧行、phase_change 新值可用、fk 干净
    migrate(db_path)
    with sqlite3.connect(db_path) as db:
        applied = [r[0] for r in db.execute("SELECT name FROM schema_migrations ORDER BY applied_at,name")]
        assert applied == EXPECTED_ORDER
        assert db.execute("SELECT kind,reason FROM task_invalidation_events WHERE id='e'").fetchone() == ("rebind", "旧事件")
        db.execute("INSERT INTO task_invalidation_events (id,task_id,kind,reason,mode,created_at) "
                   "VALUES ('e2','t','phase_change','新值','demo',?)", (stamp(),))
        try:
            db.execute("INSERT INTO task_invalidation_events (id,task_id,kind,reason,mode,created_at) "
                       "VALUES ('e3','t','bogus','x','demo',?)", (stamp(),))
            assert False, "CHECK constraint lost after rebuild"
        except sqlite3.IntegrityError:
            db.rollback()
        idx = db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='invalidation_task_idx'").fetchall()
        assert idx, "index not rebuilt"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.commit()


def test_b0003_upgrade_preserves_old_brief_rows(tmp_path):
    """旧库（无 platforms_json）升级 B_0003：既有 brief 行保留，默认 '[]' 仅表示待补范围，
    不自动视作全平台（默认放行须由启用门禁另行拒绝）。"""
    db_path = tmp_path / "old-briefs.sqlite3"
    migrations_dir = Path("backend/migrations").resolve()
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        for name in ["0001_foundation.sql", "A_0002_file_assets.sql", "A_0003_creator_tasks.sql",
                     "A_0004_invalidation.sql", "A_0005_invalidation_phase_kind.sql", "B_0001_brief_rules.sql",
                     "B_0002_brief_registration.sql"]:
            script = (migrations_dir / name).read_text(encoding="utf-8")
            db.executescript("BEGIN IMMEDIATE;\n" + script
                             + f"\nINSERT INTO schema_migrations VALUES ('{name}', '{stamp()}');\nCOMMIT;")
        db.execute("INSERT INTO brands VALUES ('b','品牌','品牌')")
        db.execute("INSERT INTO projects (id,name,normalized_name,brand_id,description,platforms_json,mode,created_at,updated_at) "
                   "VALUES ('p','n','n','b','','[\"xiaohongshu\"]','demo',?,?)", (stamp(), stamp()))
        db.execute("INSERT INTO briefs (id,project_id,title,latest_source_version_id,created_at) VALUES ('br1','p','旧登记',NULL,?)",
                   (stamp(),))
        db.commit()
    migrate(db_path)
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT title, platforms_json FROM briefs WHERE id='br1'").fetchone()
        assert row is not None and row[0] == "旧登记"
        assert row[1] == "[]"  # 待补范围标记，不是全平台
        assert "B_0003_brief_platforms.sql" in [r[0] for r in db.execute("SELECT name FROM schema_migrations")]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_failure_rolls_back_whole(tmp_path):
    """A_0005 事务性：脚本中途失败时旧表原样保留且不记录版本（migrate 的连接事务整体回滚）。"""
    import pytest
    db_path = tmp_path / "rollback.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        db.execute("CREATE TABLE task_invalidation_events (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,"
                   " reason TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}', mode TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("INSERT INTO task_invalidation_events VALUES ('keep','t','rebind','r','{}','demo','now')")
        db.commit()
    broken_script = ("BEGIN IMMEDIATE;\n"
                     "CREATE TABLE task_invalidation_events_new (id TEXT PRIMARY KEY);"
                     "\nINSERT INTO task_invalidation_events_new SELECT id FROM nonexistent_table_source;\nCOMMIT;")
    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(db_path) as db:
            db.executescript(broken_script)
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT kind FROM task_invalidation_events WHERE id='keep'").fetchone()
        assert row == ("rebind",)  # 失败整体回滚，旧行原样
        assert db.execute("SELECT name FROM sqlite_master WHERE name='task_invalidation_events_new'").fetchall() == []
