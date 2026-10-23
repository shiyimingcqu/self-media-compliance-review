import { describe, expect, it } from 'vitest';
import { videoRuleCategory } from './videoRuleCategories';
import type { StandardDraft } from './standardsApi';
const rule = (text: string, extra: Partial<StandardDraft> = {}): StandardDraft => ({ text, criterion: '明确判据', importance: 'must', category: '视频检查', enabled: true, origin: 'manual', sourceBlockId: '', sourceQuote: '', ...extra });
describe('video rule grouping', () => {
  it('groups business requirements without confusing importance and content type', () => {
    expect(videoRuleCategory(rule('展示指定产品', { importance: 'suggested' }))).toBe('required');
    expect(videoRuleCategory(rule('禁止出现竞品'))).toBe('forbidden');
    expect(videoRuleCategory(rule('产品至少出现5秒'))).toBe('exposure');
    expect(videoRuleCategory(rule('与已通过脚本基本匹配'))).toBe('content');
    expect(videoRuleCategory(rule('调性轻松自然'))).toBe('content');
  });
  it('respects brand category choices and explicit component and baseline semantics', () => {
    expect(videoRuleCategory(rule('检查产品展示', { category: '内容与脚本' }))).toBe('content');
    expect(videoRuleCategory(rule('任意标题', { baselineKey: 'competitor' }))).toBe('forbidden');
    expect(videoRuleCategory(rule('任意标题', { presetKey: 'product_count' }))).toBe('exposure');
  });
});
