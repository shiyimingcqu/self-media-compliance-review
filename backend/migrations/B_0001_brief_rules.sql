-- B 前缀：Brief/规则版本表组（启用业务由 B 域实现；本迁移先建表供 creator_tasks 外键与
-- eligibility 只读校验。state 六值与 design D4 一致；冲突在场即视为未解决）。
CREATE TABLE briefs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    latest_source_version_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE brief_source_versions (
    id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES briefs(id),
    file_id TEXT NOT NULL REFERENCES file_assets(id),
    extraction_status TEXT NOT NULL CHECK(extraction_status IN ('uploaded','extracting','ready','invalid')),
    page_count INTEGER,
    uncovered_sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE rule_versions (
    id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES briefs(id),
    source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
    version_label TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('draft','parsing','parse_failed','pending_confirmation','enabled','superseded')),
    parent_id TEXT REFERENCES rule_versions(id),
    revision INTEGER NOT NULL DEFAULT 1,
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE requirements (
    id TEXT PRIMARY KEY,
    rule_version_id TEXT NOT NULL REFERENCES rule_versions(id),
    lineage_id TEXT NOT NULL,
    text TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 500),
    category TEXT NOT NULL,
    importance TEXT NOT NULL CHECK(importance IN ('must','suggested')),
    platforms_json TEXT NOT NULL,
    content_types_json TEXT NOT NULL,
    phases_json TEXT NOT NULL,
    review_stages_json TEXT NOT NULL,
    exceptions TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    confirmation TEXT NOT NULL DEFAULT 'pending' CHECK(confirmation IN ('pending','confirmed','disabled'))
);
CREATE TABLE rule_conflicts (
    id TEXT PRIMARY KEY,
    rule_version_id TEXT NOT NULL REFERENCES rule_versions(id),
    requirement_ids_json TEXT NOT NULL,
    explanation TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved')),
    resolution TEXT,
    resolved_by TEXT
);
CREATE INDEX requirements_rv_idx ON requirements(rule_version_id);
