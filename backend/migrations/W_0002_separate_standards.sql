CREATE TABLE task_standard_sets (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 scope TEXT NOT NULL CHECK(scope IN ('script','video')), rules_json TEXT NOT NULL,
 source_version_id TEXT REFERENCES brief_source_versions(id), platform TEXT NOT NULL, campaign_phase TEXT NOT NULL,
 confirmation_note TEXT NOT NULL, confirmed_by TEXT NOT NULL, confirmed_at TEXT NOT NULL
);
CREATE INDEX task_standard_scope_idx ON task_standard_sets(task_id,scope,confirmed_at DESC);
CREATE TABLE standard_recommendations (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
 scope TEXT NOT NULL, status TEXT NOT NULL, snapshot_json TEXT NOT NULL, result_json TEXT,
 error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- Independent standards do not require a legacy rule_version; keep old records intact.
CREATE TABLE workflow_script_reviews_v2 (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 script_version_id TEXT NOT NULL REFERENCES script_versions(id), rule_version_id TEXT REFERENCES rule_versions(id),
 context_key TEXT NOT NULL, snapshot_json TEXT NOT NULL, status TEXT NOT NULL,
 result_json TEXT, error_code TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO workflow_script_reviews_v2 SELECT * FROM workflow_script_reviews ORDER BY rowid;
DROP TABLE workflow_script_reviews;
ALTER TABLE workflow_script_reviews_v2 RENAME TO workflow_script_reviews;
CREATE INDEX workflow_script_task_idx ON workflow_script_reviews(task_id,created_at DESC);
