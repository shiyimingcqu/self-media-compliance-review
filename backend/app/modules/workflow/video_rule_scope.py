"""Conservative guard for clearly non-video AI candidates, not saved brand rules.

Semantic stage selection belongs to the generation prompt. Code only removes
unambiguous publishing/operations/deliverable titles and preserves explicit
in-video requirements, even if they mention tags, comments or scripts.
"""
import re

IN_VIDEO = re.compile(r'成片|口播|画面|字幕|片中|视频内|视频中|片头|片尾|开场|结尾|封面|'
                      r'脚本.{0,12}(?:匹配|一致|执行|偏离)|(?:匹配|一致|执行|偏离).{0,12}脚本')


def excluded_reason(rule):
    title=rule.get('text','') if isinstance(rule,dict) else rule.text
    if IN_VIDEO.search(title):return None
    if re.search(r'(?:预热|测评|后续|发布).{0,12}(?:必带|带|添加|包含).{0,8}(?:TAG|话题|标签)|'
                 r'必带\s*TAG|话题标签|发布文案|发布配文|笔记正文|发布标题',title,re.I):
        return '发布标签与配文'
    if re.search(r'评论|私信',title) and re.search(r'反馈|PR|回复|置顶|处理|报备',title,re.I):
        return '发布后评论运营'
    if re.search(r'笔记|发布后|上线后|保留期|留存',title) and re.search(r'删除|隐藏|修改|保留|留存',title):
        return '发布留存与合同要求'
    if re.search(r'脚本|大纲|提纲|分镜表|拍摄方案',title) and re.search(r'提交|交付|报审|审核流程|(?:需|须|应|必须).*(?:包含|包括|含有|含)',title):
        return '脚本与大纲交付'
    if re.search(r'发布时间|发布排期|上线日期|交稿时间|发送初稿|返稿时间|报备流程|达人排期',title):
        return '项目执行与发布排期'
    if re.search(r'图片内容|图片至少|配图数量|图文笔记|Live图|实况图',title,re.I):
        return '图文图片交付'
    return None


def filter_candidates(report):
    kept=[];excluded=[]
    for rule in report.rules:
        reason=excluded_reason(rule)
        if reason:excluded.append({'text':rule.text,'reason':reason})
        else:kept.append(rule)
    report.rules=kept
    return excluded


def existing_exclusions(rules):
    """Return cleanup hints only; never mutate frozen or saved rule sets."""
    return [r['text'] for r in rules if r.get('origin')=='ai' and r.get('layer')!='baseline' and excluded_reason(r)]
