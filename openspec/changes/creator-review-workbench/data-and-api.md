# 数据与接口契约

这是待实现契约，描述接口与数据语义，不代表接口已经运行。字段采用 camelCase，ID 为服务端生成的不透明字符串；时间戳为 ISO 8601 UTC，视频区间为毫秒。基础路径 `/api/v1`。

## 1. 核心实体

| 实体 | 关键字段 | 关联与约束 |
|---|---|---|
| Brand | id, name, createdAt | 名称去空白；本期通过项目表单内联新增 |
| Project | id, name, brandId, description, platforms[], status, mode, revision | 同品牌同模式下名称唯一；platforms 为 douyin/xiaohongshu/bilibili |
| CreatorTask | id, projectId, creatorName, platform, contentType, campaignPhase, ruleVersionId, stage, revision, archivedAt | contentType 本期只允许 video；平台必须属于项目 |
| FileAsset | id, originalName, extension, mime, byteSize, sha256, storageKey, mode, createdAt | storageKey 仅服务端可见；原始文件不可覆盖 |
| Brief | id, projectId, title, latestSourceVersionId, enabledRuleVersionIds[] | 允许多平台/阶段存在不同启用版本；同一范围切换启用需显式操作 |
| BriefSourceVersion | id, briefId, fileId, extractionStatus, pageCount, uncoveredSources[], createdAt | 每次重传新建；解析结果与原件绑定 |
| RuleVersion | id, briefId, sourceVersionId, versionLabel, state, parentId, revision, confirmedBy, confirmedAt | enabled 之后不可修改；草稿 parentId 指向来源 |
| Requirement | id, ruleVersionId, lineageId, text, category, importance, platforms[], contentTypes[], phases[], reviewStages[], exceptions, source, confirmation | id 是快照内 ID；lineageId 跨规则版本追踪同一要求 |
| RuleConflict | id, ruleVersionId, requirementIds[], explanation, state, resolution, resolvedBy | 未解决冲突阻止启用，理由不可空 |
| ScriptVersion | id, taskId, versionLabel, inputType, fileId?, text?, extractionStatus, sourceMap, createdAt | inputType=file/text，二选一；Excel 来源 sheet/cell |
| VideoVersion | id, taskId, fileId, durationMs, width, height, audioTracks[], codec, integrityStatus, createdAt | 时长与音轨由服务端探测，不能只信任浏览器 |
| ModelConfig | id, revision, provider, baseUrl, textModel, videoModel, hasKey, maskedKey, mode, timeoutSec, concurrency, retryCount | 不返回明文或加密密钥；快照保存不可变引用 |
| CapabilityReport | id, configFingerprint, testedAt, textStatus, videoStatus, audioStatus, supportedContainers[], maxBytes?, maxDurationMs?, source, evidence | 未知限制为 null；报告绑定服务商/地址/模型/凭证版本，不只模型名 |
| ReviewJob | id, taskId, kind, status, snapshot, providerRequestId?, error?, attempt, retryOf?, createdAt, updatedAt | kind=brief/script/video；快照包含输入、模式、配置、规则版本；重试新记录 |
| RuleResult | id, jobId, requirementId, verdict, rationale, evidence[], notApplicableReason? | 一作业每规则最多一条；verdict 四值；完整检查才能成功 |
| Issue | id, jobId, ruleResultId?, category, importance, startMs?, endMs?, sourceRef?, description, suggestion, origin | origin=ai/human；脚本证据为来源，视频为时间区间 |
| HumanReview | id, taskId, scope, jobId, snapshot, state, draft, revision | scope=script/video；每个有效快照只有一个当前待办，可有历史 superseded |
| IssueDecision | id, reviewId, issueId, action, reason, actorId, createdAt | action=confirmed/false_positive/deferred；追加保留旧裁决 |
| FinalDecision | id, reviewId, decision, comment, actorId, snapshot, createdAt | approved/returned；必须事务检查门禁，不接受客户端直接写任务完成 |
| AuditEvent | id, actorId, entityType, entityId, action, beforeRef?, afterRef?, createdAt, mode | 只追加，记录版本引用和理由，不写密钥/大文件正文 |

source 结构：`{fileId, page?, paragraphId?, sheet?, cell?, quote, bounds?, manualNote?}`。手动补充不伪造页码，用 manualNote 说明品牌补充。视频无时间定位但表示全程缺失的要求，使用范围型证据 `{scope:'whole-video', description}`，不编造不存在的具体发生时刻。

snapshot 至少包含 `{mode, ruleVersionId, scriptVersionId?, videoVersionId?, modelConfigId, modelConfigRevision, capabilityReportId?}`。Brief 作业使用 sourceVersionId 代替任务材料引用。所有 ID 必须校验项目、任务归属。

## 2. 通用协议

- 成功单资源：`{data: {...}, requestId}`。列表：`{data: [...], page, pageSize, total, requestId}`。
- 异步任务返回 HTTP 202：`{data:{jobId,status:'queued'},requestId}`；轮询 GET `/jobs/:id`。
- 错误：`{error:{code,message,fieldErrors?,details?,retryable},requestId}`；错误不包含供应商凭证、内网堆栈或原始签名 URL。
- 写请求携带 `Idempotency-Key`；相同 key 相同 payload 返回原结果；不同 payload 返回 409。范围包括 actor、endpoint、mode；建议保留至少 24 小时，最终决定另有业务唯一约束。
- 修改草稿、配置、换绑和最终提交使用 `If-Match` revision；缺失返回 428，过期返回 409（统一业务冲突响应）。
- 列表参数：`q, platform, status, page=1, pageSize=10, sort=updatedAt:desc`；pageSize 仅 10/20/50。mode 来自已生效上下文；显式查历史演示数据必须标明，不允许通过参数伪装真实结果。
- 时间、名称和 ID 校验由后端重复执行，不依赖前端按钮禁用。
- 文件下载和流式播放通过受控 fileId 接口；支持 HTTP Range。后续共享部署必须进行身份及项目授权检查。

## 3. 接口清单

### 3.1 公共及项目

| 方法与路径 | 输入 | 输出与行为 |
|---|---|---|
| GET `/context` | 无 | 当前审核身份、时区、模式、应用限制、已实现服务商列表 |
| GET `/dashboard` | 当前模式 | 全局统计、比较周期、历史是否可用；不随列表筛选变化 |
| GET `/brands` | q | 品牌选项，供创建项目筛选 |
| POST `/projects` | name, brandId 或 brandName, description, platforms | 201，新项目；品牌与项目创建同事务 |
| GET `/projects` | 通用列表参数、brandId | 项目列表及由真实任务计算的进度 |
| GET `/projects/:id` | 无 | 项目详情、平台计数、Brief 概览 |
| PATCH `/projects/:id` | 名称/说明/品牌/平台 + revision | 修改；平台关联保护 |
| POST `/projects/:id/archive` | revision | 无运行作业才归档 |
| POST `/projects/:id/restore` | revision | 恢复，保留历史 |
| GET `/projects/:id/stats` | platform | 阶段互斥分组和其他处理中 |
| GET `/projects/:id/tasks` | 通用参数、stage | 达人任务，区分当前状态与历史 |
| POST `/projects/:id/tasks` | creatorName, platform, contentType, campaignPhase, ruleVersionId | 201；同达人疑似重复返回提示，不静默合并 |
| PATCH `/tasks/:id` | 可修改资料 + revision | 平台/阶段变更检查兼容绑定 |
| POST `/tasks/:id/rebind` | ruleVersionId, reason, acknowledgeInvalidation, revision | 换绑、作废当前通过资格并追加历史 |
| POST `/tasks/:id/archive` | revision | 无运行作业则归档，相关待办 superseded |
| GET `/tasks/:id/history` | cursor? | 版本、作业、人工记录 |

### 3.2 文件、Brief 与规则

| 方法与路径 | 输入 | 输出与行为 |
|---|---|---|
| POST `/files` | multipart file, purpose | 201 fileId、元信息、校验结果；purpose=brief/script/video，按用途验证 |
| GET `/files/:id/content` | Range? | 受控原件下载/播放；不接受任意本地路径 |
| GET `/files/:id/preview` | page?/sheet? | 解析后预览与来源映射；扫描页原件预览不等于 OCR 成功 |
| POST `/projects/:id/briefs` | fileId,title,platforms | Brief、sourceVersionId；文件须属于当前模式 |
| POST `/briefs/:id/sources` | fileId | 新源版本，不覆盖旧版 |
| POST `/briefs/:id/parse` | sourceVersionId | 202，持久化解析作业 |
| GET `/briefs/:id/rule-versions` | 无 | 全部规则版本、确认人与摘要 |
| POST `/briefs/:id/rule-versions` | sourceVersionId,parentId? | 新草稿；编辑启用版先复制为草稿 |
| GET `/rule-versions/:id` | 无 | 要求、冲突、统计、未覆盖来源和 revision |
| POST `/rule-versions/:id/requirements` | 规则字段 | 添加品牌补充规则 |
| PATCH `/requirements/:id` | 规则字段、revision | 仅 draft/pending_confirmation 可改 |
| POST `/rule-versions/:id/confirm` | requirementIds[],revision | 批量确认，返回 confirmedIds/skipped[] |
| POST `/conflicts/:id/resolve` | resolution, affectedRequirementChanges, revision | 原子解决冲突并记录理由 |
| POST `/rule-versions/:id/coverage-confirmation` | uncoveredSourceIds[],补录记录,reason,revision | 人工确认未覆盖页，形成覆盖记录 |
| POST `/rule-versions/:id/enable` | revision,changeSummary | 启用门禁及不可变快照，返回正式版本号 |

保存草稿通过上述规则编辑持久化；页面“保存草稿”提交当前未保存改动的批次，建议实现 PATCH `/rule-versions/:id/draft` 原子更新规则与 revision，避免一半成功后误显示全保存。

### 3.3 脚本与视频审核

| 方法与路径 | 输入 | 输出与行为 |
|---|---|---|
| GET `/tasks/:id/scripts` | 无 | 脚本版本列表、当前资格及历史作业 |
| POST `/tasks/:id/scripts` | inputType,fileId 或 text | 201，不可变脚本版本；创建解析任务（若需要） |
| GET `/script-versions/:id` | 无 | 预览、sourceMap、解析状态 |
| GET `/tasks/:id/script-diff` | from,to | 段落或单元格差异；校验两版属于该任务 |
| POST `/tasks/:id/script-jobs` | scriptVersionId,ruleVersionId | 202；检查模式、启用规则、当前版本 |
| GET `/jobs/:id` | 无 | 状态、实际进度、快照、脱敏错误 |
| GET `/jobs/:id/results` | verdict?,category? | 完整规则结果、问题、摘要、完整性 |
| POST `/jobs/:id/retry` | reason? | 202，新作业关联 retryOf，安全重试检查 |
| GET `/jobs/:id/report` | format=markdown/json | 附件下载，包含模式与快照 |
| GET `/tasks/:id/videos` | 无 | 视频版本及各自结果 |
| POST `/tasks/:id/videos` | fileId,scriptVersionId | 201，视频版本和完整性探测作业；脚本资格校验 |
| GET `/video-versions/:id` | 无 | 元数据、音轨、完整性及播放 fileId |
| POST `/tasks/:id/video-jobs` | videoVersionId,scriptVersionId,ruleVersionId | 202；检查完整性、模型能力、三方版本 |
| POST `/jobs/:id/human-reviews` | reason?,scope | 创建或返回对应快照待办；服务端确定待办类型 |

示例视频结果（只说明结构，不代表样例实际已审核）：

```json
{
  "jobId": "job_demo_structure",
  "mode": "demo",
  "conclusion": "needs_confirmation",
  "coverage": {"analyzed": 10, "applicable": 10, "ratio": 1},
  "mediaAnalysis": {"video": "complete", "audio": "complete"},
  "issues": [{
    "id": "issue_1",
    "requirementId": "rule_1",
    "category": "spoken",
    "startMs": 28000,
    "endMs": 36000,
    "description": "示意问题，非真实审核结果",
    "suggestion": "由审核人结合完整上下文确认"
  }]
}
```

### 3.4 人工复核

| 方法与路径 | 输入 | 输出与行为 |
|---|---|---|
| GET `/human-reviews/stats` | mode | 三类当前待办数与可选真实历史趋势 |
| GET `/human-reviews` | projectId,platform,q,type,state,page | 列表和总数；默认 pending/draft |
| GET `/human-reviews/:id` | 无 | 精确快照、AI 原结果、人工草稿、问题及历史 |
| PATCH `/human-reviews/:id/draft` | issueDecisions[],manualIssues[],finalChoice?,comment,revision | 原子保存草稿，不改变最终状态 |
| POST `/human-reviews/:id/decision` | decision,comment,issueDecisions[],manualIssues[],revision | 原子门禁检查、最终决定与任务状态更新 |

脚本阶段的 needs_confirmation 通过 scope=script 的同类复核入口解决；详情显示 ScriptPreview 替代 VideoPlayer，最终只解除脚本阶段准入，不完成整项达人任务。script=changes_requested/incomplete 的结果不可通过人工“直接通过”绕过，需修改或补齐分析。

### 3.5 模型配置与能力测试

| 方法与路径 | 输入 | 输出与行为 |
|---|---|---|
| GET `/settings/models` | 无 | 已生效配置、hasKey、maskedKey、能力报告 |
| POST `/settings/model-drafts` | provider,baseUrl,textModel,videoModel,keyAction?,key?,参数 | 创建服务端加密草稿，用于保存前测试，解决先验证后启用的依赖 |
| POST `/settings/model-drafts/:id/test` | checks[]=text/video/audio | 202，明确的测试操作；使用测试完整视频，不自动发送用户业务文件 |
| GET `/settings/model-tests/:id` | 无 | 分项进度、报告、脱敏错误、限制来源 |
| PUT `/settings/models` | draftId,mode,revision | 激活草稿；real 需有效能力报告；返回新配置版本 |
| DELETE `/settings/model-drafts/:id` | 无 | 放弃未生效草稿、清除临时凭证，不影响当前配置 |
| GET `/settings/capabilities` | 无 | 当前生效配置能力及应用/供应商有效限制 |

keyAction=retain/replace/delete，retain 不允许附带掩码作为密钥。测试使用显式用户操作，可以产生供应商测试调用，页面需说明；演示业务审核不产生真实调用。取消或过期草稿不得保留无用明文凭证。

## 4. 错误码与恢复

| HTTP / code | 含义 | 前端响应 |
|---|---|---|
| 400 VALIDATION_ERROR | 字段不合法 | 定位字段，保留输入 |
| 404 RESOURCE_NOT_FOUND | 对象不存在或不属于上下文 | 不展示其他对象，返回列表 |
| 409 REVISION_CONFLICT | 并发版本过期 | 显示新版，旧输入可复制 |
| 409 RULES_NOT_ENABLED | 规则未启用 | 跳到要求确认 |
| 409 SCRIPT_NOT_APPROVED | 脚本不具当前资格 | 显示脚本版本及阻断原因 |
| 409 REVIEW_INCOMPLETE | 音视频或规则未完整分析 | 补齐分析/异常待办，不自动放行 |
| 409 MODE_MISMATCH | 演示/真实结果混用 | 要求在正确模式重新审核 |
| 409 JOB_ALREADY_RUNNING | 不同幂等键请求同输入运行作业 | 返回现有 jobId，引导查看 |
| 413 FILE_TOO_LARGE | 超出有效限制 | 显示上限及来源 |
| 415 UNSUPPORTED_MEDIA | 格式/编码不支持 | 转换建议或替代格式 |
| 422 EXTRACTION_FAILED | 文档提取失败 | 重试/OCR/可读版本 |
| 422 INVALID_MODEL_RESULT | 模型结构、规则引用或时间无效 | 标失败/不完整，提供重试 |
| 422 CAPABILITY_UNVERIFIED | 视频或音轨能力未验证 | 配置测试入口 |
| 429 PROVIDER_RATE_LIMIT | 供应商限流 | 安全退避，显示等待及实际次数 |
| 502 PROVIDER_ERROR | 供应商错误 | 脱敏原因，保留作业 |
| 504 PROVIDER_TIMEOUT | 连接或处理超时 | 区分未发送与提交未知，不盲重试 |

## 5. 不变式

1. 任何人工通过记录都能定位到输入文件哈希、规则版本、脚本版本、模型配置和审核人。
2. UI 上按钮禁用与服务端校验同时存在；直接 API 请求不能绕过规则、模式或版本门禁。
3. 文件存储使用服务端生成名称，禁止路径穿越；上传失败的孤立文件定期清理，不误删被历史引用文件。
4. 审核失败不增加人工完成进度；AI passed 不等于 task completed。
5. 报告导出保留结果当时模式与版本，不受之后设置切换影响。
6. 演示空间允许模拟人工决定，其 mode 永远为 demo，界面标记模拟通过/退回；不得将演示决定应用到真实任务。
