import type { DirectSummary } from './projectBriefApi';
import type { StandardDraft } from './standardsApi';

export function videoExposureCriterion(text: string): string {
  // Keep the source wording; add the brand's agreed interpretation to editable drafts.
  const exposure = /(?:产品|包装|杯).*(?:露出|展示|入镜)|(?:露出|展示|入镜).*(?:产品|包装|杯)/i.test(text);
  const logo = /logo|标识|商标/i.test(text);
  const strict = /零遮挡|完全无遮挡|任何.{0,8}(?:遮挡|被挡)|逐帧|每一帧/i.test(text);
  if (!exposure || !logo || strict) return text;
  return `${text}\n执行口径：在规定时间窗口内有实物和品牌清晰可辨认的有效展示即可。普通“手持不遮挡Logo”指不影响关键品牌文字或图形的辨认，不要求Logo边框零接触或零局部遮挡；正常持杯、饮用、转杯及不影响识别的轻微遮挡不否决已有展示，不要求全程正对或持续静止。已能辨认品牌主体时，不得仅因手指触及标识边缘而判未通过；人物滤镜或注意力偏好也不是产品未露出的证据。保留原文明确的时间窗口、持续时长和次数，不自行增加阈值。看不清、窗口未完整检查或是否实质遮挡有争议时标待确认；只有完整检查后确认不存在符合识别要求的展示时才判未通过。`;
}

export function briefDirectDraft(summary: DirectSummary, scope: 'video' | 'image'): StandardDraft[] {
  const items = [...summary.rules.common, ...summary.rules[scope], ...(scope === 'image' ? summary.rules.copy : [])];
  const seen = new Set<string>();
  return items.filter(item => {
    if (seen.has(item.text)) return false;
    seen.add(item.text); return true;
  }).map(item => ({ text: item.text, criterion: scope === 'video' ? videoExposureCriterion(item.text) : item.text, category: '内容', importance: 'must', origin: 'ai',
    sourceBlockId: '', sourceQuote: '', enabled: true,
    ...(scope === 'video' ? { layer: 'brief' as const } : {}) }));
}
