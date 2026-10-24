CREATE TABLE review_responses (
 task_id TEXT NOT NULL REFERENCES creator_tasks(id), scope TEXT NOT NULL CHECK(scope IN ('script','video')),
 review_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('modifying','disputing')),
 revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(task_id,scope,review_id)
);
CREATE TABLE review_disputes (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 scope TEXT NOT NULL CHECK(scope IN ('script','video')), review_id TEXT NOT NULL, rule_id TEXT NOT NULL,
 rule_json TEXT NOT NULL, finding_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','needs_info','accepted','rejected')),
 revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(task_id,scope,review_id,rule_id)
);
CREATE INDEX review_disputes_task ON review_disputes(task_id,scope,review_id,status);
CREATE TABLE review_dispute_messages (
 id TEXT PRIMARY KEY, dispute_id TEXT NOT NULL REFERENCES review_disputes(id),
 actor TEXT NOT NULL CHECK(actor IN ('creator','brand')), kind TEXT NOT NULL,
 body TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX review_dispute_message_thread ON review_dispute_messages(dispute_id,created_at);
CREATE TABLE creator_reminders (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 scope TEXT NOT NULL CHECK(scope IN ('script','video')), message TEXT NOT NULL,
 created_at TEXT NOT NULL, read_at TEXT
);
CREATE INDEX creator_reminders_task ON creator_reminders(task_id,created_at);
