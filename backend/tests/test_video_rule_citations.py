import copy

import pytest

from app.core.errors import ApiError
from app.modules.workflow.standards import validate_recommendation,check_quotes,RecommendedStandards


BLOCKS=[{'id':'page20','pageNo':20,'text':'• 不可和其他竞品优惠活动进行比价\n• 不可拉踩竞品'},
        {'id':'page26','pageNo':26,'text':'政治立场与三观正确，避免引起容貌焦虑、引发歧视歧义。\n其他中间条目\n不得出现侮辱性言论'}]


def rule(block,quote):
    return {'text':'核对明确禁止事项','criterion':'按原文的具体范围核对','importance':'must','layer':'brief',
            'sourceBlockId':block,'sourceQuote':quote}


def test_reordered_excerpts_are_preserved_as_individually_verified_references():
    raw={'rules':[rule('page20','• 不可拉踩竞品\n• 不可和其他竞品优惠活动进行比价')],'warnings':[]}
    before=copy.deepcopy(raw)
    result=validate_recommendation(raw,BLOCKS,'video')
    assert raw==before
    refs=result.rules[0].source_references
    assert [ref.quote for ref in refs]==['• 不可拉踩竞品','• 不可和其他竞品优惠活动进行比价']
    assert all(ref.quote in BLOCKS[0]['text'] for ref in refs)
    assert result.rules[0].source_quote==refs[0].quote
    check_quotes(result.rules,BLOCKS)
    # Saved/manual rules do not silently reinterpret a stitched citation.
    with pytest.raises(ApiError):check_quotes(RecommendedStandards.model_validate(raw).rules,BLOCKS)


def test_nonadjacent_excerpts_do_not_get_a_fabricated_contiguous_quote():
    result=validate_recommendation({'rules':[rule('page26','不得出现侮辱性言论\n政治立场与三观正确')],'warnings':[]},BLOCKS,'video')
    assert len(result.rules[0].source_references)==2
    assert all(ref.quote in BLOCKS[1]['text'] for ref in result.rules[0].source_references)


@pytest.mark.parametrize('block,quote', [
    ('page20','不可拉踩竞品\n必须提及咖啡功效'),
    ('page20','不可拉踩竞品\n不得出现侮辱性言论'),
    ('missing','不可拉踩竞品\n不可和其他竞品优惠活动进行比价'),
])
def test_invented_cross_page_or_missing_block_excerpts_still_fail(block,quote):
    with pytest.raises(ApiError):validate_recommendation({'rules':[rule(block,quote)],'warnings':[]},BLOCKS,'video')


def test_repaired_references_still_enforce_material_scope():
    facts={'freeform':[{'value':'图片要求','blockId':'page20','quote':'不可拉踩竞品',
            'applicability':{'materials':['image'],'stage':'execution'}}]}
    result=validate_recommendation({'rules':[rule('page20','• 不可拉踩竞品\n• 不可和其他竞品优惠活动进行比价')],'warnings':[]},BLOCKS,'video',facts)
    assert not result.rules
