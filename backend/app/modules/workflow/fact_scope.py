"""Source-backed material/stage boundaries; legacy facts remain distinguishable."""
from typing import Literal
from pydantic import Field
from app.core.contracts import ContractModel


class FactApplicability(ContractModel):
    materials: list[Literal['script','video','image','copy','publish','shared','unknown']] = Field(default_factory=lambda:['unknown'], min_length=1, max_length=7)
    stage: Literal['planning','execution','publishing','all','unknown'] = 'unknown'
    section: str = Field(default='', max_length=200)
    section_quote: str = Field(default='', max_length=1000)
    region: str = Field(default='', max_length=300)


def validate_scope(scope, sources):
    """A label must exist on this page; this checks provenance, not visual correctness."""
    if not scope:
        return None
    parsed = FactApplicability.model_validate(scope)
    if not parsed.section_quote.strip() or not any(parsed.section_quote in text for text in sources):
        return FactApplicability(region=parsed.region).model_dump(by_alias=True)
    return parsed.model_dump(by_alias=True)


def scope_reason(scope, target):
    if not scope:
        return None  # Historical facts lack this information; never invent their scope.
    materials = set(scope.get('materials', ['unknown']))
    known_materials = materials - {'unknown'}
    # Missing metadata is not evidence that a source-backed fact is inapplicable.
    # Keep it in the generation context; only explicit contradictory scope excludes it.
    if known_materials and target not in known_materials and 'shared' not in known_materials:
        return '该出处仅适用于其他材料，不属于本次审核范围'
    if target == 'script' and scope.get('stage') in ('execution','publishing'):
        return '成片执行或实际发布要求不能直接作为脚本门槛'
    if target in ('video','image') and scope.get('stage') in ('planning','publishing'):
        return '脚本交付或实际发布要求不属于成品内容审核'
    return None


def scoped_facts(facts, target):
    """Keep the full card in storage; only send applicable facts to generation."""
    import copy
    if not facts:
        return facts, []
    result=copy.deepcopy(facts); excluded=[]
    for key, items in result.items():
        if not isinstance(items,list) or key in ('brandEntities','warnings'):
            continue
        kept=[]
        for item in items:
            reason=scope_reason(item.get('applicability'),target) if isinstance(item,dict) else None
            if reason: excluded.append({'group':key,**item,'reason':reason})
            else: kept.append(item)
        result[key]=kept
    return result, excluded


def filter_scoped_candidates(value, facts, target, catalog=()):
    """Reject cross-material citations, including when mixed with applicable ones."""
    if not isinstance(value,dict) or not isinstance(value.get('rules'),list):
        return value, []
    scopes={}
    for items in (facts or {}).values():
        if isinstance(items,list):
            for item in items:
                if isinstance(item,dict) and item.get('applicability'):
                    scopes.setdefault((item.get('blockId'),item.get('quote')),[]).append(item['applicability'])
    refs={entry['ref']:entry for entry in catalog}
    kept=[]; excluded=[]
    for rule in value['rules']:
        citations=[refs[r] for r in rule.get('sourceRefs',rule.get('source_refs',[])) if r in refs]
        citations+=rule.get('sourceReferences',[])
        block=rule.get('sourceBlockId',rule.get('source_block_id'))
        quote=rule.get('sourceQuote',rule.get('source_quote'))
        if block and quote: citations.append({'blockId':block,'quote':quote})
        reasons=[]
        for citation in citations:
            for (bid,span), entries in scopes.items():
                # Also detect a model wrapping an excluded fact in a longer literal quote.
                if bid==citation.get('blockId') and span and span in citation.get('quote',''):
                    reasons.extend(reason for entry in entries if (reason:=scope_reason(entry,target)))
        if reasons: excluded.append({'text':rule.get('text',''),'reason':reasons[0],'candidate':rule})
        else: kept.append(rule)
    return {**value,'rules':kept}, excluded
