CREATE TABLE brands (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    brand_id TEXT NOT NULL REFERENCES brands(id),
    description TEXT NOT NULL DEFAULT '',
    platforms_json TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('demo','real')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed','archived')),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(brand_id, normalized_name, mode)
);
CREATE TABLE idempotency_records (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(scope,key)
);
CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    mode TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX projects_updated_idx ON projects(updated_at DESC,id DESC);
