"""Video rule layers, configuration readiness and candidate normalization."""
LAYERS = ('baseline', 'brief', 'precise')
BASELINE_KEYS = ('competitor', 'product', 'forbidden', 'script_match')
REVIEW_LEVELS = {'basic': 1, 'custom': 2, 'strict': 3}


def layer(rule):
    return rule.get('layer') or ('precise' if rule.get('componentPlan') else 'brief')


def review_level(rules):
    return next((r['videoReviewLevel'] for r in rules if r.get('videoReviewLevel') in REVIEW_LEVELS), 'strict')


def active_rules(rules):
    maximum = REVIEW_LEVELS[review_level(rules)]
    return [r for r in rules if r.get('enabled', True) and LAYERS.index(layer(r)) < maximum]


def defaults():
    specs = [
        ('competitor', '不出现指定竞品', '优先核对基础资料中确认的竞品名单；名单为空时按语义识别画面、口播与字幕中出现的其他品牌产品（尤其同品类竞品），发现即提示问题并保留证据，不把平台标识或无关商标当竞品。'),
        ('product', '出现指定产品', '检查 基础资料中指定产品是否可辨认地出现；身份和核心信息一致即可，不要求每个镜头或每一帧都清楚。'),
        ('script_match', '与已通过脚本基本匹配', '对照品牌已通过脚本检查核心商业内容和产品承诺，不要求逐字、逐镜头复刻；合理剪辑、同义表达和表演变化不判错。'),
    ]
    return [dict(text=text, criterion=criterion, category='基础检查', importance='must', origin='template',
                 sourceBlockId='', sourceQuote='', layer='baseline', baselineKey=key, enabled=True,
                 params={'names': [], 'terms': [], 'thresholds': []}) for key, text, criterion in specs]


# 需要"整片对照"的规则不适合逐段精判：片段里看不到全貌，还容易被重复塞进每个批次。
WHOLE_VIDEO_HINTS = ('已通过脚本', '脚本一致性', '脚本基本匹配', '整体一致性')


def whole_video_only(rule):
    """脚本一致性类规则：只做一次全片判断，不参与逐段精判。"""
    if rule.get('baselineKey') == 'script_match':
        return True
    text = f"{rule.get('text', '')}{rule.get('criterion', '')}"
    return any(hint in text for hint in WHOLE_VIDEO_HINTS)


def configuration_issue(rule, has_script=True):
    if rule.get('enabled') is False:
        return None
    if layer(rule) == 'baseline':
        params = rule.get('params') or {}
        key = rule.get('baselineKey')
        if key == 'competitor':
            return None  # 名单为空不阻塞：审核模型按语义识别其他品牌产品，品牌复核
        if key == 'product' and not params.get('names'):
            return '待品牌确认产品名称'
        if key == 'forbidden' and not params.get('terms'):
            return '待品牌确认禁用词句'
        if key == 'script_match' and not has_script:
            return '待品牌确认通过脚本'
    if layer(rule) == 'precise' and not rule.get('componentPlan'):
        return '待品牌补齐并确认检查组件'
    return None


def runnable(rules, has_script=True):
    ready, pending = [], []
    for rule in active_rules(rules):
        reason = configuration_issue(rule, has_script)
        if reason:
            pending.append({'ruleId': rule['id'], 'ruleNumber': rule.get('ruleNumber'), 'layer': layer(rule),
                            'text': rule['text'], 'importance': rule.get('importance', 'must'), 'reason': reason})
        else:
            ready.append(rule)
    return ready, pending


def normalize_candidates(report):
    """Only candidates, never executable plans or an overwrite of baseline settings."""
    for rule in report.rules:
        if rule.layer not in ('brief', 'precise'):
            raise ValueError('分层生成只能返回Brief定制或详细检查候选')
        rule.enabled = False
        rule.baseline_key = None
        rule.preset_key = None
        if rule.layer == 'brief':
            # Importance follows the Brief's obligation, not the checking layer.
            rule.component_plan = None
        else:
            thresholds = rule.params.thresholds if rule.params else []
            supported = {'minContinuousSeconds', 'minTotalSeconds', 'minOccurrences', 'startSeconds', 'endSeconds', 'occurrenceGapSeconds'}
            if not thresholds or any(t.key not in supported for t in thresholds):
                rule.layer = 'brief'
                report.warnings.append('未能适配现有时长/次数组件的要求已保留为第二层文字候选，请品牌核对。')
            else:
                rule.importance = 'must'
            # Reference images and exact parameters require brand confirmation first.
            rule.component_plan = None
    return report
