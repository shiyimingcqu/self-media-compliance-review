-- B_0003（最小追加，A 23:19 批准）：briefs 增加适用平台列。
-- 初始契约 POST /projects/:id/briefs 输入含 platforms，B_0001 无此列；只追加列，不改已应用 SQL。
-- 既有行默认 '[]' 仅表示待补范围：绝不自动视作全平台，也不能凭默认放行启用；
-- 新登记由服务层写入规范化非空平台集合（项目平台子集）。
ALTER TABLE briefs ADD COLUMN platforms_json TEXT NOT NULL DEFAULT '[]';
