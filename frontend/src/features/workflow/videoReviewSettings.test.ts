import { describe, expect, it } from 'vitest';
import { editableVideoRules, readableBaselineRule, universalVideoTemplate } from './videoReviewSettings';
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


it('specializes the universal template without converting reference facts into requirements', () => {
  const defaults = [product, { ...product, baselineKey: 'competitor' as const, enabled: false }, { ...product, baselineKey: 'information_accuracy' as const, text: '已表达的产品信息准确' }];
  const facts = { products: [{ value: '芝士浓奶铁' }], competitors: [{ value: '竞品A' }], sellingPoints: [{ value: '22g萃取' }] } as import('./projectBriefApi').BriefFacts;
  const result = universalVideoTemplate(defaults, ['芝士浓奶铁'], '麦咖啡', facts);
  expect(result).toHaveLength(3);
  expect(result[0].params?.names).toEqual(['芝士浓奶铁']);
  expect(result[1].enabled).toBe(false);
  expect(result[1].params?.names).toEqual(['竞品A']);
  expect(result[2].text).toBe('已表达的产品信息准确');
  expect(result[2].params?.terms).toEqual(['22g萃取']);
  expect(defaults[0].params?.names).toEqual(['麦咖啡芝士浓奶铁']);
});


it('keeps the reset template free of project products, brands and selling points', () => {
  const defaults: StandardDraft[] = [
    product,
    { ...product, text: '品牌与产品信息没有明显错误', baselineKey: 'identity' },
    { ...product, text: '已表达的产品信息准确', baselineKey: 'information_accuracy', params: { names: [], terms: ['咖啡浓'], thresholds: [] } },
  ];
  const blank = universalVideoTemplate(defaults);
  expect(blank[0].text).toBe('出现指定产品');
  expect(blank.every(rule => !rule.params?.names?.length && !rule.params?.terms?.length)).toBe(true);
  expect(JSON.stringify(blank)).not.toContain('咖啡');
  expect(defaults[0].params?.names).toEqual(['麦咖啡芝士浓奶铁']);
});
