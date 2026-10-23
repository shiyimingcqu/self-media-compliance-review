-- A 前缀公共迁移：受控文件资产。原件不可覆盖，storage_key 仅服务端可见。
CREATE TABLE file_assets (
    id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    mime TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    storage_key TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL CHECK(purpose IN ('brief','script','video')),
    signature_state TEXT NOT NULL CHECK(signature_state IN ('verified','unverifiable')),
    media_check TEXT NOT NULL DEFAULT 'unverified',
    mode TEXT NOT NULL CHECK(mode IN ('demo','real')),
    created_at TEXT NOT NULL
);
CREATE INDEX file_assets_created_idx ON file_assets(created_at DESC, id DESC);
