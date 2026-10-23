import { useState } from 'react';
import { Button, InputNumber, Popover, Select, Switch } from 'antd';
import type { StandardDraft } from './standardsApi';
import type { ComponentNode } from './videoComponentsApi';

type PresetKey = 'product_count' | 'product_duration';
const definitions = [
  { key: 'product_count' as const, title: '产品出现次数检查', kind: 'exposure_count' as const, threshold: 'minOccurrences', unit: '次', hint: '按独立露出次数计算，同一连续镜头不重复计次。' },
  { key: 'product_duration' as const, title: '产品出现时间检查', kind: 'exposure_duration' as const, threshold: 'minTotalSeconds', unit: '秒', hint: '按全片累计出现时间计算，不额外要求连续露出。' },
];

export function VideoProductPresets({ rules, onChange, references, disabled, visibleKeys }: {
  rules: StandardDraft[]; onChange: (rules: StandardDraft[]) => void;
  references: { id: string; name: string }[]; disabled: boolean; visibleKeys?: string[];
}) {
  const [draftReferences, setDraftReferences] = useState<Partial<Record<PresetKey, string[]>>>({});
  return <div className="video-product-presets" aria-label="默认产品检查规则">
    {definitions.filter(definition => !visibleKeys || visibleKeys.includes(definition.key)).map(definition => {
      const rule = rules.find(rule => rule.presetKey === definition.key);
      const node = rule?.componentPlan?.nodes[0];
      const value = rule?.params?.thresholds?.find(t => t.key === definition.threshold)?.value
        ?? (definition.kind === 'exposure_count' ? node?.parameters.minOccurrences : node?.parameters.minTotalSeconds) ?? null;
      const referenceIds = draftReferences[definition.key] ?? node?.parameters.referenceImageIds ?? [];
      const valid = (n: number | null) => n != null && n > 0 && n <= (definition.unit === '次' ? 30 : 600) && (definition.unit !== '次' || Number.isInteger(n));
      function change(nextValue: number | null, nextRefs: string[]) {
        setDraftReferences(current => ({ ...current, [definition.key]: nextRefs }));
        const parameters = {
          referenceImageIds: nextRefs, startSeconds: 0, endSeconds: null,
          minContinuousSeconds: null, minTotalSeconds: definition.kind === 'exposure_duration' ? nextValue : null,
          minOccurrences: definition.kind === 'exposure_count' ? nextValue : null,
          occurrenceGapSeconds: 0, requireWholeProduct: false, requireClearLogo: false, allowOcclusion: true,
          terms: [], action: '',
        };
        const configured = valid(nextValue) && nextRefs.length > 0;
        const updated: StandardDraft = {
          ...rule, text: definition.title,
          criterion: `以品牌选定的产品参考图识别产品，检查全片${definition.unit === '次' ? '独立出现次数' : '累计出现时间'}至少${nextValue ?? '（待填写）'}${definition.unit}。${definition.hint}看不清或覆盖不足时待确认。`,
          layer: 'precise', presetKey: definition.key, enabled: configured && !!rule?.enabled,
          importance: rule?.importance || 'must', category: '详细检查', origin: 'template',
          sourceBlockId: rule?.sourceBlockId || '', sourceQuote: rule?.sourceQuote || '',
          params: { names: rule?.params?.names || [], terms: [], thresholds: nextValue == null ? [] : [{ key: definition.threshold, value: nextValue }] },
          componentPlan: configured ? { operator: 'all', sameSegment: false, nodes: [{ id: node?.id || crypto.randomUUID(), kind: definition.kind, parameters } as ComponentNode] } : null,
        };
        onChange(rule ? rules.map(item => item === rule ? updated : item) : [...rules, updated]);
      }
      return <article className="video-preset-row" key={definition.key} aria-label={definition.title}>
        <Switch size="small" aria-label={`启用${definition.title}`} checked={!!rule?.enabled} disabled={disabled || !rule?.componentPlan} onChange={enabled => { if (rule) onChange(rules.map(item => item === rule ? { ...item, enabled } : item)); }} />
        <div className="video-preset-title"><strong>{definition.title}</strong><small>{value == null ? '未配置' : `至少 ${value} ${definition.unit}`}</small></div>
        <Select size="small" aria-label={`${definition.title}重要性`} value={rule?.importance || 'must'} disabled={disabled} options={[{ value: 'must', label: '必须' }, { value: 'suggested', label: '建议' }]} onChange={importance => {
          if (rule) onChange(rules.map(item => item === rule ? { ...item, importance } : item));
          else onChange([...rules, { text: definition.title, criterion: definition.hint, layer: 'precise', presetKey: definition.key, enabled: false, importance, category: '详细检查', origin: 'template', sourceBlockId: '', sourceQuote: '' }]);
        }} />
        <Popover trigger="click" title={definition.title} content={<div className="video-preset-config">
        <div className="video-preset-options"><label>至少 <InputNumber aria-label={`${definition.title}至少${definition.unit}`} min={definition.unit === '次' ? 1 : 0.01} max={definition.unit === '次' ? 30 : 600} precision={definition.unit === '次' ? 0 : 2} value={value} placeholder="填写" disabled={disabled} onChange={number => change(number, referenceIds)} /> {definition.unit}</label>
          <Select size="small" aria-label={`${definition.title}产品参考图`} mode="multiple" maxCount={4} placeholder="选择产品参考图" value={referenceIds} disabled={disabled} options={references.map(ref => ({ value: ref.id, label: ref.name }))} onChange={ids => change(value, ids)} />
        </div>
            <details><summary>判定说明</summary><p>{rule?.criterion || definition.hint}</p></details>
        </div>}><Button size="small" type="text" aria-label={`配置${definition.title}`}>配置</Button></Popover>
      </article>;
    })}
  </div>;
}
