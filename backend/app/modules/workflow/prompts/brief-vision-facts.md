本次同时生成事实卡 facts，不再安排另一个模型重复提取。先忠实识读本批页面，再从识读内容提取客观事实；不是生成审核判据。
facts 分组：brands 为品牌正式名称候选，products 为产品全名与规格，competitors 为品牌点名的竞品（Brief 未点名具体对象时留空并在 warnings 注明"竞品未点名，由审核模型按语义识别其他品牌产品，需品牌复核"，不得自行列举竞争对手），sellingPoints 为核心卖点，requiredTerms 为必提词句，forbiddenTerms 为禁用词句，publishRules 为发布与形式要求，thresholds 为数值要求，freeform 为其他明确要求。brief 明确提出的结构安排、必做镜头与开场、口播字幕形式、发布与互动要求、明确禁止的做法，收进 publishRules 或 freeform，只收原文写出的要求，不把示例与行业惯例当要求。background 仅概括本批投放背景，warnings 记录歧义和冲突。
每条事实必须包含 value、pageNo、source、quote。pageNo 是本批真实物理页码；source 只选 text 或 visual，分别引用同页返回的 text 或 visualDescription；quote 必须逐字连续存在于对应字段，不拼接、不补造。代码会根据页码生成出处 ID，不要自己生成 blockId。
产品全名、必提词、禁词逐字采用可辨认原文；数值保留单位、范围、适用条件，禁止虚构默认阈值。合并本批重复事实，quote 使用足够定位的短引文，不复述整页。品牌在其他批次才出现时 brands 允许为空；所有无依据的分组返回空数组。不推测其他批次内容。
不同品牌名、不同适用范围或矛盾要求分别保留并写入 warnings，不代替品牌裁决；识读不清的文字不猜。每组最多30条。最终只返回包含 pages、warnings、facts 的 JSON，对应 JSON Schema。

品牌关系请同时用 brandEntities 输出：每个实体包含 name（带 value/pageNo/source/quote 的事实）、aliases（同样带出处的别名事实）、role（primary 或 related）。区分本次主推品牌与母品牌、合作或版权归属品牌。中文名和对应英文名不是两个竞争的主品牌候选：材料明确对应或有可靠语境时归为同一实体的 name 与 aliases；不同品牌的母子/关联关系保留为不同实体，不能把它们全部合并成同一个品牌。只在当前资料足以支持时提出关系，无法确定时留在 brands 待人确认，不凭英文拼写猜别名，不把版权页中的名称自动当作主推品牌。
# 文件名上下文

输入的 documentContext 包含原始文件名、标题、项目品牌、产品及投放平台。文件名经常直接说明本次合作的主品牌与产品，必须结合它判断正文中的品牌关系；不要把主品牌的英文别名、母品牌、背景商标都当作互相冲突的主品牌。上下文只用于语义消歧，事实卡引文仍须逐字来自相应物理页的 text 或 visualDescription，不可把文件名冒充页面文字。上下文与正文相互矛盾时注明疑问，保留候选供人工确认。

