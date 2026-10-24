"""Video rule layers, configuration readiness and candidate normalization."""
LAYERS = ('baseline', 'brief', 'precise')
BASELINE_KEYS = ('competitor', 'product', 'forbidden', 'script_match', 'identity', 'information_accuracy', 'understandability')
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
        ('competitor', '没有出现竞品推广内容', '有指定竞品名单时按名单及禁止范围检查；没有名单时结合本项目品牌、产品品类和视频内容识别明显竞品露出或推广。不把平台标识、无关商标、路人用品自动判为竞品；无法确认竞争关系时标待确定并说明依据，不编造品牌名单。', 'must', False),
        ('product', '出现指定产品', '指定产品在视频中有实际出现，结合包装、口播或字幕能确认产品身份即可；不默认要求特写、正对镜头、固定时长或出现次数，不要求所有产品款式同时出现。', 'must', True),
        ('script_match', '核心商业内容与确认脚本基本一致', '仅对照品牌确认脚本中的产品、核心承诺和明确保留内容；允许换台词、换镜头、调整剧情节奏和合理剪辑，不要求逐字、逐镜头复刻。没有确认脚本时不适用。', 'must', False),
        ('identity', '品牌与产品信息没有明显错误', '当视频出现品牌、产品名称或型号时，检查有没有叫错品牌、说错产品或混淆型号；认可已确认的简称、别名和自然口语，不要求每次说全称，也不要求逐一说出全部名称。', 'must', True),
        ('information_accuracy', '已表达的产品信息准确', '只核对视频实际讲到的卖点、成分、参数及活动信息是否与确认资料存在明显冲突。检查资料中的事实仅供核对，不是必提清单；没有讲到某项资料不算遗漏，不把缺少资料自动判成达人违规。', 'must', True),
        ('understandability', '主要信息能看懂、听清', '仅提醒严重影响主要信息理解的遮挡、模糊或声音问题。普通拍摄差异、轻微噪声、表演和审美偏好不拦截；材料完全无法核查时说明范围并待人工确定，不编造违规。', 'suggested', True),
    ]
    return [dict(text=text, criterion=criterion, category='基础检查', importance=importance, origin='template',
                 sourceBlockId='', sourceQuote='', layer='baseline', baselineKey=key, enabled=enabled,
                 params={'names': [], 'terms': [], 'thresholds': []}) for key, text, criterion, importance, enabled in specs]



# 需要"整片对照"的规则不适合逐段精判：片段里看不到全貌，还容易被重复塞进每个批次。
WHOLE_VIDEO_HINTS = ('已通过脚本', '脚本一致性', '脚本基本匹配', '整体一致性')


def whole_video_only(rule):
    """脚本一致性类规则：只做一次全片判断，不参与逐段精判。"""
    if rule.get('baselineKey') == 'script_match':
        return True
    text = f"{rule.get('text', '')}{rule.get('criterion', '')}"
    return any(hint in text for hint in WHOLE_VIDEO_HINTS)


# 主观价值判断类规则：容貌、歧视、导向、价值观这类结论依赖文化语境解读，
# 模型看造型/滤镜/肤色的观感并不可靠（实测把抖音常见的变脸娱乐特效判成"肤色歧视"），
# 但读画面文字、字幕和听口播是可靠的。因此这类规则要有逐字证据才能定不合规。
SUBJECTIVE_HINTS = ('容貌焦虑', '歧视', '争议', '物化女性', '性别对立', '冒犯', '贬损', '刻板印象',
                    '低俗', '价值观', '导向上', '敏感表达', 'social_compliance')
SUBJECTIVE_CATEGORIES = ('social_compliance', '内容安全', '价值导向')


def subjective_rule(rule):
    """需要"逐字证据"才能判不合规的主观判断类规则。带量化组件的规则不算（由程序算结论）。"""
    if rule.get('componentPlan'):
        return False
    if (rule.get('category') or '') in SUBJECTIVE_CATEGORIES:
        return True
    text = f"{rule.get('text', '')}{rule.get('criterion', '')}{rule.get('category', '')}"
    return any(hint in text for hint in SUBJECTIVE_HINTS)


def script_unavailable_note(script_status):
    """脚本侧到底是没提交还是没确认，直接用状态说清楚，不要含糊成"待确认"。"""
    if script_status == 'pending_approval':
        return '本次脚本已提交，但品牌尚未确认通过'
    if script_status == 'brand_approved':
        return '本次已通过脚本的正文为空'
    return '本次未提交脚本'


def mark_script_rules_unavailable(findings, rules, script_status):
    """没有可用的已通过脚本时，脚本一致性类规则判"不适用"，不留一条永远消不掉的待确认。"""
    mapping = {rule.get('id'): rule for rule in rules}
    note = script_unavailable_note(script_status)
    changed = []
    for finding in findings:
        rule = mapping.get(finding.get('rule_id'))
        if not rule or not whole_video_only(rule):
            continue
        finding['verdict'] = 'not_applicable'
        finding['description'] = f'{note}，无法逐条比对内容承诺与关键镜头，本条不适用；品牌确认脚本后可重新验收。'
        changed.append(finding.get('rule_id'))
    return changed


def configuration_issue(rule, has_script=True):
    if rule.get('enabled') is False:
        return None
    if layer(rule) == 'baseline':
        params = rule.get('params') or {}
        key = rule.get('baselineKey')
        if key == 'competitor':
            return None  # 名单可选；未指定时由模型按品牌与品类判断。
        if key in ('product', 'identity') and not params.get('names'):
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


def legacy_skipped_competitors(snapshot, findings):
    """Old runs omitted unnamed competitors. Allow brand review, never invent a pass."""
    seen={f.get('rule_id') for f in findings}
    pending={r.get('ruleId') for r in snapshot.get('unconfiguredRules',[])
             if r.get('reason')=='待品牌确认指定竞品名单'}
    return {r['id'] for r in active_rules(snapshot.get('rules',[]))
            if r['id'] in pending and r['id'] not in seen and layer(r)=='baseline'
            and r.get('baselineKey')=='competitor' and not (r.get('params') or {}).get('names')
            and not r.get('componentPlan')}


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


def normalize_template(report, facts=None):
    """Complete baseline drafts in the same call; never mutate saved settings."""
    from .standards import StandardRule
    from .baseline_fill import baseline_fill
    templates = defaults()
    baseline_fill(templates, facts)
    for item in templates:
        item.pop('paramsSource', None)
    expected = {item['baselineKey']: item for item in templates}
    completed = {}
    additional = []
    for rule in report.rules:
        if rule.layer != 'baseline':
            additional.append(rule)
            continue
        key = rule.baseline_key
        if key not in expected or key in completed:
            raise ValueError('通用规则类型不存在或重复')
        template = expected[key]
        rule.importance = template['importance']
        rule.enabled = template['enabled']
        rule.component_plan = None
        rule.preset_key = None
        if rule.params:
            rule.params.thresholds = []
        # Keep the universal limits explicit alongside the Brief-specific wording.
        rule.criterion = rule.criterion[:17000] + '\n' + template['criterion']
        completed[key] = rule
    report.rules = additional
    normalize_candidates(report)
    missing = [key for key in expected if key not in completed]
    if missing:
        report.warnings.append('部分通用项未返回，已保留模板并填入可用的 Brief 事实，请核对草稿。')
    baseline_rules = [(completed[key].model_dump(by_alias=True) if key in completed else item) for key, item in expected.items()]
    baseline_fill(baseline_rules, facts)
    for item in baseline_rules:
        item.pop('paramsSource', None)
    report.rules = [StandardRule.model_validate(item) for item in baseline_rules] + report.rules
    report.warnings = [warning for warning in report.warnings if '本次没有新增适用的视频成片要求' not in warning]
    return report
