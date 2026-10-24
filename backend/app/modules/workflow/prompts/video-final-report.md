工具轮次已结束，不得再调用工具。现在提交最终审核JSON，不要解释或Markdown。
最终逐条检查理由归属：每条 findings 只说明违反本条哪一个明确要求。禁止把名称规则中的品名/简称争议复制为其他规则不通过的唯一理由；其他规则必须有独立事实。音画使用同一个名称不等于音画不一致，口语简称也不自动等于产品未展示。保留冻结规则的明文条件，不自行添加要求，不把待品牌核对的争议写成已证实违规。
每条输入视频标准必须恰好一个finding，rule_id原样使用输入ID。finding.id及组件检查issue_id保持稳定；evidence_ids只能引用本次工具实际返回的**证据文件ID**（capture_evidence_image / cut_evidence_clip 返回的 id）。check_ 组件检查不产生证据文件，组件类 finding 的 evidence_ids 留空，不要填 issue_id 或规则 ID。同一画面或对话支撑多条相关规则时，可复用该证据ID，无须重复取证，各条说明其关联事实；不得复用无关证据、其他调用或其他视频的证据。
对要求内容在检查范围内未出现的项，observation_type=absence、evidence_ids=[]，说明缺失内容、检查范围与覆盖限制，无须截图或片段，也不因没有媒体附件单独降为待确认。这不证明客观绝对不存在。具体已观察问题使用observed；检查不完整、定位或感知不确定使用uncertain并标needs_confirmation。具体问题证据不足或工具失败时标needs_confirmation，不能凭空补造截图、时间或口播。不要把工具成功当作标准已满足。
结论只是AI建议，最终交品牌确认。请严格使用随后附加的JSON Schema。
