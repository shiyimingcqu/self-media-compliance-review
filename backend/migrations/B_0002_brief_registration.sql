-- B_0002：Brief 登记切片的**最小追加**（A 22:43：按当前表缺口最小化，不新建 brief_jobs 队列）。
-- 缺口 1：一个文件可被重复登记成多个来源版本 → 加唯一索引，使"已绑定他项目/已绑定本项目"可判定。
-- 缺口 2：TXT 文本来源需要可验证落库（真实同步解析结果，不是作业假态）→ 段落映射表。
CREATE UNIQUE INDEX brief_source_version_file_unique ON brief_source_versions(file_id);
CREATE TABLE brief_text_blocks (
    id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
    ordinal INTEGER NOT NULL,
    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK(end_offset >= start_offset),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_version_id, ordinal)
);
CREATE INDEX brief_text_blocks_source_idx ON brief_text_blocks(source_version_id, ordinal);
