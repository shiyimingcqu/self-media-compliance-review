CREATE TABLE video_reference_images (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 name TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 width INTEGER NOT NULL,
 height INTEGER NOT NULL,
 byte_size INTEGER NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX video_reference_project_idx ON video_reference_images(project_id,created_at);
