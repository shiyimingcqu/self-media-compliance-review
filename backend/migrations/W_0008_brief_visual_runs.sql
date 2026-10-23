CREATE TABLE brief_visual_runs (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
 status TEXT NOT NULL CHECK(status IN ('queued','processing','succeeded','failed','interrupted')),
 snapshot_json TEXT NOT NULL,
 result_json TEXT,
 error_code TEXT,
 error_message TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX brief_visual_source_idx ON brief_visual_runs(source_version_id,created_at);
