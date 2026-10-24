import type { StandardDraft } from './standardsApi';

export function cleanVideoDraft(rules: StandardDraft[], excludedTexts: string[] = []) {
  const excluded = new Set(excludedTexts);
  const kept = rules.filter(rule => rule.origin !== 'ai' || rule.layer === 'baseline' || !excluded.has(rule.text));
  return { rules: kept, removed: rules.length - kept.length };
}

// Strip a stock lead-in, preserving names, negation, numbers and conditions.
export function videoRuleTitleKey(text: string) {
  return text.trim().replace(/^(?:在)?(?:视频中|视频内|成片中|成片内)/, '')
    .replace(/^(?:必须|应当|须|需|应)/, '').replace(/\s+/g, '');
}

export function mergeVideoDraft(current: StandardDraft[], incoming: StandardDraft[], excludedTexts: string[] = []) {
  const cleaned = cleanVideoDraft(current, excludedTexts);
  const rules = [...cleaned.rules];
  const keys = new Set(rules.map(rule => videoRuleTitleKey(rule.text)));
  const added: StandardDraft[] = [];
  for (const rule of incoming) {
    if (rule.layer === 'baseline' && rule.baselineKey) {
      const index = rules.findIndex(item => item.layer === 'baseline' && item.baselineKey === rule.baselineKey);
      if (index >= 0) {
        const previous = rules[index];
        rules[index] = { ...rule, enabled: previous.enabled, importance: previous.importance };
        keys.add(videoRuleTitleKey(rule.text));
        continue;
      }
      if (added.some(item => item.layer === 'baseline' && item.baselineKey === rule.baselineKey)) continue;
    }
    const key = videoRuleTitleKey(rule.text);
    if (!key || keys.has(key) || rules.length + added.length >= 60) continue;
    keys.add(key); added.push(rule);
  }
  return { rules: [...rules, ...added], removed: cleaned.removed, added: added.filter(rule => rule.layer !== 'baseline').length };
}
