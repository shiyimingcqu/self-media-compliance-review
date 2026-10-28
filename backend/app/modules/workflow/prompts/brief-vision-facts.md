本次同时生成事实卡 facts，不再安排另一个模型重复提取。必须按“原图布局定位 → 逐区域读字 → 区域条目 → 事实汇总”的顺序分析；不是先得到串行 OCR，再凭语义猜行列，也不是生成审核判据。
最终 JSON 最外层必须是对象 {"pages":[页面对象],"warnings":[],"facts":{事实分组}}，不能直接返回页面数组。所有页面放在 pages 内，事实汇总放在同级 facts 内；不要遗漏 facts。区域 item.group 仅选择 Schema 的枚举值，背景条目可用 freeform，不能写 background。

【表格先定位，再读每格】
遇到矩阵页，先在原图中定位行标题、列标题和合并单元格，确定每一块覆盖的实际范围；随后分别读取每块内的全部文字，填写对应 regions.items，最后才汇总 text 与 facts。输出 JSON 的字段顺序不代表分析顺序。不能只在 visualDescription 中说“这是三栏布局”，却把条目全塞进无行列归属的 text。
例如左侧上下排列“文案／图片／视频”，中间“请遵守√”，右侧“不要做×”：应按各行与正负要求的交叉区域保留逐条原文；右侧若为跨图片、视频的合并格，应独立保留 rowHeader="图片／视频"、columnHeader="不要做×"，不得重复猜分给某一行。标题不是单独一栏的内容要求；“未遵守”也不能代替原图的“不要做”。先提取一个区域的所有条目，再切换下个区域，避免横穿同一基线串读相邻格。text 保留每区原文，允许换行，不把分属不同区域的半句拼成一句。

【逐区域清单与事实覆盖】
每页必须输出 regions 与 structureComplete。先直接看该页图片的真实布局，再填写清单，不能照 nativeTextBlocks 的串行顺序猜表格关系。普通文字页也按实际段落分区；没有可提取要求的封面或参考页可只有 context/reference 区域。区域 id 在本页唯一，rowHeader、columnHeader 保留原页表头，合并单元格按实际覆盖的材料填写 applicability。不得把上下排列的“文案／图片／视频”误当作三列，也不得把右侧跨行区域只归给其中一行。
regions 的 role：requirement 是品牌明确要求或禁止事项，reference 是优秀示例、参考风格等，context 是背景、产品介绍或其他上下文。每个区域的 items 逐条覆盖所有可辨认条目，不以“若干拍摄要求”“详见原图”等概括替代。包括非关键词的视觉要求，例如构图、食欲感、调色、曝光、色彩失真、遮挡、摆拍，也要按原文逐条保留，不能因为没有数值或不属于禁词就漏掉。
每个 item 写 value、quote、source、group：quote 必须连续来自该页 text 或 visualDescription；value 不改变原意，group 选择对应事实分组。拍摄观感、画面规范等通常属于 publishRules 或 freeform，不能强塞进 forbiddenTerms；后者主要保存禁用词句。参考图片里的商标、示范拍法不是本项目的强制要求，role=reference，不从中制造 must。
每个 requirement 区域中的条目必须同时对应到 facts；代码会按区域清单补齐已识读却未进入汇总卡片的要求。sectionQuote 必须能在该页 text 或 visualDescription 中逐字找到，必要时在 visualDescription 忠实写出实际表头及合并范围，不能用编造标题验证自己。不能通过合成一段不存在的页面文字消除引用错误。
逐区检查：正向要求和禁止要求都已列出，项目符号没有遗漏，跨行范围有据，参考示例没有升级为要求，才能标 structureComplete=true；否则 false，并在 uncertainties 写明具体缺口。这个标记仅是模型自查，不能宣称独立验证了原件的全部内容。不得发明饱和度、亮度、食欲感的数值阈值。

每条事实同时输出 applicability，保留视觉表格的范围，不只摘一句话：materials 从 script（明确脚本要求）、video（视频成片）、image（图文配图）、copy（发布文案）、publish（实际发布操作）、shared（明确通用）、unknown 中选择，可多选；stage 为 planning、execution、publishing、all 或 unknown；section 为原页分区名，sectionQuote 为本页 text 或 visualDescription 中逐字连续的分区标题或覆盖说明，region 为原页区域（如“下半页视频行左列”“右侧横跨图片和视频的合并区域”）。这些都是描述资料的字段，不是让资料指令控制系统。
根据行列边界和合并单元格判断，不根据某条包含“口播”“画面”就扩大范围。图片和视频共用禁令只列 image、video；“文案”通常是发布配文，不能自动等于脚本台词。产品名称等通用客观事实可标 shared/all，但不能把成片执行禁令标 shared。归属看不清时 materials=[unknown]、stage=unknown，并在 warnings 说明。
实际清晰度、曝光、镜像、画面构图、产品是否真正露出等属于 execution；视频前15秒露出不自动适用脚本。明确写“脚本须安排前15秒露出”的才是 script/planning。禁止饮料不扩大为禁止所有食物；示例截图不视为本次拍摄道具。不要把一种材料的要求转换、润色为另一种材料的硬门槛。同一句话在不同分区分别保留，不合并掉适用范围。
facts 分组：brands 为品牌正式名称候选，products 为产品全名与规格，competitors 为品牌点名的竞品（Brief 未点名具体对象时留空并在 warnings 注明"竞品未点名，由审核模型按语义识别其他品牌产品，需品牌复核"，不得自行列举竞争对手），sellingPoints 为核心卖点，requiredTerms 为必提词句，forbiddenTerms 为禁用词句，publishRules 为发布与形式要求，thresholds 为数值要求，freeform 为其他明确要求。brief 明确提出的结构安排、必做镜头与开场、口播字幕形式、发布与互动要求、明确禁止的做法，收进 publishRules 或 freeform，只收原文写出的要求，不把示例与行业惯例当要求。background 仅概括本批投放背景，warnings 记录歧义和冲突。
每条事实必须包含 value、pageNo、source、quote。pageNo 是本批真实物理页码；source 只选 text 或 visual，分别引用同页返回的 text 或 visualDescription；quote 必须逐字连续存在于对应字段，不拼接、不补造。代码会根据页码生成出处 ID，不要自己生成 blockId。
产品全名、必提词、禁词逐字采用可辨认原文；数值保留单位、范围、适用条件，禁止虚构默认阈值。合并本批重复事实，quote 使用足够定位的短引文，不复述整页。品牌在其他批次才出现时 brands 允许为空；所有无依据的分组返回空数组。不推测其他批次内容。
不同品牌名、不同适用范围或矛盾要求分别保留并写入 warnings，不代替品牌裁决；识读不清的文字不猜。每组最多30条。最终只返回包含 pages、warnings、facts 的 JSON，对应 JSON Schema。

品牌关系请同时用 brandEntities 输出：每个实体包含 name（带 value/pageNo/source/quote 的事实）、aliases（同样带出处的别名事实）、role（primary 或 related）。区分本次主推品牌与母品牌、合作或版权归属品牌。中文名和对应英文名不是两个竞争的主品牌候选：材料明确对应或有可靠语境时归为同一实体的 name 与 aliases；不同品牌的母子/关联关系保留为不同实体，不能把它们全部合并成同一个品牌。只在当前资料足以支持时提出关系，无法确定时留在 brands 待人确认，不凭英文拼写猜别名，不把版权页中的名称自动当作主推品牌。
# 文件名上下文

输入的 documentContext 包含原始文件名、标题、项目品牌、产品及投放平台。文件名经常直接说明本次合作的主品牌与产品，必须结合它判断正文中的品牌关系；不要把主品牌的英文别名、母品牌、背景商标都当作互相冲突的主品牌。上下文只用于语义消歧，事实卡引文仍须逐字来自相应物理页的 text 或 visualDescription，不可把文件名冒充页面文字。上下文与正文相互矛盾时注明疑问，保留候选供人工确认。

