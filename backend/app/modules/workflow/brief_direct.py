"""One original-image request, product facts and four rule groups."""
from typing import Literal

from pydantic import Field, RootModel

from app.core.contracts import ContractModel
from app.core.errors import ApiError

PIPELINE_VERSION = 'direct-product-rules-v1'


class ProductAndRuleItem(ContractModel):
    text: str = Field(min_length=1, max_length=1000)
    page_no: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=3000)
    group: Literal['brand', 'product', 'sellingPoint', 'common', 'video', 'image', 'copy']


class ProductAndRules(RootModel[list[ProductAndRuleItem]]):
    root: list[ProductAndRuleItem] = Field(min_length=1, max_length=240)


def summarize_response(snapshot, response, run_id="saved"):
    """Trust the model summary; retain raw quotes as provenance without checking them."""
    parsed = ProductAndRules.model_validate(response.get('report'))
    product = {key: [] for key in ('brand', 'product', 'sellingPoint')}
    rules = {key: [] for key in ('common', 'video', 'image', 'copy')}
    facts = {key: [] for key in ('products', 'competitors', 'sellingPoints', 'requiredTerms', 'forbiddenTerms', 'publishRules', 'thresholds', 'freeform')}
    facts.update(brand={'value': '', 'blockId': '', 'quote': ''}, brandEntities=[], background='', warnings=[])
    summary_blocks = []
    mapping = {'brand': 'brands', 'product': 'products', 'sellingPoint': 'sellingPoints', 'common': 'freeform', 'video': 'freeform', 'image': 'freeform', 'copy': 'publishRules'}
    for index, item in enumerate(parsed.root):
        if item.page_no > snapshot['pageCount']:
            raise ApiError(400, 'BRIEF_VISION_PAGE_MISMATCH', '模型返回了不存在的原页，已保留回复；未自动重试')
        block_id = f"summary:{run_id}:{index}"
        summary_blocks.append({'id': block_id, 'pageNo': item.page_no, 'text': item.text,
                               'origin': 'ai-brief-summary', 'visionRunId': run_id})
        value = {'text': item.text, 'pageNo': item.page_no, 'quote': item.quote,
                 'sourceBlockId': block_id, 'citationStatus': 'model'}
        (product if item.group in product else rules)[item.group].append(value)
        # Compatibility evidence is explicitly model summary, not a verified original quote.
        fact = {'value': item.text, 'blockId': block_id, 'quote': item.text}
        if item.group in rules:
            fact['applicability'] = {'materials': ['shared' if item.group == 'common' else item.group],
                                     'stage': 'all' if item.group == 'common' else 'publishing' if item.group == 'copy' else 'execution',
                                     'section': item.group, 'sectionQuote': item.text, 'region': ''}
        target = mapping[item.group]
        if target != 'brands' and len(facts[target]) >= 30:
            continue
        elif target == 'brands':
            if not facts['brand']['value']: facts['brand'] = fact
        else:
            facts[target].append(fact)
    # These are native text blocks for compatibility, never a fabricated OCR transcript.
    pages = [{'pageNo': number, 'text': '\n'.join(b['text'] for b in snapshot['nativeTextBlocks'] if b.get('pageNo') == number),
              'visualDescription': '原图已提交模型提炼产品事实与四组规则；本次不返回逐页OCR全文。',
              'readability': 'readable', 'uncertainties': [], 'regions': [], 'structureComplete': False}
             for number in range(1, snapshot['pageCount'] + 1)]
    return {'summaryOnly': True, 'directSummary': {'productFacts': product, 'rules': rules}, 'facts': facts,
            'pendingFacts': [], 'factsComplete': bool(facts['brand']['value']), 'summaryBlocks': summary_blocks,
            'summaryValidationVersion': 'model-trusted-v1',
            'report': {'pages': pages, 'warnings': []}, 'coverageComplete': True, 'coverageVerified': False,
            'providerRequestIds': response.get('providerRequestIds', []), 'usage': response.get('usage', []),
            'progress': {'stage': 'direct', 'completed': 1, 'total': 1}}


def run_pipeline(settings, run_id, snapshot, config, secret, pages, call_vision, page_bytes, checkpoint):
    images = [(page, page_bytes(settings, run_id, page)) for page in pages]
    # Do not silently split into several paid calls or fall back to OCR.
    if sum((len(data) + 2) // 3 * 4 for _, data in images) > 20 * 1024 * 1024:
        raise ApiError(400, 'BRIEF_INPUT_LIMIT', '全部原页图片超过单次20MB限制，请拆分Brief；未提交模型')
    state = {'summaryOnly': True, 'progress': {'stage': 'direct', 'completed': 0, 'total': 1}}
    checkpoint(state)
    child = {**snapshot, 'nativeTextBlocks': [], 'visionStage': 'direct-summary',
             'stagePayload': {'physicalPages': [p['pageNo'] for p in pages]},
             'responseSchema': ProductAndRules.model_json_schema()}
    response = call_vision(config, secret, child, images)
    state['rawResponse'] = response
    if response.get('httpStatus'): state['httpStatus'] = response['httpStatus']
    checkpoint(state)
    if response.get('httpStatus'):
        raise ApiError(400, 'BRIEF_VISION_PROVIDER_FAILED', '模型请求失败，已保留回复；未自动重试')
    state.update(summarize_response(snapshot, response, run_id))
    return state
