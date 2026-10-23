# Design

## Context

动机与业务范围见 proposal.md。当前仓库只有 `docs/assets/screenshots/01_…07_….png`，本变更建立规划基线。图稿表达了浅蓝白色后台、表格密度、工作流和主次操作，但把多个互斥状态放在同一画面，部分标签超出本期范围。

设计依据优先级：用户明确需求 > 本变更规范性 spec > 页面详设与接口契约 > 参考图示意文案。出现规格与附属文档矛盾时必须修正文档，不能让开发自行挑选。

## Goals / Non-Goals

**Goals**：七页有可恢复路由；每条反馈能追溯原文或视频区间；原视频和完整音轨进入审核链路；人工决定绑定准确版本；后端拒绝绕过前端门禁的非法转换。

**Non-Goals**：不实施发布或评论功能；不承诺模型逐帧理解；不把结构化分析率称作准确率；不以搭建复杂基础设施为一周 MVP 的前置条件。

## Decisions

### D1. 技术栈与边界

建议 React + TypeScript + Vite、React Router、TanStack Query、Ant Design，播放器使用原生 video 配合自定义时间轴。业务审核运算位于 FastAPI 后端，SQLite 保存元数据，本地受控目录存储文件。以上是待实现技术决策，不表示依赖已安装。

选择理由：前端大量表单、表格与抽屉，现成组件库便于一周交付，样式通过主题令牌贴合图稿。相比手写所有控件，优先保证键盘操作及错误提示。SQLite 适用于单实例演示；正式多人服务可替换 PostgreSQL 和对象存储，不改变业务契约。

### D2. 路由与信息架构

| 页面 | 路由 | 激活导航 |
|---|---|---|
| 01 项目列表 | `/projects` | 项目工作台 |
| 02 项目详情 | `/projects/:projectId` | 项目工作台 |
| 03 Brief 确认 | `/projects/:projectId/briefs/:briefId?version=:id` | 项目工作台 |
| 04 脚本审核 | `/projects/:projectId/tasks/:taskId/script?version=:id` | 项目工作台 |
| 05 视频审核 | `/projects/:projectId/tasks/:taskId/video?version=:id&issue=:id` | 项目工作台 |
| 06 人工待办 | `/reviews` | 人工待办 |
| 06 复核详情 | `/reviews/:reviewId` | 人工待办 |
| 07 模型设置 | `/settings/models` | 模型设置 |

图 05 单独增加的“视频审核”导航不采用：视频属于具体任务，由任务入口和深链接到达。全局保持三项导航；顶栏头像代表本地审核身份，不附带本期未实现的通知中心。默认路由跳转 `/projects`。不存在或不匹配的项目/任务组合显示不存在并提供返回入口。

### D3. 可复用组件与布局

公共组件：AppShell、Breadcrumb、PageHeader、StatCard、PlatformBadge、StatusTag、FilterBar、DataTable、UploadField、DocumentViewer、SourceLocator、RuleEditorDrawer、VersionPicker、VersionDiff、WorkflowStepper、ReviewSummary、IssueCard、VideoPlayer、IssueTimeline、EvidencePanel、JobProgress、HistoryTimeline、StickyActionBar、ErrorState、EmptyState、ModeBanner。

图稿背景采用浅蓝灰，白色面板、轻阴影、细边框，蓝色主操作。建议令牌：主色 #246BFF、背景 #F3F7FC、正文 #17233D、次要文本 #64748B，状态文字满足 WCAG AA，颜色之外配标签和图标。正文字体优先 Microsoft YaHei/PingFang SC/Noto Sans SC，数字使用等宽数字。间距 8/12/16/24px，圆角 8–16px，侧栏 208–224px，内容区 padding 24px。

1440px 对照参考图；1280px 保留双栏并将第三栏改为抽屉；1024px 折叠侧栏，任务页上下分区；768px 采用单栏，表格内部横向滚动。仅做桌面优先的可用降级，不承诺完整手机审片体验。底部固定操作栏预留等高内边距，所有对话框支持焦点限制、Esc 关闭和关闭后焦点恢复。

### D4. 状态分层

不得把上传、任务执行、AI 判断和人工决定塞进同一个 status。

| 对象 | 状态 |
|---|---|
| 项目 | active、completed、archived |
| 规则版本 | draft、parsing、parse_failed、pending_confirmation、enabled、superseded |
| 提交版本 | uploaded、extracting、ready、invalid |
| 后台作业 | queued、processing、succeeded、failed、interrupted |
| 单条规则结果 | compliant、non_compliant、needs_confirmation、not_applicable |
| AI 总体结论 | passed、changes_requested、needs_confirmation、incomplete |
| 人工复核 | pending、draft、approved、returned、superseded |
| 任务阶段 | requirements、script、video、human、completed |

执行线：queued → processing → succeeded/failed/interrupted；重试创建关联原作业的新作业，不擦除失败记录。AI 总结论：适用的必须项有不符合则 changes_requested；没有不符合但有待确认则 needs_confirmation；适用必须项全部符合且分析完整才 passed；分析失败、缺项或视频/音频未覆盖则 incomplete。建议项问题保留提示，不自动阻断，人工可退回。

脚本 passed 可进入视频；needs_confirmation 由人工明确裁决并记录后可进入；changes_requested 需新脚本重审；incomplete 必须补足分析。AI 视频 passed 或 needs_confirmation 可进入人工待办；changes_requested 默认退回修改，允许“申请人工复核”处理争议；incomplete 进入异常待办但不能最终通过，必须补齐审核。人工 approved 后阶段才 completed，不出现发布阶段。

新提交保留旧决定为历史，对新版本清空当前通过资格。规则更新不自动改绑旧任务；显式换绑后旧结果标为依据过期，脚本、视频、人工决定均重新计算有效性。审核期间变更规则绑定或提交版本，已运行作业仍写入旧快照，不能覆盖新版本状态。

### D5. 文件与长任务

Brief 支持 PDF、DOC、DOCX、TXT；脚本支持 DOC、DOCX、XLSX、XLS、TXT 与粘贴。DOC/XLS 由隔离转换器转为可解析格式；转换能力不存在、扫描件缺 OCR、密码文件无法打开时提供明确错误和替代格式，不静默读出空文本。保留页码、段落、sheet/cell 及来源坐标。文档嵌图若未被分析，来源预览保留并标注人工检查。

建议 Brief/脚本单文件上限 20MiB；视频应用上限 2GiB，最终有效上限取应用限制与经过验证的供应商限制的最小值。截图中的 500MB、60 分钟和 gpt-4o 只为示意，不是实现事实。元数据未知显示未验证，不默认支持。

作业提交返回 202 和 jobId，前端轮询 2 秒、页面隐藏后 10 秒，结束后停止。刷新从后端恢复。任务写入数据库，单独 worker 消费，不能仅放在 Web 请求进程内。SQLite 原子认领避免重复执行；崩溃后未能恢复外部状态的作业标 interrupted 并提供显式重试，不无限自动重发收费请求。

视频由后端将完整原文件上传供应商文件服务或受限短时 URL；应用不生成离散帧列表作为审核输入。完整音轨必须与视觉分析关联；若模型不能同时读取声音，增加全量音轨转写/音频分析并保持时间对齐。音频分支失败就不能通过。供应商内部如何采样必须在能力说明如实注明，不能承诺逐帧检查。

### D6. 模型结果、证据与确定性门禁

模型适配器输出固定 JSON schema：每条规则一个结果，问题引用 ruleId、原文定位或 startMs/endMs、证据描述、建议和不确定原因。后端检查 schema、规则覆盖、时间范围、引用 ID 和模式；格式错误最多有限修复，仍失败则作业 failed/incomplete。模型输出作数据渲染，HTML 转义；文档中的命令不改变系统指令。

审核覆盖率 = 已返回有效分析结果的适用规则数 / 适用规则总数，符合/不符合/待确认均算已分析，not_applicable 不计分母；分母零显示“—”。另列待确认数、音视频分析状态，覆盖率不得解释成质量分或通过概率。原视频未完整处理时即使规则覆盖 100% 也不得通过。

### D7. 密钥、身份与部署

服务端存储加密后的供应商密钥，主密钥从环境变量注入，不随数据库备份或代码提交；前端仅收到 hasKey/maskedKey，不提供取回已保存明文的接口。编辑新密钥可切换可见，旧密钥不可还原。日志过滤密钥和上传 URL。接口地址仅允许 HTTPS（本地开发适配器可显式白名单），阻止回环、内网、云元数据地址和重定向绕过，避免后端连接测试成为 SSRF。

MVP 使用单个有名称的本地审核身份并记录 actorId；仅绑定本机运行。若部署到共享或公网，必须先加入访问认证及项目文件授权，不把开发单人模式直接公开。无复杂角色体系；版本并发通过 revision/If-Match 防止覆盖，过期操作返回 409。

### D8. 与七张图的业务修正

| 图稿现象 | 规格决定 |
|---|---|
| 图 01 列表、新建弹窗、空态同时出现 | 三种独立状态；不在已有列表旁显示空项目卡 |
| 图 02/04 同时显示脚本未通过和视频审核中 | 新版本可并存历史状态，但当前版本门禁一致；显示“历史结果”避免误认 |
| 图 03 展示所有编辑侧栏 | 只打开当前操作抽屉，版本历史与编辑互斥 |
| 图 04 摘要 12 项但问题列表只有 6 项 | 摘要统计规则，列表默认关注项；切换全部后完整列出，计数标清单位 |
| 图 05 完成/失败显示为相邻步骤 | 失败是分支终态，成功时不把失败当下一步 |
| 图 05 任务创建→完成发布 | 改为要求确认→脚本审核→视频审核→人工确认→审核完成 |
| 图 06 出现“发布于” | 改为“提交于”；时间选择器使用视频时分秒/毫秒，不使用日历控件 |
| 图 07 固定模型视频和音轨能力 | 依据服务商验证报告；文本连接成功不等于视频可用 |
| 人工通过文案“同意投放” | 改为“审核通过，待业务后续处理”，不执行发布 |

## Risks / Trade-offs

- [供应商完整视频与音轨能力未验证] → 第一天使用真实完整视频验证输入、音轨、时长和时间定位；不满足则该适配器禁用真实视频审核，保留明确演示模式。
- [长视频超时/收费重复] → 持久化作业、幂等键、供应商请求 ID；对不确定提交不盲目自动重发。
- [扫描件及旧 Office 格式] → 保留原件，转换/OCR 出错可见；允许提供可读替代版本，不能跳过内容后宣告已解析。
- [时间定位有偏差] → 证据跳转含上下文，人工可编辑问题时间并保留原值；不声称毫秒精度的语义识别。
- [一周范围] → 单实例、三项导航、无通知中心/品牌管理独立页；所有页面必须能走主流程，增强项见 tasks.md。

## Migration Plan

这是全新应用，无历史数据迁移。后续依次创建数据库迁移、文件目录、服务配置、演示空间，再启动 Web 与 worker。演示数据使用独立模式命名空间；切换真实模式时不搬运模拟通过结果。回滚应用时保留原始文件和审计记录；数据库变更必须有版本标识及备份说明。
