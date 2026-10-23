ALTER TABLE projects ADD COLUMN product_names_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE brief_project_imports (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES file_assets(id),
    status TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    result_json TEXT,
    project_id TEXT REFERENCES projects(id),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
