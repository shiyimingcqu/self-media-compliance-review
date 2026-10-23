CREATE TABLE brief_processing_runs (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
 status TEXT NOT NULL CHECK(status IN ('queued','vision','facts','succeeded','blocked','failed','interrupted','superseded')),
 vision_run_id TEXT,
 facts_run_id TEXT,
 error_code TEXT,
 error_message TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX brief_processing_active ON brief_processing_runs(source_version_id) WHERE status IN ('queued','vision','facts');
