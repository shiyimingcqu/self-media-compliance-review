"""Frozen-rule assessment boundaries; guidance only, never rewrites verdicts."""
VERSION = 'video_rule_boundaries_v2'


def topic(rule):
    from .video_layers import whole_video_only
    key = rule.get('baselineKey')
    if whole_video_only(rule):
        return 'script_match'
    if key == 'identity':
        return 'identity'
    if key == 'product':
        return 'product_presence'
    text = rule.get('text', '')
    if any(term in text for term in ('品名', '主推名称', '产品名称', '品牌名称', '品牌与产品信息', '品牌与产品身份', '品牌与产品名称')):
        return 'identity'
    if any(term in text for term in ('出现指定产品', '产品展示可辨认', '产品实际出现', '产品可辨认')):
        return 'product_presence'
    return 'own_requirement'


def assessment_boundaries(rules, selected_ids=None):
    """Use the complete active set even when a clip contains only a few rules.

    Owners are references for routing concerns, not extra rules for a clip call.
    Unrecognized or composite rules retain their literal frozen requirements.
    """
    rules = list(rules)
    identities = [{'rule_id': r['id'], 'text': r.get('text', '')} for r in rules if topic(r) == 'identity']
    scopes = {}
    for rule in rules:
        rid = rule['id']
        if selected_ids is not None and rid not in selected_ids:
            continue
        kind = topic(rule)
        focus = {
            'identity': '只判断品牌、品名、型号是否明显错误；按本条已确认简称、别名和判据核对，不默认要求每次使用全称。',
            'product_presence': '只判断本条指定产品是否实际出现、可辨认；不因口播使用简称就认定产品未出现。身份确实无法核实时说明限制并待确定。',
            'script_match': '只对照确认脚本的核心环节、动作、关键镜头和明确保留的内容承诺；允许口语、节奏和剪辑变化。',
            'own_requirement': '只按本条冻结的 text、criterion、params 或组件条件判断，不追加其他规则的要求。',
        }[kind]
        delegated = identities if kind != 'identity' else []
        scopes[rid] = {'focus': focus, 'nameChecksHandledBy': delegated,
                       'boundary': ('已有专门品名规则，品名/简称争议交给所列规则；不得仅据同一争议把本条也判为未通过。'
                                    if delegated else '不得引入未写进本条的品名、卖点、时长、次数或审美要求。'),
                       'literalRequirements': '本条明确写出的条件仍须独立核对；其他规则的失败本身不是本条失败的证据。'}
        if rule.get('baselineKey')=='competitor':
            scopes[rid]['competitorPolicy'] = ('有名单按 params.names 与本条禁止范围核对；名单为空时结合本项目品牌、产品品类和片中可辨认标识判断明显竞品，名单是可选资料，不得以未配置名单跳过检查或要求先配置。旧模板中“未配置名单不执行”已废止。平台水印、无关品牌或普通路人用品不自动算竞品；竞争关系或标识不确定时待确认并给出定位和依据。')
    return {'version': VERSION, 'rules': scopes,
            'instruction': '仅返回本次输入 rules 中的规则；nameChecksHandledBy 只是其他检查的归属引用，不是本批新增任务。'}
