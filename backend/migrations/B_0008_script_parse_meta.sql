-- B_0008（t-38，A 12:51 批准）：脚本版本持久化"真实格式"解析元数据。
-- 目的：XLSX/DOCX 的结构化定位、解析告警、未识读图片锚点必须进 ScriptVersionDetail 持久数据，
-- 不能只存在创建响应或本地 ParsedDoc 里，否则后续会误判"文本已完整"。
-- 兼容：新增列默认 '{}'——历史 TXT 行原样读回（无 warnings/uncoveredMedia/partial 字段即视为
-- 文本完整）；**不**为历史 ready 行补写"支持 XLSX/DOCX"之类能力，避免伪回填。
ALTER TABLE script_versions ADD COLUMN parse_meta_json TEXT NOT NULL DEFAULT '{}';
