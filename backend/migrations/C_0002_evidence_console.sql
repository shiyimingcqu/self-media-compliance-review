CREATE TABLE evidence_model_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    encrypted_key TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE evidence_runs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('connection','video')),
    task_id TEXT REFERENCES creator_tasks(id),
    file_id TEXT REFERENCES file_assets(id),
    status TEXT NOT NULL CHECK(status IN ('queued','processing','succeeded','failed','interrupted')),
    config_revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX evidence_runs_task_idx ON evidence_runs(task_id,created_at DESC);
