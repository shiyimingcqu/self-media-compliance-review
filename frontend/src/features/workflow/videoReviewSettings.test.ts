import { describe, expect, it } from 'vitest';
import { editableVideoRules, readableBaselineRule } from './videoReviewSettings';
import type { StandardDraft } from './standardsApi';

const product: StandardDraft = { text: '出现指定产品', criterion: '核对指定产品', category: '基础检查', sourceBlockId: '', sourceQuote: '', origin: 'template', enabled: true, importance: 'must', layer: 'baseline', baselineKey: 'product', params: { names: ['麦咖啡芝士浓奶铁'], terms: [], thresholds: [] } };

describe('readable baseline names', () => {
  it('expands known products and competitors without changing their facts', () => {
    const expanded = editableVideoRules([product])[0];
    expect(expanded.text).toBe('视频中须展示麦咖啡芝士浓奶铁');
    expect(expanded.params).toEqual(product.params);
    expect(readableBaselineRule({ ...product, text: '不出现指定竞品', baselineKey: 'competitor', params: { names: ['竞品甲'], terms: [], thresholds: [] } }).text).toBe('视频中不得出现竞品甲');
    expect(readableBaselineRule({ ...product, params: { names: [], terms: [], thresholds: [] } }).text).toBe('出现指定产品');
  });
  it('updates a derived title when facts change but preserves brand edits', () => {
    const expanded = readableBaselineRule(product);
    const changed = { ...expanded, params: { names: ['新产品全名'], terms: [], thresholds: [] } };
    expect(readableBaselineRule(changed, expanded).text).toBe('视频中须展示新产品全名');
    expect(readableBaselineRule({ ...changed, text: '展示杯身正面' }, expanded).text).toBe('展示杯身正面');
    expect(product.text).toBe('出现指定产品');
  });
});
