CREATE TABLE brief_page_reviews (
    source_version_id TEXT NOT NULL REFERENCES brief_source_versions(id),
    check_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('confirmed','irrelevant')),
    snapshot_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    PRIMARY KEY (source_version_id, check_id)
);
