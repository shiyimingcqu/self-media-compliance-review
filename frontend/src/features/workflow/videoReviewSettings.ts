import type { StandardDraft } from './standardsApi';
export type VideoReviewLevel = 'basic' | 'custom' | 'strict';
export const reviewDepth = { basic: 1, custom: 2, strict: 3 };
export function readableBaselineRule(rule: StandardDraft, previous: StandardDraft = rule): StandardDraft {
  const title = (value: StandardDraft) => {
    const names = value.params?.names?.filter(Boolean).join('、');
    if (value.baselineKey === 'product') return names ? `视频中须展示${names}` : '出现指定产品';
    if (value.baselineKey === 'competitor') return names ? `视频中不得出现${names}` : '不出现指定竞品';
    return value.text;
  };
  if (rule.layer !== 'baseline' || !['product', 'competitor'].includes(rule.baselineKey || '')) return rule;
  const generic = rule.baselineKey === 'product' ? '出现指定产品' : '不出现指定竞品';
  // Only expand template titles; preserve names explicitly edited by the brand.
  return rule.text === generic || rule.text === title(previous) ? { ...rule, text: title(rule) } : rule;
}
export function savedReviewLevel(rules?: StandardDraft[]): VideoReviewLevel {
  const level = rules?.map(rule => (rule as StandardDraft & { videoReviewLevel?: VideoReviewLevel }).videoReviewLevel).find(Boolean);
  return level || 'strict';
}
export function editableVideoRules(rules: StandardDraft[]): StandardDraft[] {
  return rules.map(source => {
    const rule = readableBaselineRule(source);
    if (rule.layer !== 'baseline' || rule.baselineKey !== 'forbidden') return rule;
    const terms = rule.params?.terms || [];
    return { ...rule, layer: 'precise', baselineKey: null, presetKey: 'forbidden_expression', enabled: !!terms.length && rule.enabled !== false,
      componentPlan: terms.length ? { operator: 'all', sameSegment: false, nodes: [{ id: crypto.randomUUID(), kind: 'forbidden_expression', parameters: {
        referenceImageIds: [], startSeconds: 0, endSeconds: null, requireWholeProduct: false, requireClearLogo: false, allowOcclusion: true,
        minContinuousSeconds: null, minTotalSeconds: null, minOccurrences: null, occurrenceGapSeconds: 0, terms, action: '',
      } }] } : null };
  });
}

// 阈值键名是给程序读的英文标识，品牌界面上必须换成中文，否则用户认不出这是什么条件。
const thresholdLabels: Record<string, (value: string | number) => string> = {
  minContinuousSeconds: value => `连续露出不少于 ${value} 秒`,
  minTotalSeconds: value => `累计露出不少于 ${value} 秒`,
  minOccurrences: value => `出现不少于 ${value} 次`,
  startSeconds: value => `从第 ${value} 秒开始`,
  endSeconds: value => `到第 ${value} 秒为止`,
  occurrenceGapSeconds: value => `每次间隔不少于 ${value} 秒`,
};
export function thresholdText(key: string, value: string | number): string {
  const label = thresholdLabels[key];
  return label ? label(value) : `${key}：${value}`;
}

export function universalVideoTemplate(defaults: StandardDraft[], names: string[] = [], brand = '', facts?: import('./projectBriefApi').BriefFacts | null): StandardDraft[] {
  const products = [...new Set([...names, ...(facts?.products || []).map(item => item.value)].filter(Boolean))];
  const identity = [...new Set([brand, facts?.brand?.value || '', ...products].filter(Boolean))];
  return defaults.map(source => readableBaselineRule({ ...structuredClone(source), params: {
    names: source.baselineKey === 'product' ? products : source.baselineKey === 'identity' ? identity : source.baselineKey === 'competitor' ? (facts?.competitors || []).map(item => item.value) : [],
    terms: source.baselineKey === 'information_accuracy' ? (facts?.sellingPoints || []).map(item => item.value) : [], thresholds: [],
  } }));
}
