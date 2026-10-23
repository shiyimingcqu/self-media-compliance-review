-- D_0001（D t-34）：人工复核持久化首批。
-- 门禁数据唯一来源=review_jobs+jobs_results（真实作业产物，不造 AI 资格）。
-- UNIQUE(job_id)=一作业一当前待办（幂等回放用；Idempotency-Key 指纹独立处理）。
-- UNIQUE(review_id)=一复核一最终决定（终态不可重复提交）。
-- scope 由服务端依 job.kind 决定（script|video），不信任客户端。
CREATE TABLE human_reviews (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES review_jobs(id),
    task_id TEXT NOT NULL REFERENCES creator_tasks(id),
    scope TEXT NOT NULL CHECK(scope IN ('script','video')),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','draft','approved','returned','superseded')),
    mode TEXT NOT NULL CHECK(mode IN ('demo','real')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX human_reviews_state_idx ON human_reviews(state, updated_at DESC, id DESC);
CREATE INDEX human_reviews_task_idx ON human_reviews(task_id, created_at DESC);

CREATE TABLE review_issue_decisions (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES human_reviews(id),
    issue_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('confirmed','false_positive','deferred')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(review_id, issue_key)
);

CREATE TABLE review_drafts (
    review_id TEXT PRIMARY KEY REFERENCES human_reviews(id),
    draft_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE final_decisions (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL UNIQUE REFERENCES human_reviews(id),
    decision TEXT NOT NULL CHECK(decision IN ('approved','returned')),
    comment TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    result_ref TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0 CHECK(simulated IN (0,1)),
    created_at TEXT NOT NULL
);
