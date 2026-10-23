# 独立 Brief 解构模型

默认模型ID为 `qwen3.8-flash`，与内容审核的多模态模型分开配置。阿里云结构化输出支持范围见 https://www.alibabacloud.com/help/zh/model-studio/qwen-structured-output ，思考模式说明见 https://www.alibabacloud.com/help/en/model-studio/deep-thinking 。自定义兼容服务的模型权限和参数兼容性仍须单独验证。

## 分工

| 工作 | 配置 |
|---|---|
| Brief文字 → 脚本/视频候选规则，含历史单段生成接口 | 独立Brief解构语言模型 |
| Brief PDF图片识读 | 原有多模态审核配置 |
| 脚本、图文成品、视频审核 | 原有审核配置 |

设置页新增“Brief 解构语言模型”卡片，单独保存地址、模型ID、Key、思考强度和Temperature。默认最高思考强度 `xhigh`、Temperature `0.1`，可改为 `none/low/medium/xhigh`。保存和沿用Key不调用模型；生成仍须显式确认一次真实调用，不自动重试，也不自动确认候选规则。

## 隔离与兼容

- 新表 `decomposition_model_settings` 保存独立加密Key和revision，不改变 `evidence_model_settings`。
- 已有安装在迁移时一次性复制审核服务地址和加密Key，模型设为 `qwen3.8-flash`。以后修改或清除任一边的Key、模型和版本，不影响另一边。
- 新安装若先保存审核配置，不隐式回退或复制。可在解构卡片点击“沿用审核服务地址与 Key”，显式复制后独立保存；此操作不复制审核模型名称或视频大小参数。
- 解构Key缺失时拒绝生成，不偷偷使用审核Key。候选生成请求的 `configRevision` 指解构配置版本；审核模型revision变化不影响该版本。
- 生成记录冻结 `modelRole=decomposition`、模型、端点、解构配置版本、思考强度、Temperature、输出模式、提示词与原文块。历史生成记录不重写、不重发，原有本地恢复继续使用其原始快照。
- 默认Qwen3.8-Flash请求启用 strict JSON Schema；未识别的兼容模型使用JSON Object，不为探测能力自动重发。完整槽位/谓词解构、双采样、冲突检测与覆盖台账不属于本次模型拆分，不能把本次拆分视作这些工作已完成。

## API

- `GET/PUT /api/v1/settings/models/decomposition`
- `POST /api/v1/settings/models/decomposition/copy-review-credentials`，传入当前解构 `revision`。
- 原有 `GET/PUT /api/v1/settings/models` 保持内容审核配置用途与契约。


## 规则生成尺度

脚本及旧客户端的视频规则生成支持三档，默认宽松（没有大问题即可）。新视频规则页改用三层配置，不再显示 AI 生成尺度选择器，顶部基础／定制／严格控制实际审核层级范围；`videoRuleMode=layered` 使用专门的分层候选提示词，只返回第二、第三层关闭候选，品牌核对后选用。详见 [视频三层规则](VIDEO_RULE_LAYERS.md)。保留的历史档位如下：

| 档位 | 提示词重点 |
| --- | --- |
| 宽松 | 只拦影响品牌交付的重大问题，一般措辞、镜头、剪辑、表演和执行细节作为建议，不阻断通过 |
| 标准 | 覆盖主要商业交付要求，区分必须项与优化建议 |
| 严格 | 细化已有明确依据的执行要求、检查范围及证据口径，无硬性依据的细节仍只作建议 |

档位只影响下一次生成，不修改已保存规则或直接改变审核结果；生成后仍须品牌核对并保存。品牌/产品事实与数值不得篡改，确认的禁止项、合规要求及明确冻结的验收硬门槛要求保留；宽松档一般执行细节可以作为建议，需品牌核对确认；提示词约束不替代品牌核对，不保证模型不会遗漏。

三档文件为 `backend/app/modules/workflow/prompts/rule-generation-{lenient,standard,strict}.md`，也可在模型提示词目录中查看。服务端组合阶段专用提示词、选定档位提示词及共同边界，按实际组合内容生成版本哈希；推荐记录快照保存 strictness、完整提示词及档位提示词版本。请求新增 strictness 枚举字段，旧客户端省略时采用 lenient；同一幂等键修改档位会被拒绝，避免混用结果。


当前宽松档以“没有明显大问题就可以”为实务目标：仅重大品牌/产品错误、核心商业内容明显缺失、有依据的实质误导及明确验收硬门槛作为阻断项；一般细节不机械升级为must。脚本、视频与图文审核提示词同步要求在规则未明确逐字/固定执行时允许合理变化，轻微差异已满足核心要求时判符合并给建议。已有冻结规则和历史结果不自动改写；如旧规则明确设置了过细的必须条件，需按新尺度重新生成、核对并保存。
