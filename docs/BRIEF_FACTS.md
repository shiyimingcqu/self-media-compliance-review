# Brief 事实卡（一次解构，规则生成共用）

## 待核对统一处理

页面识读疑点（小字未识清、含义有疑问等）与事实候选统一出现在“查看解析内容 → 待核对”。点击页面条目定位左侧原页，可选择“已核对”或“不影响规则”；保存后从待核对及项目概览提示中移除，刷新仍保留。不会重新调用模型，也不会自动采信未核实的事实候选。

`POST /projects/{id}/brief/pages/review` 接收 `sourceVersionId`、`checkId`、`action`（confirmed / irrelevant）与幂等键。W_0014 将决定按来源、识读运行和疑点内容保存，保留审计记录；Brief 替换或新一次识读产生新范围，旧确认不会覆盖新疑点。原始模型回复、机器识读覆盖率和 `uncoveredSources` 均保持原样，人工已核对不冒充 AI 全部识清。`GET /projects/{id}/brief` 的 `pageChecks` 仅返回当前尚未处理的页面疑点。

## 从 Brief 自动创建项目

项目列表新增“上传 Brief 创建项目”，支持 PDF/TXT。先使用已配置的图文识读模型，将原始文件名、前 3 页原生文字及缩略图（TXT 为前 16000 字）作为上下文，识别主品牌、产品名称、投放平台及项目名称。这是额外一次轻量 VLM 元信息调用，界面在提交前说明可能计费；不调用解构模型来猜文件名。仅原图可见而无法在文件名/原生文字核对的信息，转人工补充，不伪造正文出处。

`POST /brief-project-imports` 携带 `fileId`、`allowRealCall: true` 和幂等键，返回持久化导入记录；`GET /brief-project-imports/{id}` 查询进度，列表端点返回最近 20 条摘要。状态为 queued → processing → succeeded / needs_input / failed；重启中的运行转 interrupted，绝不自动重新发起收费调用。原始模型返回在 JSON 解析之前保存，文件哈希、模型配置版本、提示词及哈希、预览范围与用量可回溯。

品牌、产品、平台都有可核对出处时，自动创建项目、挂接当前 Brief、排队全文解析。三项写入处于同一个事务，登记失败不会留下空项目。项目新增 `productNames` 字段，旧项目默认空列表，原有手动新建仍可用。同一导入完成重复提交返回已有项目，不重复创建。

缺失或矛盾信息展示“待补充”，不默认某个平台；`POST /brief-project-imports/{id}/complete` 接收 `{project: ProjectCreate}`，补充后直接创建，不再调用元信息模型。同名冲突可修改项目名称继续。关闭页面不会中断后台任务，刷新后可在“导入记录”找回。

创建后沿用全文处理：PDF VLM 分批输出页面和事实卡；TXT 使用独立解构模型。元信息识别成功不等于全文事实卡已完成；后续缺 Key 或解析失败仍由项目 Brief 面板明确展示。全文事实提取模型同步接收原始文件名、标题、项目品牌、产品及平台作为消歧上下文，事实卡引用仍必须落到真实正文块，不将文件名冒充物理页面。

## 解决什么问题
Brief 上传解析后，脚本规则和视频规则此前各自独立读 Brief 原文生成，同一事实（产品全名、必提/禁用词、数值）可能在两套规则里被写成不同版本；判断阶段又不看 Brief，规则里未内联事实就无法执行。事实卡把"品牌客观事实"从"检查判据"里剥出来，只解构一次，两条规则生成链路共用同一份。

## 链路
```
PDF 上传 → VLM 分批返回 pages + facts → 代码校验页码、引文与合并
                                      └─ brief_facts_runs（modelRole=vision，共享同次调用）
TXT 上传 → 独立语言模型提取事实卡 ──────────┘
    → 规则生成自动引用当前来源事实卡 → 品牌核对并保存 → 审核只看已确认规则
```

PDF 不再额外调用语言模型提取事实卡。`POST /brief/vision` 新前端发送 `extractFacts: true`；旧客户端省略时保持原有仅识读行为。独立语言模型继续承担 TXT 事实提取和审核规则生成。

## 关键约束
- 结构：brand（必须可核对，缺失整次失败）、background、products / sellingPoints / requiredTerms / forbiddenTerms / publishRules / thresholds / freeform，每条 `value + block_id + quote`。
- 出处校验与推荐规则一致：quote 必须逐字来自所选 blocks 的同一个块；`BRIEF_FACTS_CITATION_MISMATCH` 时不部分采信。
- 版本隔离：事实卡按 source_version_id 查询，Brief 更新后旧事实卡不会喂给新规则的生成。
- 判断阶段输入不变：事实卡只进"生成"，不进脚本/视频审核的模型输入，维持"生成看 Brief、判断只看规则"的边界。
- fail-closed：`allow_real_call` 必须为 true；不自动重试；处理中互斥（BRIEF_FACTS_BUSY）；复用解构模型配置与 revision 校验。

## 上传后自动处理
新前端上传携带 `autoProcess: true`，服务端在登记成功的同一事务中创建 `brief_processing_runs`（W_0012），提交事务后交给独立后台执行器：PDF 的同一次 VLM 调用同时识读全页和提取事实卡，后台只做代码校验、合并与落库；TXT 使用语言模型提取事实卡。离开或刷新页面不会中断任务。旧客户端省略字段仍保持仅登记行为。

处理状态随 `GET /projects/{id}/brief` 的 `processing` 返回：PDF 为 queued → vision → succeeded，TXT 为 queued → facts → succeeded。先检查当前任务所需 Key，再发起付费调用；PDF 仅需图文模型 Key，不要求独立解构模型 Key。缺配置显示 blocked；配置后通过 `POST /projects/{id}/brief/process` 明确继续。失败或服务重启不自动重试；显示失败阶段关联记录和原因，手动继续可能产生新费用。上传重放与继续请求均使用幂等键；同一来源只允许一个活动处理任务。Brief 替换后旧任务停止后续阶段，旧事实卡不进入新来源。

当前自动流程未成功前，规则生成按钮显示等待状态，后端也拒绝生成，避免绕过事实卡。就绪后脚本、视频规则生成自动携带同版本事实卡与出处，填充候选判据和参数；不自动覆盖或启用品牌已确认规则。

## 前端
项目 Brief 面板展示自动处理阶段和分组事实卡，Tooltip 显示出处原文与页码；事实卡疑点直接列出。历史未启动自动处理的 PDF 提供“解析图文并生成事实卡”入口；TXT 保留语言模型提取入口。新上传只需点击一次“上传并解析”，界面说明后续模型处理可能计费。


## 测试
backend/tests/test_brief_facts.py：解构成功/幂等重放/出处伪造整次失败/版本隔离/缺 Key/并发互斥/推荐调用吃到事实卡。迁移 W_0011_brief_facts.sql 已入顺序断言。

自动流程验证：`backend/tests/test_brief_processing.py` 覆盖 PDF/TXT 顺序、事实卡进入生成输入、上传重放、缺 Key 继续、失败短路、旧版本取消；`e2e/brief-auto-processing.spec.ts` 使用固定响应验证上传、刷新、阶段跟踪与规则页就绪门禁，不调用真实模型。

## TXT 输入与等待状态
提取前仅删除已被同页图文识读文字逐字完整包含的原生文字块，以及同页完全相同的重复块；保留原 ID、文本、不同页面、数值差异和未覆盖原文，原始库记录不改写。snapshot 同时记录 sourceBlockCount、实际 blockCount 和 inputCompaction。保留解构模型的原思考配置。

事实卡阶段显示已等待时长与单次读取超时上限，运行中的事实卡记录定时刷新。PDF 人工继续时，仅复用同一来源同时包含有效事实卡的成功 VLM 结果（允许页面有 partial，但覆盖缺口必须保留）。旧版仅识读结果无法凭代码补造事实卡，继续时需要明确重跑一次 VLM。事实卡校验失败保留 pages 和批次原始回复，不自动补调语言模型；用户明确继续才会重新调用 VLM。校验失败仍保留模型原文和请求 ID。

## VLM 事实卡约束
每条 VLM 事实为 `value + pageNo + source(text/visual) + quote`。代码先校验它引用的是当前批次真实页面，quote 在对应页的 text 或 visualDescription 中连续存在（允许仅换行、制表符及空格差异，匹配后存回原始文本中的真实连续片段），再生成 `vision:{runId}:{pageNo}:{source}` 来源 ID。校验只证明引用一致，不能证明图像识读准确。

每个批次品牌允许为空，整份必须存在可核对品牌；多个不同品牌候选不会自动选一个，单独列入待核对，其他有效事实保留。跨批相同 value 去重；不同数值条件保留，并对同指标多版本和必提/禁用交叉写出警告。整批事实卡结构缺失仍会报错并保留页面；单条引文错误不再使其他有效事实作废。事实卡与视觉结果在同一事务落库，共享 providerRequestIds / usage，billingOrigin 标为 shared-vision-call，实际只产生 VLM 批次调用。

已确认规则和历史审核不重写；同一 PDF 新识读任务会生成新出处，不能继续向规则生成提供旧识读对应的事实卡。PDF 旧的独立事实提取接口返回 BRIEF_FACTS_USE_VLM，收费前拒绝，防止重复调用语言模型。

## 引文容错与部分事实
仅忽略 Unicode 空白进行连续匹配，不模糊匹配、不替换标点、数字、单位，不跨页面或跨 text/visual 字段。匹配后保留原页的真实引文，quoteRepairs 记录原引文、规范引文和方法，原始 batches/modelResponse 不改写。

无法核对的条目存入 result.pendingFacts（分组、原值、原页码、原引文、原因），与有效 facts 分离，界面展开查看。pendingFacts 不传给规则生成。品牌无有效出处或存在多个候选时不自动选择，brand 留空、factsComplete=false，并保留其他有效条目。没有任何有效事实仍报错。

`revalidate_saved_facts` 可用已保存原回复重新校验，无模型调用，生成独立事实卡记录并记录审计事件；原错误留在 factsValidation 的 previousError 字段。自动流程继续前先尝试这项免费校验，可恢复时不重新发送 VLM 请求。

## 原文与事实卡对照页
项目概览只保留 Brief 文件、处理状态、有效/待核对数量及上传入口，不展示长事实列表。`/projects/{projectId}/brief-review` 独立页面提供固定高度双栏：左侧物理原页图片（支持页码、上一/下一页、缩放），右侧事实卡、待核对项和当页识读文字。两栏分别滚动，选中事实按真实来源页定位；待核对项不改变采信状态。无已渲染图片时使用原始 PDF 预览，TXT 展示原文文字；读取不会触发模型调用。

页面页码与标签写入 URL，刷新后保留。顶部可返回项目与达人列表。长提示与规则异常默认折叠，项目页不因资料条目数量增加而增长。浏览器验证覆盖 85 条事实、5 条待核对、27 页文档及移动端宽度。

事实卡按分类分段展示：每个分类一个小标题加一段话，同类事实用「、」连成一句（`.brief-fact-group` / `.brief-fact-term`），不再一条一张卡。每项仍是按钮，点击定位原页并在该段下方展开原文出处；搜索命中项在段内收窄，空分类隐藏。待核对项保持单条卡片，因为每条要单独确认、编辑或忽略——合并成段就没法逐条决策。

## 竞品分组与基础资料自动填充
事实卡含 competitors 分组，只收 Brief 逐字点名的竞品；Brief 只写"竞品"未点名时该组留空，warnings 注明由审核模型按语义识别其他品牌产品，需品牌复核，禁止模型自行列举市场上的对手。

`baseline_fill.py` 在 `project_standards_view` 读取时做确定性映射：products → 「出现指定产品」的 names、competitors → 「不出现指定竞品」的 names、forbiddenTerms → 禁用词的 terms，只填当前为空的参数，品牌手改过的名单不覆盖，不写库（确认冻结时看到的就是最终值），并在规则上标 `paramsSource.origin=briefFacts` 供追溯。

竞品名单为空不再阻塞执行：`configuration_issue` 对 competitor 返回空、不进 unconfiguredRules，判据改为"名单为空时按语义识别画面、口播与字幕中出现的其他品牌产品"，前端按钮显示"未点名 · 模型语义识别"。

## 品牌可读性约定
规则 text 和 criterion 是给品牌看的，不得出现 `params.names`、`params.terms`、`minTotalSeconds` 这类程序内部键名——键名只写在 params 字段和生成 prompt 里。默认模板规则因此改为「品牌确认的完整名称」「已确认的必提词句」「视频基础资料中填写的完整名称」等中文表述；仅视频侧基础资料可填名单，脚本侧无对应输入，故脚本判据只写"尚未确认时标注待品牌确认"而不承诺某个字段。视频规则页的阈值标签走 `thresholdText()` 转成"连续露出不少于 3 秒"这类中文。回归测试 `test_rule_scope.py::test_template_rules_are_written_in_brand_language` 扫描三个模板的全部规则。

## 品牌实体与人工核对
事实卡新增 brandEntities：每个实体由带出处的 name、aliases 和 primary/related 角色组成。中文名与英文名可以属于同一实体，母品牌、主推品牌可为不同关联实体；不维护写死的品牌字典。VLM 可提出有出处的分组关系，关系不明时仍列待核对。

Brief 对照页的待核对项提供“设为主品牌”“标为关联品牌”“选择归属后归为别名”“忽略”；其他事实提供“确认采用”“编辑后采用”“忽略”。POST `/projects/{projectId}/brief/facts/review` 携带当前 sourceVersionId、baseRunId 与 decisions，服务端逐条校验出处和所属项目，使用幂等键、版本冲突检查。每次保存派生新的事实卡记录与审计事件，保留模型回复及旧事实卡；不调用模型、不改变已冻结规则，后续生成规则才使用已确认的品牌实体与别名。

引文无法核对时不能直接采用，可在侧栏修正事实、页码、文字/画面描述来源及原文；无依据的内容可忽略。品牌别名必须归属已存在的明确实体，禁止静默把已确认的不同品牌互相合并。

## 生成规则的模型输入
生成规则时优先只发事实卡：`latest_facts_run` 判定卡片完整（无待核对、有可核对品牌、未被标 `factsComplete=false`；TXT 提取不写该字段，按"无待核对即完整"处理）时，user 消息只有 `{uncoveredSources, briefFacts}`，Brief 正文不再下发；否则回退为 `{blocks, uncoveredSources, briefFacts}`。正文始终留在服务端参与引文逐字校验（`check_quotes`），所以只发卡片不削弱引用检查，但模型只能引用卡片给出的 `blockId` 与 `quote`，卡片没覆盖的要求只能写进 warnings。

snapshot 记录 `inputMode: facts|blocks` 与 `factsPendingCount`，`blocks` 字段记的是实际下发的块（facts 模式为空数组）；规则页与发布抽屉的调用信息显示"输入：仅事实卡 / 事实卡 + Brief 正文"。

与之配套，`brief-facts.md` 与 `brief-vision-facts.md` 要求把结构安排、必做镜头与开场、口播字幕形式、发布与互动要求、明确禁止的做法收进 publishRules、freeform——卡片可能成为唯一输入，漏收等于丢规则。

测试：`test_brief_facts.py::test_recommendation_receives_saved_facts`（完整卡片时 blocks 为 None、inputMode=facts）、`test_recommendation_falls_back_to_blocks_when_facts_have_pending`（有待核对时回退并带 pending 计数）。
