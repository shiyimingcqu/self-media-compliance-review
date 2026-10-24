import type { StandardDraft } from './standardsApi';

export const videoRuleCategories = [
  { key: 'required', label: '必须出现', hint: '产品、品牌、卖点与指定动作' },
  { key: 'forbidden', label: '禁止出现', hint: '竞品、禁用表达与禁止画面' },
  { key: 'content', label: '内容与脚本', hint: '脚本匹配、表达准确与创意方向' },
  { key: 'exposure', label: '时长与次数', hint: '露出时间、出现次数与范围' },
] as const;
export type VideoRuleCategory = typeof videoRuleCategories[number]['key'];

export function videoRuleCategory(rule: StandardDraft): VideoRuleCategory {
  const chosen = videoRuleCategories.find(c => c.label === rule.category);
  if (chosen) return chosen.key;
  const kinds = rule.componentPlan?.nodes.map(n => n.kind) || [];
  if (rule.baselineKey === 'competitor' || rule.baselineKey === 'forbidden' || rule.presetKey === 'forbidden_expression' || kinds.includes('forbidden_expression') || kinds.includes('product_mirroring')) return 'forbidden';
  if (rule.presetKey === 'product_count' || rule.presetKey === 'product_duration' || kinds.some(k => k === 'exposure_duration' || k === 'exposure_count')) return 'exposure';
  if (['script_match', 'identity', 'information_accuracy', 'understandability'].includes(rule.baselineKey || '')) return 'content';
  if (rule.baselineKey === 'product') return 'required';
  const text = `${rule.category} ${rule.text}`;
  if (/禁止|不得|不可|不能|不使用|不出现|不露出|避免|禁用|竞品|镜像/.test(text)) return 'forbidden';
  if (/秒|时长|次数|至少.{0,8}次|露出时间/.test(text) || rule.params?.thresholds?.some(t => /seconds|duration|occurrences|count/i.test(t.key))) return 'exposure';
  if (/一致|匹配|准确|正确|脚本|调性|创意|风格|结构|逻辑|前置/.test(text)) return 'content';
  if (kinds.some(k => ['product_presence','logo_presence','required_speech','specified_action'].includes(k)) || /出现|展示|露出|卖点|口播|品牌|产品|饮用/.test(text)) return 'required';
  return 'content';
}
