"""VLM supplies observations; deterministic tools evaluate immutable brand parameters.

Observed coverage remains a model assertion, never a verified full-coverage claim.
"""
import os

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class Observation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start_ms: int=Field(ge=0,le=600_000,strict=True)
    end_ms: int=Field(gt=0,le=600_000,strict=True)
    matched: bool
    certain: bool=False
    whole_visible: bool=False
    logo_clear: bool=False
    occluded: bool=True
    orientation: Literal['normal','mirrored','uncertain']='uncertain'
    channel: Literal['visual','audio','subtitle','visible_text']='visual'
    text: str=Field(default='',max_length=2000)
    term: str=Field(default='',max_length=200)
    description: str=Field(min_length=1,max_length=2000)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms<=self.start_ms:raise ValueError('时间区间无效')
        return self


class ComponentObservationInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rule_id: str=Field(min_length=1,max_length=100)
    component_id: str=Field(min_length=1,max_length=80)
    issue_id: str=Field(min_length=1,max_length=100)
    coverage_complete: bool=False
    observations: list[Observation]=Field(default_factory=list,max_length=100)
    notes: str=Field(default='',max_length=3000)


def merge(intervals,gap=0):
    result=[]
    for start,end in sorted(intervals):
        if result and (start<=result[-1][1] or start-result[-1][1]<gap):
            result[-1][1]=max(result[-1][1],end)
        else:result.append([start,end])
    return result


def intersect(a,b):
    return merge([[max(x,u),min(y,v)] for x,y in a for u,v in b if max(x,u)<min(y,v)])


def evaluate(session,kind,payload):
    rule=session.rule_source(payload.rule_id)
    plan=rule.get('componentPlan') or {}
    node=next((n for n in plan.get('nodes',[]) if n['id']==payload.component_id),None)
    if not node or node['kind']!=kind:raise ValueError('组件不属于本规则或工具类别不符')
    cache_key=(payload.rule_id,payload.component_id)
    fingerprint=payload.model_dump_json()
    prior=session.component_results.get(cache_key)
    if prior:
        if prior['_fingerprint']!=fingerprint:raise ValueError('已提交的组件观察不能覆盖')
        return public_result(session,rule,prior)
    if any(v['ruleId']==payload.rule_id and v['issueId']!=payload.issue_id for v in session.component_results.values()):
        raise ValueError('同一规则须使用同一个问题ID')
    params=node['parameters'];start=round(params.get('startSeconds',0)*1000)
    end=min(session.duration_ms,round(params['endSeconds']*1000) if params.get('endSeconds') is not None else session.duration_ms)
    if start>=end:raise ValueError('检查区间不在视频内')
    if any(o.end_ms>session.duration_ms for o in payload.observations):raise ValueError('观察时间超出原视频')
    observations=[o for o in payload.observations if o.end_ms>start and o.start_ms<end]
    unknown=any(not o.certain for o in observations)
    coverage=payload.coverage_complete
    # complete 用于"未发现"类结论；量化类（时长/次数）先用 coverage 判"连乐观估计都不够"。
    complete=coverage and not unknown
    if kind in ('required_speech','forbidden_expression') and not session.has_audio:complete=False
    def qualifies(o):
        if not o.matched or not o.certain:return False
        if kind=='specified_action':return True
        if kind=='logo_presence' and not o.logo_clear:return False
        return ((not params.get('requireWholeProduct') or o.whole_visible) and
                (not params.get('requireClearLogo') or o.logo_clear) and
                (params.get('allowOcclusion',False) or not o.occluded))
    good=[o for o in observations if qualifies(o)]
    intervals=merge([[max(start,o.start_ms),min(end,o.end_ms)] for o in good])
    # 只要求 matched 的"观察到"区间：模型认出是这只产品但标了不确定/遮挡时，仍然要给人工可用的数字。
    observed=merge([[max(start,o.start_ms),min(end,o.end_ms)] for o in observations if o.matched])
    verdict='needs_confirmation';reason='证据不足或检查范围未完整覆盖';metrics={}
    if kind in ('product_presence','logo_presence','specified_action'):
        if good:verdict='compliant';reason='在指定区间内发现满足全部条件的目标'
        elif complete:verdict='non_compliant';reason='所报完整检查范围内未发现满足条件的目标'
    elif kind=='product_mirroring':
        mirrored=[o for o in good if o.orientation=='mirrored']
        normal=[o for o in good if o.orientation=='normal']
        intervals=merge([[max(start,o.start_ms),min(end,o.end_ms)] for o in normal])
        if mirrored:verdict='non_compliant';reason='发现目标产品镜像区间'
        elif complete and normal and not any(o.orientation=='uncertain' for o in good):
            verdict='compliant';reason='所报检查范围中目标产品方向正常'
    elif kind=='exposure_duration':
        longest=max((b-a for a,b in intervals),default=0);total=sum(b-a for a,b in intervals)
        observed_longest=max((b-a for a,b in observed),default=0);observed_total=sum(b-a for a,b in observed)
        metrics={'longestContinuousMs':longest,'totalMs':total,
                 'observedLongestContinuousMs':observed_longest,'observedTotalMs':observed_total}
        need_continuous=params.get('minContinuousSeconds');need_total=params.get('minTotalSeconds')
        sure=(need_continuous is None or longest>=need_continuous*1000) and (need_total is None or total>=need_total*1000)
        maybe=(need_continuous is None or observed_longest>=need_continuous*1000) and (need_total is None or observed_total>=need_total*1000)
        if sure:verdict='compliant';reason='有效区间达到品牌确认的连续和累计时长'
        elif coverage and not maybe:verdict='non_compliant';reason='即使计入未确认或遮挡区间，仍未达到品牌确认的时长'
        elif observed_total:
            verdict='needs_confirmation'
            reason=(f'观察到约{observed_total/1000:.1f}秒（最长连续{observed_longest/1000:.1f}秒），'
                    '但产品身份未经确认（logo 不可辨或被遮挡），需人工确认')
        elif complete:verdict='non_compliant';reason='有效露出未达到品牌确认时长'
    elif kind=='exposure_count':
        gap=round(params['occurrenceGapSeconds']*1000)
        occurrences=merge(intervals,gap)
        observed_occurrences=merge(observed,gap)
        metrics={'count':len(occurrences),'occurrenceIntervals':occurrences,
                 'observedCount':len(observed_occurrences),'observedOccurrenceIntervals':observed_occurrences}
        if len(occurrences)>=params['minOccurrences']:verdict='compliant';reason='有效露出次数达到要求'
        elif coverage and len(observed_occurrences)<params['minOccurrences']:
            verdict='non_compliant';reason='即使计入未确认或遮挡区间，有效露出次数仍不足'
        elif observed_occurrences:
            verdict='needs_confirmation'
            reason=(f'观察到约{len(observed_occurrences)}次出现，但产品身份未经确认（logo 不可辨或被遮挡），需人工确认')
        elif complete:verdict='non_compliant';reason='有效露出次数不足'
    elif kind=='forbidden_expression':
        matches=[o for o in observations if o.matched and o.certain and o.channel in ('audio','subtitle','visible_text') and o.text.strip()]
        intervals=merge([[max(start,o.start_ms),min(end,o.end_ms)] for o in matches])
        if matches:verdict='non_compliant';reason='发现结合语境判定的禁用表达'
        elif complete:verdict='compliant';reason='所报完整音视频范围内未发现禁用表达'
    elif kind=='required_speech':
        matches=[o for o in observations if o.matched and o.certain and o.channel=='audio' and o.term in params['terms'] and o.text.strip()]
        missing=[term for term in params['terms'] if term not in {o.term for o in matches}]
        metrics={'missingTerms':missing}
        intervals=merge([[max(start,o.start_ms),min(end,o.end_ms)] for o in matches])
        if not missing and session.has_audio:verdict='compliant';reason='每项必须口播均有对应原声观察'
        elif complete:verdict='non_compliant';reason='所报完整音轨范围内缺少必须口播'
    result={'ruleId':payload.rule_id,'componentId':payload.component_id,'kind':kind,'issueId':payload.issue_id,
            'verdict':verdict,'reason':reason,'intervals':intervals,'observedIntervals':observed,'metrics':metrics,'scopeMs':[start,end],
            'coverageClaimed':payload.coverage_complete,'coverageVerified':False,'observations':[o.model_dump() for o in observations],
            'notes':payload.notes,'promptVersion':node['prompt']['version'],'_fingerprint':fingerprint}
    session.component_results[cache_key]=result;session.save_manifest()
    return public_result(session,rule,result)


def public_result(session,rule,result):
    public={k:v for k,v in result.items() if not k.startswith('_')}
    try:
        combined=aggregate_rule(session,rule)
        public['ruleResult']={k:combined[k] for k in ('verdict','reason','commonIntervals','operator','sameSegment')}
    except ValueError:public['ruleResult']={'status':'awaiting_other_components'}
    return public


def aggregate_rule(session,rule):
    plan=rule.get('componentPlan')
    if not plan:return None
    values=[session.component_results.get((rule['id'],n['id'])) for n in plan['nodes']]
    if any(v is None for v in values):raise ValueError('存在未调用的检查组件')
    verdicts=[v['verdict'] for v in values]
    common=[]
    if plan['operator']=='any':
        verdict='compliant' if 'compliant' in verdicts else 'needs_confirmation' if 'needs_confirmation' in verdicts else 'non_compliant'
    else:
        verdict='non_compliant' if 'non_compliant' in verdicts else 'needs_confirmation' if 'needs_confirmation' in verdicts else 'compliant'
        if plan.get('sameSegment') and verdict=='compliant':
            common=values[0]['intervals']
            for value in values[1:]:common=intersect(common,value['intervals'])
            if not common:verdict='non_compliant'
    return {'ruleId':rule['id'],'ruleNumber':rule.get('ruleNumber'),'verdict':verdict,'operator':plan['operator'],
            'sameSegment':plan.get('sameSegment',False),'commonIntervals':common,
            'reason':'各组件符合但没有同时满足的共同画面' if plan.get('sameSegment') and not common and all(v=='compliant' for v in verdicts) else '依据逐组件结果组合',
            'issueId':values[0]['issueId'],'components':[{k:v for k,v in value.items() if not k.startswith('_')} for value in values]}


def components_enabled():
    """REVIEW_VIDEO_COMPONENTS=off 时关闭组件工具，改由模型直接判读（用于对比准确率）。"""
    return os.getenv('REVIEW_VIDEO_COMPONENTS', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def component_tools(session):
    if not components_enabled():
        return []
    from langchain_core.tools import StructuredTool
    kinds={n['kind'] for r in session.rules.values() for n in (r.get('componentPlan') or {}).get('nodes',[])}
    result=[]
    for kind in sorted(kinds):
        nodes=[n for r in session.rules.values() for n in (r.get('componentPlan') or {}).get('nodes',[]) if n['kind']==kind]
        def make_callback(selected_kind):
            def callback(**kwargs):
                return evaluate(session,selected_kind,ComponentObservationInput.model_validate(kwargs))
            return callback
        result.append(StructuredTool.from_function(make_callback(kind),name='check_'+kind,args_schema=ComponentObservationInput,
            description=nodes[0]['prompt']['content']+'\n返回值由工具按冻结参数计算，不能自行覆盖；所有规则组件须实际调用。'))
    return result
