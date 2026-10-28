"""Application-owned platform skill packs, loaded into the existing single call."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).with_name('platform_skills')
PACKS = {'xiaohongshu': '小红书', 'douyin': '抖音'}
SCOPES = {
    'script': ('script_plan', 'planning', '只检查脚本台词、字幕设计和拍摄安排，实际发布及成片另验。'),
    'video': ('video', 'content', '只检查音视频成片及明确属于视频的封面，不核验发布配文和后台设置。'),
    'image': ('image_post', 'content', '检查提交的封面、有序配图、标题和正文；发布配文内容可核对，实际发布操作另验。'),
}


def apply_platform_skills(template, platforms, scope):
    platforms = sorted(set(getattr(p, 'value', p) for p in platforms))
    content_type, stage, boundary = SCOPES[scope]
    common = (ROOT / 'common.md').read_text(encoding='utf-8').strip()
    priorities = (ROOT / 'brand-priorities.md').read_text(encoding='utf-8').strip()
    packs = []
    sections = [common, priorities]
    for platform in platforms:
        if platform not in PACKS:
            continue
        text = (ROOT / platform / 'SKILL.md').read_text(encoding='utf-8').strip()
        sources = json.loads((ROOT / platform / 'sources.json').read_text(encoding='utf-8'))
        cases = json.loads((ROOT / platform / 'cases.json').read_text(encoding='utf-8'))
        version = hashlib.sha256((common + priorities + text + json.dumps([sources,cases], sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:12]
        packs.append({'id': platform, 'name': PACKS[platform], 'version': version, 'sources': sources})
        sections.append(text)
        sections.append('【作用范围示例，仅示范分类，不是本项目事实或要求】\n' +
                        json.dumps([case for case in cases if case['scope']==scope],ensure_ascii=False))
    context = {'version': 'platform_skills_v1', 'platforms': platforms, 'scope': scope,
               'contentType': content_type, 'stage': stage,
               'priorityProfile': {'version':'brand_priorities_v1','delivery':50,'risk':30,'brandStyle':10,'effectiveness':10,'weightedPass':False},
               'mode': 'shared' if len(platforms)>1 else 'single', 'packs': packs}
    if not packs:
        sections.append('当前平台暂无专属规则包，只使用通用边界与 Brief 明确要求，不借用其他平台政策。')
    sections.append('【代码选择的执行上下文】\n' + json.dumps(context, ensure_ascii=False) + '\n' + boundary)
    content = template['content'] + '\n\n' + '\n\n'.join(sections)
    return {**template, 'content': content, 'version': hashlib.sha256(content.encode()).hexdigest()[:12]}, context


def excluded_reason(text, scope):
    """Only unambiguous cross-stage titles; semantic applicability stays in prompts."""
    # 明确要求片中/脚本内展示的话题等仍是当前材料的内容要求。
    in_material = re.search(r'片中|视频中|视频内|画面|口播|字幕|台词|分镜|脚本中|脚本内', text)
    if not in_material and re.search(r'发布时间|发布排期|上线日期|报备|发布标记|商业合作标识|商单标识|挂载购物|反馈.{0,4}PR|评论.{0,8}(?:回复|维护|处理)|发布后.{0,12}(?:删除|隐藏|留存|保留)', text, re.I):
        return '发布或运营环节要求'
    if scope in ('script', 'video'):
        if not in_material and re.search(r'发布(?:计划|配文|文案|标题)|笔记正文|(?:预热|测评).{0,8}(?:TAG|话题|标签)|话题标签', text, re.I):
            return '发布配文要求不属于脚本或视频内容'
        if not in_material and re.search(r'图文.{0,8}(?:至少|不少于|必须|须)|配图.{0,8}(?:数量|至少|不少于)|(?:Live图|实况图).{0,8}(?:至少|必须|须)|产品图.{0,8}(?:张|数量)', text, re.I):
            return '图文交付要求不能转换为视频或脚本硬门槛'
    if scope == 'image' and re.search(r'口播|视频.{0,8}(?:前\d+秒|时长|秒数)|累计露出.{0,8}秒', text):
        return '视频执行要求不属于图文内容'
    return None


def filter_stage_candidates(value, scope, context=None):
    if not isinstance(value, dict) or not isinstance(value.get('rules'), list):
        return value, []
    kept, excluded = [], []
    for rule in value['rules']:
        reason = excluded_reason(rule.get('text', ''), scope) if isinstance(rule, dict) else None
        if isinstance(rule,dict) and context and not reason:
            text=rule.get('text','')
            target=set(context.get('platforms',[]))
            for platform,name in PACKS.items():
                if re.search(r'^(?:仅适用于|仅限)?'+name+r'(?:笔记|平台|图文|视频|发布|项目|内容|专属)',text):
                    if target != {platform}:
                        reason='平台专属要求不属于当前共用规则范围'
                    break
        if reason:
            excluded.append({'text': rule['text'], 'reason': reason})
        else:
            kept.append(rule)
    if not excluded:
        return value, []
    warnings = list(value.get('warnings', []))
    warnings.append(f'【系统提示】已略过{len(excluded)}条不属于本次材料或审核阶段的候选要求。')
    return {**value, 'rules': kept, 'warnings': warnings}, excluded
