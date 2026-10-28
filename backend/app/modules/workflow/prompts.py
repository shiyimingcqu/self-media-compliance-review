from pathlib import Path
import hashlib

ROOT = Path(__file__).with_name('prompts')
TITLES = {
    'brief-script-rules-text': ('Brief → 脚本规则文字', '根据品牌上传的Brief生成一段可编辑审核规则'),
    'brief-script-standards': ('Brief → 脚本推荐标准', '将Brief转换为可编辑的脚本检查候选项'),
    'brief-image-standards': ('Brief → 图文推荐标准', '将 Brief 转换为封面、配图、标题与正文检查候选项'),
    'brief-video-standards': ('Brief → 视频推荐标准', '将Brief转换为独立成片检查候选项'),
    'brief-video-template': ('Brief + 通用模板 → 视频规则', '结合 Brief 补全通用规则，并追加明确的成片要求'),
    'brief-video-layered': ('Brief → 视频分层候选', '生成待品牌确认的第二层文字和第三层量化候选'),
    'brief-facts': ('Brief → 事实卡', '把Brief中的品牌名、产品全名、必提禁用词句与发布规则整理成带出处的结构化事实'),
    'rule-generation-lenient': ('规则生成 · 宽松', '聚焦明确硬性要求和影响品牌信息的实质问题'),
    'rule-generation-standard': ('规则生成 · 标准', '覆盖主要交付要求，区分必须项与优化建议'),
    'rule-generation-strict': ('规则生成 · 严格', '细化可验证的执行要求和证据口径'),
    'script-review': ('脚本检查', '按品牌确认的脚本标准逐条检查台词与镜头安排'),
    'image-review': ('图文成品验收', '按图文标准核对封面、有序图片、标题正文与配文'),
    'video-review': ('完整视频审查与取证', '按视频标准审查完整音视频并按需调用证据工具'),
    'video-localize': ('视频整片定位', '逐条初判并定位需要复核的区间，组件只输出观察'),
    'video-refine': ('视频片段精判', '独立复核带原声片段及相关规则'),
    'video-component-pass': ('视频组件精判', '只核对量化检查组件，提交观察由程序计算结论'),
    'video-final-report': ('视频最终报告', '停止工具调用并提交结构化报告'),
    'connection-test': ('文本连接测试', '一次简短接口连通验证'),
}


def prompt(key):
    if key not in TITLES:
        raise ValueError('Unknown prompt')
    content=(ROOT/(key+'.md')).read_text(encoding='utf-8-sig').strip()
    if key in ('script-review', 'brief-script-standards', 'brief-script-rules-text'):
        content += '\n\n' + (ROOT/'script-default-dimensions.md').read_text(encoding='utf-8-sig').strip()
    if key == 'script-review':
        content += '\n\n' + (ROOT/'script-reference-material.md').read_text(encoding='utf-8-sig').strip()
    if key in ('brief-script-standards', 'brief-script-rules-text'):
        content += '\n\n脚本规则只约束本次实际安排，不要求风格参考图出现本品，也不把参考素材原有道具自动视为本次计划。禁止其他饮料不能扩写为禁止所有食品；不增加未确认的道具禁区。'
        from .script_defaults import DEFAULT_SCRIPT_RULES
        content += '\n\n【通用脚本模板与判据】\n' + DEFAULT_SCRIPT_RULES
    if key in ('brief-script-standards', 'brief-image-standards', 'brief-video-standards', 'brief-video-layered', 'brief-video-template'):
        content += '\n\n' + (ROOT/'rule-readable-names.md').read_text(encoding='utf-8-sig').strip()
    if key in ('brief-script-standards','brief-script-rules-text','brief-image-standards','brief-video-standards','brief-video-layered','brief-video-template','brief-facts'):
        content += '\n\n' + (ROOT/'brief-material-scope.md').read_text(encoding='utf-8-sig').strip()
    if key in ('brief-video-standards', 'brief-video-layered'):
        content += '\n\n' + (ROOT/'rule-video-scope.md').read_text(encoding='utf-8-sig').strip()
        content += '\n\n' + (ROOT/'rule-video-baseline-first.md').read_text(encoding='utf-8-sig').strip()
    if key == 'brief-video-template':
        content += '\n\n' + (ROOT/'rule-video-scope.md').read_text(encoding='utf-8-sig').strip()
        content += '\n\n本模式即使没有新增定制要求，也必须返回补全后的六条 baseline 规则，不返回空列表。产品事实用于补全核对依据，不自动变成必提项。'
    if key in ('brief-video-standards', 'brief-video-layered', 'brief-video-template'):
        content += '\n\n' + (ROOT/'video-rule-generation.md').read_text(encoding='utf-8-sig').strip()
    if key in ('script-review','image-review','video-review','video-localize','video-refine','video-final-report',
               'brief-script-standards','brief-script-rules-text','brief-image-standards','brief-video-standards','brief-video-layered','brief-video-template'):
        content += '\n\n' + (ROOT/'creative-tolerance.md').read_text(encoding='utf-8-sig').strip()
    if key in ('video-review','video-localize','video-refine','video-final-report'):
        # Give both passes the same window semantics before they inspect footage.
        content = (ROOT/'video-exposure-boundaries.md').read_text(encoding='utf-8-sig').strip() + '\n\n' + content
    return {'key':key,'title':TITLES[key][0],'purpose':TITLES[key][1],
            'version':hashlib.sha256(content.encode()).hexdigest()[:12],'content':content}


def catalog():
    return [prompt(key) for key in TITLES]


def recommendation_prompt(key, strictness='lenient'):
    if strictness not in ('lenient', 'standard', 'strict'):
        raise ValueError('Unknown generation strictness')
    base = prompt(key)
    profile = prompt('rule-generation-' + strictness)
    guard = ('各档位共同边界：品牌/产品事实与明确数值不得捏造或篡改；确认的禁止内容、已确认的合规红线和品牌明确冻结的验收硬门槛不可因档位被删除或放宽。'
             '不得虚构品牌要求；无硬性验收依据的优化项不能升级为must。'
             '保留重大交付要求，宽松档的一般执行细节可作为suggested，具体商业取舍交品牌核对确认；歧义和冲突写入warnings。')
    content = base['content'] + '\n\n' + profile['content'] + '\n\n' + guard
    return {**base, 'content': content, 'version': hashlib.sha256(content.encode()).hexdigest()[:12],
            'strictness': strictness, 'profile': profile}
