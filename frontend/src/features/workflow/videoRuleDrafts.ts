import type { StandardDraft } from './standardsApi';

export function cleanVideoDraft(rules: StandardDraft[], excludedTexts: string[] = []) {
  const excluded = new Set(excludedTexts);
  const kept = rules.filter(rule => rule.origin !== 'ai' || rule.layer === 'baseline' || !excluded.has(rule.text));
  return { rules: kept, removed: rules.length - kept.length };
}

// Strip a stock lead-in, preserving names, negation, numbers and conditions.
export function videoRuleTitleKey(text: string) {
  return text.normalize('NFKC').trim().replace(/^(?:在)?(?:视频中|视频内|成片中|成片内)/, '')
    .replace(/^(?:视频|成片)(?=前|须|需|必须|应当|不得|禁止)/, '')
    .replace(/(^|秒|分钟|内)(?:必须|应当|须|需|应)/g, '$1')
    .replace(/[\s，。；：、！？,!;:“”"'‘’（）()]/g, '').replace(/\.$/, '');
}

function sameRequirement(a: StandardDraft, b: StandardDraft) {
  // Never merge a tool configuration, product list or numeric change by title alone.
  const parameters = (r: StandardDraft) => JSON.stringify({ names: r.params?.names || [], terms: r.params?.terms || [], thresholds: r.params?.thresholds || [], componentPlan: r.componentPlan || null });
  if (parameters(a) !== parameters(b)) return false;
  const numbers = (r: StandardDraft) => [...new Set((r.text + '\n' + r.criterion).match(/\d+(?:\.\d+)?/g) || [])].sort().join(',');
  if (numbers(a) !== numbers(b)) return false;
  const at = videoRuleTitleKey(a.text), bt = videoRuleTitleKey(b.text);
  const ac = videoRuleTitleKey(a.criterion.split('\n执行口径：')[0]), bc = videoRuleTitleKey(b.criterion.split('\n执行口径：')[0]);
  if (at && at === bt) return ac === bc || a.origin === 'manual' || b.origin === 'manual';
  // An imported Brief rule uses the original requirement as its title. A generated
  // rule can shorten that title while retaining the same requirement as criterion.
  return !!at && !!bt && ((at.length >= 12 && at === bc) || (bt.length >= 12 && bt === ac));
}

export function mergeVideoDraft(current: StandardDraft[], incoming: StandardDraft[], excludedTexts: string[] = []) {
  const cleaned = cleanVideoDraft(current, excludedTexts);
  const rules: StandardDraft[] = [];
  for (const rule of cleaned.rules) {
    const index = rule.layer === 'baseline' ? -1 : rules.findIndex(item => item.layer === rule.layer && sameRequirement(item, rule));
    if (index < 0 || (rule.origin === 'manual' && rules[index].origin === 'manual')) rules.push(rule);
    else if (rule.origin === 'manual') rules[index] = rule;
  }
  const added: StandardDraft[] = [];
  for (const rule of incoming) {
    if (rule.layer === 'baseline' && rule.baselineKey) {
      const index = rules.findIndex(item => item.layer === 'baseline' && item.baselineKey === rule.baselineKey);
      if (index >= 0) {
        const previous = rules[index];
        if (previous.origin !== 'manual') rules[index] = { ...rule, enabled: previous.enabled, importance: previous.importance };
        continue;
      }
      if (added.some(item => item.layer === 'baseline' && item.baselineKey === rule.baselineKey)) continue;
    }
    const key = videoRuleTitleKey(rule.text);
    if (!key || [...rules, ...added].some(item => item.layer === rule.layer && sameRequirement(item, rule)) || rules.length + added.length >= 60) continue;
    added.push(rule);
  }
  return { rules: [...rules, ...added], removed: cleaned.removed, added: added.filter(rule => rule.layer !== 'baseline').length };
}
