-- C_0001（D 实施 · t-22）：共享 ReviewJob 队列与结果权威表。
-- 首批仅持久化/CAS 基础：无 worker、无 API、无恢复接管（需 owner/lease/fencing 另批）。
-- 四 kind 同一队列；task_id 仅 script/video 必填（brief/capability 用 source_version_id，
-- 不伪填）；mode 进所有更新/查询条件（隔离 demo/real）；review_jobs 不存 result 列——
-- 结果唯一权威在 jobs_results（succeeded 同事务写入，终态不可覆盖）。
-- 字典序位于 B_0004 之后：creator_tasks/brief_source_versions/file_assets 前置已到位。
CREATE TABLE review_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('brief','script','video','capability_test')),
    task_id TEXT REFERENCES creator_tasks(id),
    source_version_id TEXT REFERENCES brief_source_versions(id),
    script_version_id TEXT,
    video_file_id TEXT REFERENCES file_assets(id),
    rule_version_id TEXT,
    mode TEXT NOT NULL CHECK(mode IN ('demo','real')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','succeeded','failed','interrupted')),
    snapshot_json TEXT NOT NULL,
    input_json TEXT NOT NULL,
    provider_request_id TEXT,
    submission_state TEXT NOT NULL DEFAULT 'not_sent' CHECK(submission_state IN ('not_sent','submitted','unknown')),
    error_message TEXT,
    retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    retry_of TEXT REFERENCES review_jobs(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- brief/capability 无 task；script/video 必须绑定 task（不伪填）。
    CHECK (kind IN ('brief','capability_test') OR task_id IS NOT NULL)
);
CREATE INDEX review_jobs_claim_idx ON review_jobs(status, created_at, id);
CREATE INDEX review_jobs_task_idx ON review_jobs(task_id, kind, created_at DESC);
CREATE INDEX review_jobs_retry_idx ON review_jobs(retry_of);

-- 结果权威表：一个 succeeded 作业恰好一行；PK 即防重复，终态不可覆盖。
CREATE TABLE jobs_results (
    job_id TEXT PRIMARY KEY REFERENCES review_jobs(id),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
