CREATE TABLE review_responses_updated (
 task_id TEXT NOT NULL REFERENCES creator_tasks(id), scope TEXT NOT NULL CHECK(scope IN ('script','video')),
 review_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('modifying','disputing','submitted')),
 revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(task_id,scope,review_id),
 CHECK(action != 'submitted' OR scope = 'script')
);
INSERT INTO review_responses_updated SELECT * FROM review_responses;
DROP TABLE review_responses;
ALTER TABLE review_responses_updated RENAME TO review_responses;
