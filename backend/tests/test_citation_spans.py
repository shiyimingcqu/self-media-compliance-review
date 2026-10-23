"""引文跨块：PDF 页内原文常被拆成多个块，模型会把相邻块拼成一句话引用。"""
import pytest

from app.core.errors import ApiError
from app.modules.workflow import standards

# 第 20 页在原文里被拆成两块，第 21 页是另一页。
BLOCKS = [
    {'id': 'b1', 'pageNo': 20, 'text': '产品露出：视频前15s需清晰且完整露出产品，logo正对镜头，'},
    {'id': 'b2', 'pageNo': 20, 'text': '对焦清晰，\n手持不遮挡 logo'},
    {'id': 'b3', 'pageNo': 21, 'text': '第21页的其他要求。'},
]
CROSS_BLOCK = '产品露出：视频前15s需清晰且完整露出产品，logo正对镜头，对焦清晰，\n手持不遮挡 logo'


def rule(quote, block_id='b1'):
    return {'text': '产品露出要求', 'criterion': '视频前 15 秒须清晰露出产品', 'category': '画面',
            'importance': 'must', 'source_block_id': block_id, 'source_quote': quote,
            'params': {'names': [], 'terms': [], 'thresholds': []}}


def test_cross_block_same_page_quote_is_repaired_with_warning():
    report = standards.validate_recommendation({'rules': [rule(CROSS_BLOCK)], 'warnings': []}, BLOCKS)
    repaired = report.rules[0]
    assert repaired.source_block_id == 'b1'
    assert '产品露出：视频前15s需清晰且完整露出产品' in repaired.source_quote
    notes=[warning for warning in report.warnings if '跨了同一页的多个原文块' in warning]
    assert notes and all(note.startswith(standards.SYSTEM_NOTE_PREFIX) for note in notes)


def test_cross_block_match_ignores_whitespace_differences():
    # 模型把块间换行写成空格或直接吞掉，仍应定位到同一页连续原文。
    loose = CROSS_BLOCK.replace('\n', ' ')
    report = standards.validate_recommendation({'rules': [rule(loose)], 'warnings': []}, BLOCKS)
    assert report.rules[0].source_block_id == 'b1'
    assert '\n' in report.rules[0].source_quote


def test_quote_spanning_different_pages_is_rejected():
    across = '对焦清晰，\n手持不遮挡 logo第21页的其他要求。'
    with pytest.raises(ApiError) as error:
        standards.validate_recommendation({'rules': [rule(across, 'b2')], 'warnings': []}, BLOCKS)
    assert error.value.code == 'INVALID_STANDARD_SOURCE'
    assert error.value.details['ruleNumbers'] == [1]


def test_rewritten_quote_is_still_rejected():
    with pytest.raises(ApiError) as error:
        standards.validate_recommendation({'rules': [rule('视频前15秒要把产品拍清楚')], 'warnings': []}, BLOCKS)
    assert error.value.code == 'INVALID_STANDARD_SOURCE'


def test_normalized_fact_value_reuses_verified_original_quote():
    blocks = [{'id': 'b1', 'pageNo': 20, 'text': '不要做×\n• 露出竞品'}]
    facts = {'forbiddenTerms': [{'value': '不可露出竞品', 'blockId': 'b1', 'quote': '露出竞品'}]}
    value = {'rules': [rule('不可露出竞品')]}
    report = standards.validate_recommendation(value, blocks, facts=facts)
    assert report.rules[0].source_quote == '露出竞品'
    assert report.rules[0].text == value['rules'][0]['text']
    assert report.rules[0].criterion == value['rules'][0]['criterion']
    assert value['rules'][0]['source_quote'] == '不可露出竞品'
    standards.check_quotes(report.rules, blocks)


@pytest.mark.parametrize('fact', [
    {'value': '不可露出竞品', 'blockId': 'b1', 'quote': '原文不存在'},
    {'value': '不可露出竞品', 'blockId': 'b2', 'quote': '露出竞品'},
    {'value': '不可饮用竞品', 'blockId': 'b1', 'quote': '露出竞品'},
])
def test_unverified_or_unrelated_fact_cannot_replace_quote(fact):
    blocks = [{'id': 'b1', 'pageNo': 20, 'text': '露出竞品'}, {'id': 'b2', 'pageNo': 21, 'text': '露出竞品'}]
    with pytest.raises(ApiError):
        standards.validate_recommendation({'rules': [rule('不可露出竞品')]}, blocks, facts={'freeform': [fact]})


def validated(quote, block_id='b1'):
    return standards.StandardRule.model_validate(rule(quote, block_id))


def test_frozen_cross_block_citation_passes_check_quotes_only_from_first_block():
    standards.check_quotes([validated(CROSS_BLOCK)], BLOCKS)
    with pytest.raises(ApiError):
        # 引用了覆盖区间的中间块，而不是起始块：无法证明引用从该块开始。
        standards.check_quotes([validated(CROSS_BLOCK, 'b2')], BLOCKS)


def test_single_block_quotes_keep_working():
    standards.check_quotes([validated('对焦清晰，', 'b2')], BLOCKS)

def test_repair_repoints_when_cited_block_is_a_neighbour_on_the_same_page():
    # 模型引用了覆盖区间的后一块而不是起始块：生成阶段重新定位到起始块并提示。
    report = standards.validate_recommendation({'rules': [rule(CROSS_BLOCK, 'b2')], 'warnings': []}, BLOCKS)
    assert report.rules[0].source_block_id == 'b1'
    assert any('并把出处改到' in warning for warning in report.warnings)


def test_quote_cited_on_another_page_is_rejected():
    with pytest.raises(ApiError) as error:
        standards.validate_recommendation({'rules': [rule(CROSS_BLOCK, 'b3')], 'warnings': []}, BLOCKS)
    assert error.value.code == 'INVALID_STANDARD_SOURCE'
