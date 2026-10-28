# 2026-10-28 源码同步验证

本次将当前开发工作区整理后同步到 GitHub，包含 Brief 产品事实与四组规则直接提炼、规则编辑与导入、视频规则增量生成去重及审核判断边界等近期修改。服务器更新需单独部署，此次操作只同步源码。

## 已通过

- `npm run build`：TypeScript 检查和生产构建通过；Vite 仍提示部分输出包超过 500 kB。
- `npm run test:frontend`：16 个测试文件、110 项测试通过。
- `npm run spec:validate`：规范校验通过。
- OpenAPI 与前端类型已重新生成。
- 本次规则去重修复专项后端测试 24 项通过，涉及 `test_video_existing_draft.py`、`test_video_rule_generation_prompt.py`、`test_recommendation_transport.py`。
- 浏览器专项测试 3 项通过，涉及 `brief-direct-summary.spec.ts`、`video-rule-cleanup.spec.ts`，覆盖产品事实与规则展示、Brief 导入后连续两次生成、停用状态保留及旧 AI 草稿清理。模型请求使用固定响应，不产生模型费用。

## 全量后端检查尚未全部通过

`python -m pytest -c backend/pyproject.toml backend/tests -q` 已完整执行，但存在失败。当前测试集共收集 932 项，不能将本次同步视为全量验收通过。

失败涉及以下范围：

- `test_review_jobs_repository.py`、`test_worker_run_once.py`：测试夹具直接使用无列名的项目插入语句，与当前 12 列项目表不匹配，报 11 个值不足。
- `test_creator_gate.py`：审核队列预期与当前结果不一致。
- `test_decomposition_model.py`：模型请求材料的旧断言触发 KeyError。
- `test_evidence_console.py`：重启恢复检查失败。
- `test_migrations_upgrade.py`：迁移检查失败。
- `test_rule_enable_toggle.py`、`test_script_citations.py`、`test_video_layers.py`：规则开关、引文和生成提示词相关断言失败。

以上失败尚未逐项修复或判定是否属于业务缺陷；不通过删除测试、跳过检查或更改业务行为来掩盖结果。未运行全量浏览器测试，未发起真实付费模型验收。

## 发布范围

按 `scripts/export-github.py` 白名单同步源码、测试、契约及文档；排除本地数据库、上传原件、运行配置、密钥、依赖目录、构建产物与旧 Git 历史。已进行敏感文件路径及常见凭据模式检查。
