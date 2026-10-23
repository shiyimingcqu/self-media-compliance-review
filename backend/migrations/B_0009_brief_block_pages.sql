-- B_0009（t-40，A 14:01 批准）：PDF Brief 真实物理页号落到文本块。
-- 只新增**缺的** brief_text_blocks.page_no；brief_source_versions 的 page_count(15) /
-- uncovered_sources_json(16) 在 B_0001 已存在，**不重复 ALTER**（A 实读纠正）。
-- 可空：TXT 等无页概念的历史/新行保持 NULL，不伪造页号、不破坏旧数据。
ALTER TABLE brief_text_blocks ADD COLUMN page_no INTEGER
    CHECK (page_no IS NULL OR page_no >= 1);
CREATE INDEX brief_text_blocks_page_idx ON brief_text_blocks(source_version_id, page_no);
