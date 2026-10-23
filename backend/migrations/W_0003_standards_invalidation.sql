CREATE TABLE task_invalidation_events_v3 (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 kind TEXT NOT NULL CHECK(kind IN ('rebind','new_script_version','new_video_version','phase_change','standards_reconfirmed')),
 reason TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}', mode TEXT NOT NULL, created_at TEXT NOT NULL
);
INSERT INTO task_invalidation_events_v3 SELECT * FROM task_invalidation_events ORDER BY rowid;
DROP TABLE task_invalidation_events;
ALTER TABLE task_invalidation_events_v3 RENAME TO task_invalidation_events;
CREATE INDEX invalidation_task_idx ON task_invalidation_events(task_id,created_at DESC,id DESC);
