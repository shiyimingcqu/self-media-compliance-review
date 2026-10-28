import { expect, it } from 'vitest';
import { briefDirectDraft, videoExposureCriterion } from './briefDirectDraft';
import type { DirectSummary, DirectSummaryItem } from './projectBriefApi';

it('loads common and material rules, trusts model items without quote gates, deduplicates', () => {
  const item = (text: string, verified = true): DirectSummaryItem => ({ text, pageNo: 20, quote: text, sourceBlockId: verified ? 'native' : '', citationStatus: verified ? 'verified' : 'pending' });
  const summary: DirectSummary = { productFacts: { brand: [], product: [], sellingPoint: [] }, rules: { common: [item('共用')], video: [item('视频', false), item('共用')], image: [item('图片')], copy: [item('文案')] } };
  const video = briefDirectDraft(summary, 'video');
  expect(video.map(rule => rule.text)).toEqual(['共用', '视频']);
  expect(video[1]).toMatchObject({ enabled: true, sourceBlockId: '', sourceQuote: '', layer: 'brief' });
  expect(briefDirectDraft(summary, 'image').map(rule => rule.text)).toEqual(['共用', '图片', '文案']);
});

it('carries exposure tolerance through Brief loading, while preserving strict and timed requirements', () => {
  const text = '视频前15秒需清晰且完整露出产品，Logo正对镜头，对焦清晰，手持不遮挡Logo。';
  const item: DirectSummaryItem = { text, pageNo: 20, quote: text, sourceBlockId: '', citationStatus: 'model' };
  const summary: DirectSummary = { productFacts: { brand: [], product: [], sellingPoint: [] }, rules: { common: [], video: [item], image: [item], copy: [] } };
  const video = briefDirectDraft(summary, 'video')[0];
  expect(video.text).toBe(text);
  expect(video.criterion).toContain('不要求Logo边框零接触或零局部遮挡');
  expect(video.criterion).toContain('前15秒');
  expect(briefDirectDraft(summary, 'image')[0].criterion).toBe(text);
  const strict = text + '品牌另要求Logo零遮挡，连续露出3秒。';
  expect(videoExposureCriterion(strict)).toBe(strict);
  expect(videoExposureCriterion('禁止出现竞品Logo')).toBe('禁止出现竞品Logo');
});
