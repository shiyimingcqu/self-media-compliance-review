# 独立审核标准与 AI 提示词

本次修复 BriefPage 在加载前后 Hook 数量不一致的问题：coverageRunnerRef 始终在条件返回前调用。浏览器测试覆盖初次加载和刷新。

## 品牌发布任务

项目详情 → 新建任务 → 推荐模板或 Brief AI 推荐 → 编辑脚本标准、视频标准 → 品牌分别确认 → 填写意见 → 发布任务。

- 两套标准分别持久化到 task_standard_sets，有独立版本ID、规则ID和确认信息。每条含要求 text、判定口径 criterion、重要性、来源引用和取证策略。
- 模板有通用品牌种草、食品饮品、美妆个护，均为可编辑起点，不是品牌已下达要求。
- 可选择项目中已解析的 Brief。AI 脚本标准推荐和视频标准推荐分别为一次真实请求，需用户明确确认；只生成候选项，需点击应用到对应集合。另一集合不受影响。
- 真实推荐必须引用所选 Brief 的真实文本块与逐字原文；没有来源的通用建议明确由品牌判断。解析遗漏仍需人工核对，模型不能补造图片内容。
- 发布事务同时创建任务与两套确认快照；任何一套未确认、没有必须项、内容不完整或引用无效则整体失败。
- 独立标准不依赖先启用旧版混合规则，模板可以直接发布。旧版规则编辑/文件解析功能保留，已有任务需在脚本/视频页通过「品牌审核 / 重新确认两套标准」完成独立标准确认。
- 重新确认会生成新快照并使当前审核资格回退，需要重新审脚本；历史不删。活动阶段变化后标准须重新确认。

## 执行隔离

脚本请求只读取 script 集合，视频请求只读取 video 集合。视频额外收到已通过脚本文本用于对照，但不会私自追加未经品牌确认的隐藏规则。是否检查脚本执行一致性由品牌确认的视频标准决定（推荐模板包含该项）。

两套标准ID纳入审核上下文指纹，视频快照显式记录 scriptStandardSetId、videoStandardSetId 和脚本品牌确认记录。标准变更、脚本变更等不会复用旧通过资格。

## 每个 AI 环节的提示词

源码位于 backend/app/modules/workflow/prompts/，在模型设置页「各 AI 环节的独立提示词」查看：

| 提示词文件 | 使用环节 |
| --- | --- |
| brief-script-standards.md | Brief → 脚本标准候选项 |
| brief-video-standards.md | Brief → 视频标准候选项 |
| script-review.md | 按品牌确认的脚本标准审核脚本 |
| video-review.md | 完整音视频审查与受控证据工具调用 |
| video-final-report.md | 工具轮次结束后提交结构化报告 |
| connection-test.md | 文本接口连接测试 |

每份提示词使用内容哈希版本；每次调用保存当时提示词内容和版本。业务输入、结构化输出 schema 与工具权限仍由服务端约束，不能被文档里的指令改写。

## 新接口

- GET /api/v1/workflow/standard-templates
- GET /api/v1/workflow/prompts
- POST /api/v1/projects/{projectId}/publish-task
- POST /api/v1/tasks/{taskId}/standards/confirm
- GET/POST /api/v1/projects/{projectId}/standard-recommendations

真实请求不自动重试，幂等键复用不会重复发送，重启会把未终结推荐标为待核实。测试使用模拟模型，不调用真实收费接口。
