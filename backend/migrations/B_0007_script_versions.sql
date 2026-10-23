-- B_0007（A 01:02/01:04 批准）：脚本不可变版本登记。
-- 确定性"当前版本"=同任务最大 ordinal（UNIQUE 约束+事务内 max+1 领取），不按时间/UUID 判定。
-- 输入互斥锁死：file 必有 file_id 无 text；text 必有原文无 file_id（粘贴不伪 fileId）。
CREATE TABLE script_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES creator_tasks(id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    version_label TEXT NOT NULL,
    input_type TEXT NOT NULL CHECK(input_type IN ('file','text')),
    file_id TEXT REFERENCES file_assets(id),
    text TEXT,
    extraction_status TEXT NOT NULL CHECK(extraction_status IN ('ready','invalid')),
    source_map_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, ordinal),
    CHECK((input_type='file' AND file_id IS NOT NULL AND text IS NULL)
       OR (input_type='text' AND text IS NOT NULL AND file_id IS NULL))
);
CREATE INDEX script_versions_task_idx ON script_versions(task_id, ordinal DESC);
