-- A 前缀：换绑绑定历史与基础失效事件（仅事件先行，不宣称下游已消费）。
CREATE TABLE rule_binding_history (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES creator_tasks(id),
    from_rule_version_id TEXT,
    to_rule_version_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE task_invalidation_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES creator_tasks(id),
    kind TEXT NOT NULL CHECK(kind IN ('rebind','new_script_version','new_video_version')),
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX invalidation_task_idx ON task_invalidation_events(task_id, created_at DESC, id DESC);
