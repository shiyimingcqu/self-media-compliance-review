CREATE TABLE project_current_briefs (
 project_id TEXT PRIMARY KEY REFERENCES projects(id),
 source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
 revision INTEGER NOT NULL CHECK(revision>0),
 updated_at TEXT NOT NULL
);
-- Only adopt an unambiguous existing source. Other historical files remain readable.
INSERT INTO project_current_briefs
SELECT b.project_id, MIN(s.id), 1, MAX(s.created_at)
FROM briefs b JOIN brief_source_versions s ON s.brief_id=b.id
GROUP BY b.project_id HAVING COUNT(*)=1 AND MIN(s.extraction_status)='ready';
