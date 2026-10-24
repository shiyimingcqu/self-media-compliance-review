"""基线规则参数自动填充：事实卡就绪后，用确定性映射把 Brief 事实写进
L1 基础检查的 params（只填空的，品牌手改过的永不覆盖；不写库，随视图实时派生）。
竞品名单未确认时不猜测；产品事实用于准确性核对，不转成必讲清单。"""


def baseline_fill(rules, facts):
    """rules 为视频规则 dict 列表；facts 为事实卡 dict（camelCase）。返回是否有填充发生。"""
    if not facts:
        return False
    names = [item['value'] for item in (facts.get('products') or []) if item.get('value')]
    competitors = [item['value'] for item in (facts.get('competitors') or []) if item.get('value')]
    terms = [item['value'] for item in (facts.get('forbidden_terms') or facts.get('forbiddenTerms') or []) if item.get('value')]
    brand = (facts.get('brand') or {}).get('value')
    identity_names = list(dict.fromkeys(([brand] if brand else []) + names))
    references = [item['value'] for item in (facts.get('sellingPoints') or []) if item.get('value')]
    filled = False
    for rule in rules:
        if rule.get('layer') != 'baseline':
            continue
        key = rule.get('baselineKey')
        params = dict(rule.get('params') or {})
        params.setdefault('names', [])
        params.setdefault('terms', [])
        params.setdefault('thresholds', [])
        source = []
        if key == 'product' and not params['names'] and names:
            params['names'] = names
            source.append('products')
        if key == 'identity' and not params['names'] and identity_names:
            params['names'] = identity_names
            source.extend(['brand', 'products'])
        if key == 'information_accuracy' and not params['terms'] and references:
            params['terms'] = references
            source.append('sellingPoints')
        if key == 'competitor' and not params['names'] and competitors:
            params['names'] = competitors
            source.append('competitors')
        if key == 'forbidden' and not params['terms'] and terms:
            params['terms'] = terms
            source.append('forbiddenTerms')
        if source:
            rule['params'] = params
            rule['paramsSource'] = {'origin': 'briefFacts', 'groups': source}
            filled = True
    return filled
