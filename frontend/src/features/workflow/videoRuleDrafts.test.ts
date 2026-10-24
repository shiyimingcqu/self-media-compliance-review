import { describe, expect, it } from 'vitest';
import { cleanVideoDraft, mergeVideoDraft } from './videoRuleDrafts';
import type { StandardDraft } from './standardsApi';

const rule = (text: string, extra: Partial<StandardDraft> = {}): StandardDraft => ({ text, criterion: text, category: '内容', importance: 'must', origin: 'ai', layer: 'brief', enabled: true, sourceBlockId: '', sourceQuote: '', ...extra });

describe('video draft cleanup and merge', () => {
  it('cleans only classified old AI items, preserving manual and baseline rules', () => {
    const title = '笔记发布后3个月不得删除';
    const source = [rule(title, { enabled: false }), rule(title, { origin: 'manual' }), rule(title, { layer: 'baseline' }), rule('视频中须出现产品')];
    const result = cleanVideoDraft(source, [title]);
    expect(result.removed).toBe(1);
    expect(result.rules).toEqual(source.slice(1));
    expect(source).toHaveLength(4);
  });
  it('does not duplicate a requirement by adding 视频中须 and preserves its switch', () => {
    const result = mergeVideoDraft([rule('需强调活动机制和套餐内容', { enabled: false })], [rule('视频中须强调活动机制和套餐内容'), rule('视频中须强调活动机制和套餐内容')]);
    expect(result.added).toBe(0);
    expect(result.rules[0].enabled).toBe(false);
  });
  it('retains different thresholds, negation and product names', () => {
    const result = mergeVideoDraft([rule('产品至少出现5秒')], [rule('产品至少出现10秒'), rule('不得出现产品'), rule('须出现产品A'), rule('须出现产品B')]);
    expect(result.added).toBe(4);
  });
  it('cleans old rules even when the model adds zero candidates', () => {
    const result = mergeVideoDraft([rule('预热期必带TAG'), rule('产品出现')], [], ['预热期必带TAG']);
    expect(result.removed).toBe(1);
    expect(result.rules.map(r => r.text)).toEqual(['产品出现']);
  });
});


it('fills baseline wording by stable key while preserving brand settings and custom rules', () => {
  const existing = rule('出现指定产品', { layer: 'baseline', baselineKey: 'product', enabled: false, importance: 'suggested' });
  const incoming = rule('视频中展示麦咖啡芝士浓奶铁', { layer: 'baseline', baselineKey: 'product', enabled: true, params: { names: ['麦咖啡芝士浓奶铁'], terms: [], thresholds: [] } });
  const custom = rule('品牌手动追加的要求', { origin: 'manual' });
  const first = mergeVideoDraft([existing, custom], [incoming]);
  expect(first.rules).toHaveLength(2);
  expect(first.rules[0]).toMatchObject({ text: incoming.text, enabled: false, importance: 'suggested', params: incoming.params });
  expect(first.rules[1]).toEqual(custom);
  expect(first.added).toBe(0);
  expect(existing.text).toBe('出现指定产品');
  expect(mergeVideoDraft(first.rules, [incoming]).rules).toEqual(first.rules);
});
