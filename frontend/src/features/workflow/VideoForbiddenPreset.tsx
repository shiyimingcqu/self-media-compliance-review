import { useState } from 'react';
import { Button, Input, Popover, Select, Switch } from 'antd';
import type { StandardDraft } from './standardsApi';
const criterion = '检查品牌配置的禁用词句，结合否定、引用和上下文判断，不只凭单字命中；定位具体表达并按需保留有声片段，听不清时待确认。';
export function VideoForbiddenPreset({ rules, onChange, disabled }: { rules: StandardDraft[]; onChange: (rules: StandardDraft[]) => void; disabled: boolean }) {
  const rule = rules.find(rule => rule.presetKey === 'forbidden_expression');
  const terms = rule?.componentPlan?.nodes[0].parameters.terms || rule?.params?.terms || [];
  const [raw, setRaw] = useState('');
  function change(next: string[], importance = rule?.importance || 'must') {
    const updated: StandardDraft = { ...rule, text: '不使用禁用表达', criterion: rule?.criterion || criterion, layer: 'precise', presetKey: 'forbidden_expression',
      enabled: !!next.length && !!rule?.enabled, importance, origin: rule?.origin || 'template', category: '详细检查', sourceBlockId: rule?.sourceBlockId || '', sourceQuote: rule?.sourceQuote || '',
      params: { names: [], terms: next, thresholds: [] }, componentPlan: next.length ? { operator: 'all', sameSegment: false, nodes: [{ id: rule?.componentPlan?.nodes[0].id || crypto.randomUUID(), kind: 'forbidden_expression', parameters: {
        referenceImageIds: [], startSeconds: 0, endSeconds: null, requireWholeProduct: false, requireClearLogo: false, allowOcclusion: true,
        minContinuousSeconds: null, minTotalSeconds: null, minOccurrences: null, occurrenceGapSeconds: 0, terms: next, action: '',
      } }] } : null };
    onChange(rule ? rules.map(item => item === rule ? updated : item) : [...rules, updated]);
  }
  return <article className="video-preset-row" aria-label="不使用禁用表达">
    <Switch size="small" aria-label="启用不使用禁用表达" checked={!!rule?.enabled} disabled={disabled || !rule?.componentPlan} onChange={enabled => { if (rule) onChange(rules.map(item => item === rule ? { ...item, enabled } : item)); }} />
    <div className="video-preset-title"><strong>不使用禁用表达</strong><small>{terms.length ? `${terms.length} 条词句` : '未配置'}</small></div>
    <Select size="small" aria-label="不使用禁用表达重要性" value={rule?.importance || 'must'} disabled={disabled} options={[{ value: 'must', label: '必须' }, { value: 'suggested', label: '建议' }]} onChange={importance => change(terms, importance)} />
    <Popover trigger="click" title="禁用表达" onOpenChange={open => { if (open) setRaw(terms.join('\n')); }} content={<div className="video-criterion-editor"><strong>品牌禁用词句</strong><Input.TextArea aria-label="禁用表达词句" rows={5} value={raw} disabled={disabled} placeholder="每行一项" onChange={event => { setRaw(event.target.value); change(event.target.value.split('\n').map(v => v.trim()).filter(Boolean)); }} /><details><summary>判定说明</summary><p>{rule?.criterion || criterion}</p></details></div>}><Button size="small" type="text" aria-label="配置禁用表达">配置</Button></Popover>
  </article>;
}
