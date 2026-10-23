CREATE TABLE project_standard_sets (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 scope TEXT NOT NULL CHECK(scope IN ('script','video')),
 rules_json TEXT NOT NULL,
 source_version_id TEXT REFERENCES brief_source_versions(id),
 confirmation_note TEXT NOT NULL,
 confirmed_by TEXT NOT NULL,
 confirmed_at TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0)
);
CREATE UNIQUE INDEX project_standard_revision_idx ON project_standard_sets(project_id,scope,revision);
