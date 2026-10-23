-- B_0006（A 00:40 批准，只追加；不改 B_0004/B_0005）：未覆盖项逐条补录映射。
-- source_snapshot_json 记录签署时补录要求的来源声明，enable 校验其未被改写（A 要求的"来源声明未改"）。
-- 历史 B_0005 关联行保留为事实记录，但不自动构成逐 item 有效覆盖。
CREATE TABLE coverage_item_links (
    confirmation_id TEXT NOT NULL REFERENCES coverage_confirmations(id),
    uncovered_item TEXT NOT NULL,
    requirement_id TEXT NOT NULL REFERENCES requirements(id),
    note TEXT NOT NULL CHECK(length(note) BETWEEN 1 AND 500),
    source_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (confirmation_id, uncovered_item)
);