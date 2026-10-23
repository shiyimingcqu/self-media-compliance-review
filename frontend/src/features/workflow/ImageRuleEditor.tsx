import { Button, Card, Input, Select, Space } from 'antd';
import { TextRuleEditor } from './TextRuleEditor';
import type { StandardDraft } from './standardsApi';

export const defaultImageRules: StandardDraft[] = [
  { text: '产品与品牌标识清晰完整', criterion: '对照品牌提供的产品资料检查封面及配图，产品主体完整，品牌标识清晰可辨；没有参考依据时标记待确认。', category: '图文画面', importance: 'must', origin: 'template', sourceBlockId: '', sourceQuote: '', enabled: true },
  { text: '文案与图片信息准确一致', criterion: '核对标题、正文和配图中的品牌名、产品名、卖点及活动信息，不得出现未经确认的功效、价格或优惠承诺。', category: '图文信息', importance: 'must', origin: 'template', sourceBlockId: '', sourceQuote: '', enabled: true },
  { text: '构图清晰自然，产品颜色真实', criterion: '建议画面明亮清晰，减少无关元素；避免滤镜使产品颜色明显失真。主观审美意见作为建议，交品牌确认。', category: '视觉建议', importance: 'suggested', origin: 'template', sourceBlockId: '', sourceQuote: '', enabled: true },
];
export function textRulesValid(rules: StandardDraft[]) { return rules.length > 0 && rules.some(r => r.importance === 'must') && rules.every(r => r.text.trim() && r.criterion.trim()); }
export function ImageRuleEditor({ rules, onChange, disabled }: { rules: StandardDraft[]; onChange: (rules: StandardDraft[]) => void; disabled: boolean }) {
  const update = (i: number, patch: Partial<StandardDraft>) => onChange(rules.map((r, n) => n === i ? { ...r, ...patch, origin: 'manual' } : r));
  return <><h3>图文规则</h3><p className="muted">针对封面、图片和配套文案逐条设置要求，独立于脚本及视频规则保存。</p>
    <TextRuleEditor scope="image" rules={rules} onChange={onChange} disabled={disabled} />
    <Space wrap><Button disabled={disabled || rules.length >= 60} onClick={() => onChange([...rules, { text: '', criterion: '', category: '图文检查', importance: 'must', origin: 'manual', sourceBlockId: '', sourceQuote: '', enabled: true }])}>添加图文规则</Button><Button disabled={disabled} onClick={() => onChange(defaultImageRules.map(r => ({ ...r })))}>恢复图文默认规则</Button></Space>
  </>;
}
