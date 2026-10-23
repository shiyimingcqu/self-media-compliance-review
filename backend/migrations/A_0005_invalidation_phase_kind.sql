-- A_0005：扩展 task_invalidation_events 的 kind 集合，加入 phase_change（A 裁决 (a)：
-- phase 变化必须进同一 invalidation 流，不改已应用 A_0004）。
-- SQLite CHECK 约束不可 ALTER，走重建：新表定义→全量搬迁→删旧→改名→重建索引。
-- migrate() 将本脚本与版本记录置于同一 BEGIN IMMEDIATE 事务：中途失败整体回滚，旧表原样保留。
CREATE TABLE task_invalidation_events_new (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES creator_tasks(id),
    kind TEXT NOT NULL CHECK(kind IN ('rebind','new_script_version','new_video_version','phase_change')),
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO task_invalidation_events_new SELECT id,task_id,kind,reason,details_json,mode,created_at FROM task_invalidation_events;
DROP TABLE task_invalidation_events;
ALTER TABLE task_invalidation_events_new RENAME TO task_invalidation_events;
DROP INDEX IF EXISTS invalidation_task_idx;
CREATE INDEX invalidation_task_idx ON task_invalidation_events(task_id, created_at DESC, id DESC);
