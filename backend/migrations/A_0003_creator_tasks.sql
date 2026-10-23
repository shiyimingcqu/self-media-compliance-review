-- A 前缀：达人任务。rule_version_id 前向引用 B_0001 的 rule_versions：SQLite 允许建表时引用
-- 尚未存在的表，但启动 migrate 会按字典序（0001→A_0002→A_0003→A_0004→B_0001）在服务前
-- 应用全部迁移；服务与测试均以 foreign_key_check 为空为准入证据（见 test_migrations_upgrade.py）。
CREATE TABLE creator_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    creator_name TEXT NOT NULL,
    platform TEXT NOT NULL CHECK(platform IN ('douyin','xiaohongshu','bilibili')),
    content_type TEXT NOT NULL CHECK(content_type = 'video'),
    campaign_phase TEXT NOT NULL CHECK(campaign_phase IN ('warmup','seeding','general')),
    rule_version_id TEXT REFERENCES rule_versions(id),
    stage TEXT NOT NULL CHECK(stage IN ('requirements','script','video','human','completed')),
    revision INTEGER NOT NULL DEFAULT 1,
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX creator_tasks_project_idx ON creator_tasks(project_id, updated_at DESC, id DESC);
