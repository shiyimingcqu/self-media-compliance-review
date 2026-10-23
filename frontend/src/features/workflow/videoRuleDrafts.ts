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
  const keys = new Set(cleaned.rules.map(rule => videoRuleTitleKey(rule.text)));
  const added: StandardDraft[] = [];
  for (const rule of incoming) {
    const key = videoRuleTitleKey(rule.text);
    if (!key || keys.has(key) || cleaned.rules.length + added.length >= 60) continue;
    keys.add(key); added.push(rule);
  }
  return { rules: [...cleaned.rules, ...added], removed: cleaned.removed, added: added.length };
}
