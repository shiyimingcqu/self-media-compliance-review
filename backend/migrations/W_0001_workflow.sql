CREATE TABLE workflow_script_reviews (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 script_version_id TEXT NOT NULL REFERENCES script_versions(id), rule_version_id TEXT NOT NULL REFERENCES rule_versions(id),
 context_key TEXT NOT NULL, snapshot_json TEXT NOT NULL, status TEXT NOT NULL,
 result_json TEXT, error_code TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX workflow_script_task_idx ON workflow_script_reviews(task_id,created_at DESC);
CREATE TABLE workflow_decisions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id), scope TEXT NOT NULL CHECK(scope IN ('script','video')),
 target_id TEXT NOT NULL, context_key TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('approved','returned')),
 note TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX workflow_decision_task_idx ON workflow_decisions(task_id,created_at DESC);
