-- B_0004（t-20，A 23:36 批准）：未覆盖来源的人工补录签署。
-- 签署必须列举具体 uncoveredSourceIds 并带补录关联与理由；manualNote 不能替代签署跳过未覆盖页。
CREATE TABLE coverage_confirmations (
    id TEXT PRIMARY KEY,
    rule_version_id TEXT NOT NULL REFERENCES rule_versions(id),
    uncovered_items_json TEXT NOT NULL,
    statement TEXT NOT NULL CHECK(length(statement) BETWEEN 1 AND 500),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
    actor_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('demo','real')),
    created_at TEXT NOT NULL
);
CREATE INDEX coverage_version_idx ON coverage_confirmations(rule_version_id, created_at DESC, id DESC);