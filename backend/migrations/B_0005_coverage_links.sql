-- B_0005（A 00:10 批准，最小追加）：覆盖签署与补录要求的真实关联。
-- 不改已应用 B_0004；旧签署行保留（无关联=历史事实），但 enable 不再承认无关联的旧签名。
CREATE TABLE coverage_confirmation_requirements (
    confirmation_id TEXT NOT NULL REFERENCES coverage_confirmations(id),
    requirement_id TEXT NOT NULL REFERENCES requirements(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (confirmation_id, requirement_id)
);