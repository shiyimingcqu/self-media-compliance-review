-- Durable filesystem cleanup after the project deletion transaction commits.
CREATE TABLE project_file_cleanup (
 path TEXT PRIMARY KEY,
 project_id TEXT NOT NULL,
 created_at TEXT NOT NULL
);
