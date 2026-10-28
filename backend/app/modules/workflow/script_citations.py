"""Model selects source IDs; server owns literal quotes and template coverage."""
import hashlib
import re

from app.core.errors import ApiError
from .script_defaults import default_script_rules

CONTRACT = '''【脚本引用契约 script_refs_v1】
本次规则输出只用 source_refs 选择输入 sourceReferences 中的 ref，可选多个，禁止输出或拼接 sourceQuote、sourceBlockId、原文和 origin。原文由代码回填。
sourceReferences 的每项都是独立原文片段；有几处依据就选几个编号，不重排拼写原文。不凭空编造编号。没有 Brief 依据的通用规则用空数组。
category 从八维分类或“其他”中选。代码会保留八维基础检查，你只需润色有依据的维度或追加明确的要求。空数组不代表已核实 Brief，涉及具体事实或新增强制条件必须选择对应出处。
所有早先要求输出 source_block_id、source_quote 或 origin 的指令由本契约替代。'''


def source_catalog(blocks, facts, available_blocks=None):
    from .standards import quote_located
    available = available_blocks if available_blocks is not None else (blocks or [])
    entries = {}

    def add(block_id, quote):
        if not quote or len(quote) > 3000 or not quote_located(quote, block_id, available):
            return
        ref = 'src_' + hashlib.sha256((block_id+'\0'+quote).encode()).hexdigest()[:24]
        entries[ref] = {'ref': ref, 'blockId': block_id, 'quote': quote}

    for block in blocks or []:
        # 字符边界由代码产生；每条都是该原文内的真实连续子串。
        for match in re.finditer(r'[^\n•。！？]+[。！？]?', block['text']):
            text = match.group().strip()
            for start in range(0, len(text), 700):
                add(block['id'], text[start:start+700])

    def walk(node):
        if isinstance(node, dict):
            if node.get('blockId') and isinstance(node.get('quote'), str):
                add(node['blockId'], node['quote'])
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(facts)
    return list(entries.values())


def prepare_report(value, blocks, facts, catalog, legacy=False):
    from .standards import (ScriptRecommendationReport, StandardRule,
                            validate_recommendation, quote_located, system_note)
    # 校验基础对象，缺少内容不伪造；引用错误则逐条降级，不作废其他候选。
    warnings = list(value.get('warnings', []))
    lookup = {entry['ref']: entry for entry in catalog}
    candidates = []
    for number, original in enumerate(value.get('rules', []), 1):
        rule = dict(original)
        refs = rule.pop('source_refs', rule.pop('sourceRefs', None))
        pending = False
        references = []
        recovered = False
        if refs is not None:
            if not isinstance(refs, list) or len(refs)>20 or any(not isinstance(ref, str) for ref in refs):
                raise ValueError('source_refs must be a list of reference IDs')
            for ref in dict.fromkeys(refs):
                entry = lookup.get(ref)
                if entry and quote_located(entry['quote'], entry['blockId'], blocks):
                    references.append(dict(entry))
                else:
                    pending = True
            rule.update(sourceBlockId='', sourceQuote='', sourceReferences=references)
            rule.pop('source_block_id', None); rule.pop('source_quote', None)
            # 无出处的 AI 定制内容不能伪装成已验证的模板；八维标准条目由代码补齐。
            if not refs:
                exact_template = next((r for r in default_script_rules()
                                       if r['text']==rule.get('text') and r['criterion']==rule.get('criterion')), None)
                if not exact_template:
                    pending = True
        else:
            # 兼容旧响应：仅恢复同一块中逐字存在的片段，不做近似语义匹配。
            quote = rule.get('sourceQuote', rule.get('source_quote', ''))
            block_id = rule.get('sourceBlockId', rule.get('source_block_id', ''))
            source = next((b for b in blocks if b['id']==block_id), None)
            if legacy and source and '\n' in quote and not quote_located(quote, block_id, blocks):
                parts = [part.strip() for part in quote.splitlines() if part.strip()]
                if len(parts)>1 and all(len(part)>=4 and source['text'].count(part)==1 for part in parts):
                    starts = [source['text'].index(part) for part in parts]
                    continuous = source['text'][min(starts):max(start+len(part) for start, part in zip(starts, parts))]
                    if len(continuous)<=3000:
                        rule['sourceQuote']=continuous;rule.pop('source_quote', None)
                        recovered = True
            try:
                parsed = validate_recommendation({'rules':[rule],'warnings':[]}, blocks, 'script', facts=facts)
                rule = parsed.rules[0].model_dump(by_alias=True)
                warnings.extend(parsed.warnings)
                if rule['sourceBlockId'] and rule['sourceQuote']:
                    references = [{'ref':'legacy_'+str(number),'blockId':rule['sourceBlockId'],'quote':rule['sourceQuote']}]
                else:
                    pending = True
            except ApiError as exc:
                if exc.code != 'INVALID_STANDARD_SOURCE':
                    raise
                pending = True
                rule['sourceBlockId']='';rule['sourceQuote']=''
                rule.pop('source_block_id', None);rule.pop('source_quote', None)
        if references:
            rule.update(sourceBlockId=references[0]['blockId'], sourceQuote=references[0]['quote'])
        rule['sourceReferences']=references
        rule['origin']='ai'
        rule['citationConfirmed']=False
        rule['citationStatus']='pending' if pending else ('verified' if references else None)
        rule['citationNote']='出处待核对；核对内容后可按品牌自定要求采用。' if pending else ('已从旧回复恢复连续原文，规则内容未改变。' if recovered else '')
        if pending:
            rule['enabled']=False
            warnings.append(system_note(f'第{number}条出处待核对，已保留为未启用草稿；其他有效规则可正常使用。'))
        elif recovered:
            warnings.append(system_note(f'第{number}条已本地恢复真实连续原文，未调用AI。'))
        candidates.append(StandardRule.model_validate(rule))

    # 固定八个基础维度；未核验的候选不能替换可靠的通用基础项。
    merged = []
    used = set()
    for baseline in default_script_rules():
        found = next((i for i, r in enumerate(candidates)
                      if i not in used and r.category==baseline['category'] and r.citation_status!='pending'), None)
        if found is None:
            merged.append(StandardRule.model_validate(baseline))
        else:
            candidate=candidates[found].model_copy(deep=True)
            combined=baseline['criterion']+'\n本项目要求：'+candidate.criterion
            if len(combined)>20000:
                # 不截断模型判据；保留基础项，长候选作为独立补充。
                merged.append(StandardRule.model_validate(baseline))
                continue
            candidate.criterion=combined
            if baseline['importance']=='must':candidate.importance='must'
            merged.append(candidate);used.add(found)
    merged.extend(r for i, r in enumerate(candidates) if i not in used)
    return ScriptRecommendationReport(rules=merged, warnings=list(dict.fromkeys(warnings)))
