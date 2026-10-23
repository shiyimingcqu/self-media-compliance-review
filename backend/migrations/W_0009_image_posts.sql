-- Independent deliverable channel; does not rewrite legacy video task contracts.
CREATE TABLE image_post_assets (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 original_name TEXT NOT NULL, source_sha256 TEXT NOT NULL, sha256 TEXT NOT NULL,
 width INTEGER NOT NULL, height INTEGER NOT NULL, byte_size INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX image_post_asset_task ON image_post_assets(task_id,created_at);
CREATE TABLE image_post_versions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 ordinal INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, images_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(task_id,ordinal)
);
CREATE TABLE image_post_reviews (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 version_id TEXT NOT NULL REFERENCES image_post_versions(id), context_key TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('processing','succeeded','failed','interrupted')),
 result_json TEXT, error_code TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX image_post_review_context ON image_post_reviews(task_id,context_key,created_at);
CREATE TABLE image_post_decisions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES creator_tasks(id),
 review_id TEXT NOT NULL REFERENCES image_post_reviews(id), context_key TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approved','returned')), note TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX image_post_decision_task ON image_post_decisions(task_id,created_at);
