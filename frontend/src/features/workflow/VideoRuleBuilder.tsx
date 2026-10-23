import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Empty, Image, Input, InputNumber, Popover, Select, Space, Switch, Tag } from 'antd';
import type { StandardDraft } from './standardsApi';
import { newNode, referenceImageUrl, videoComponentsApi } from './videoComponentsApi';
import type { ComponentDefinition, ComponentKind, ComponentNode, ComponentPlan } from './videoComponentsApi';
import './video-components.css';

const visual = new Set<ComponentKind>(['product_presence', 'logo_presence', 'product_mirroring', 'exposure_duration', 'exposure_count', 'specified_action']);
const needsReference = (kind: ComponentKind) => visual.has(kind) && kind !== 'specified_action';
export function videoRulesError(rules: StandardDraft[]) {
  if (!rules.length) return '至少保留一条视频规则';
  if (!rules.some(r => r.importance === 'must')) return '至少保留一条视频必须项';
  if (rules.some(r => !r.text.trim() || !r.criterion.trim())) return '请填写每条视频规则的名称和说明';
  if (rules.reduce((n, r) => n + (r.componentPlan?.nodes.length || 0), 0) > 8) return '每次审核最多8个检查组件';
  for (let i = 0; i < rules.length; i++) for (const node of rules[i].componentPlan?.nodes || []) {
    if (needsReference(node.kind) && !node.parameters.referenceImageIds?.length) return `第${i + 1}条：请为视觉组件选择品牌参考图`;
    if (['forbidden_expression', 'required_speech'].includes(node.kind) && !node.parameters.terms?.length) return `第${i + 1}条：请填写检查词句`;
    if (node.kind === 'specified_action' && !node.parameters.action?.trim()) return `第${i + 1}条：请填写指定动作`;
    if (node.parameters.endSeconds != null && node.parameters.endSeconds <= (node.parameters.startSeconds || 0)) return `第${i + 1}条：结束时间须大于开始时间`;
  }
}

function NodeEditor({ node, definitions, references, disabled, onChange, onRemove }: { node: ComponentNode; definitions: ComponentDefinition[]; references: { id: string; name: string }[]; disabled: boolean; onChange: (node: ComponentNode) => void; onRemove: () => void }) {
  const definition = definitions.find(d => d.kind === node.kind), p = node.parameters;
  const patch = (value: Partial<ComponentNode['parameters']>) => onChange({ ...node, parameters: { ...p, ...value } });
  return <div className="video-component-node">
    <div className="component-line"><Select aria-label="检查组件类型" value={node.kind} disabled={disabled} style={{ minWidth: 190 }} options={definitions.map(d => ({ value: d.kind, label: d.name }))} onChange={kind => onChange({ ...newNode(kind), id: node.id, parameters: { ...newNode(kind).parameters, referenceImageIds: needsReference(kind) ? p.referenceImageIds : [] } })} /><Button type="text" danger disabled={disabled} onClick={onRemove}>移除组件</Button></div>
    {needsReference(node.kind) && <label className="component-field"><span>品牌参考图 <span className="required">*</span></span><Select aria-label="组件参考图" mode="multiple" maxCount={4} value={p.referenceImageIds} disabled={disabled} placeholder="选择产品或商标参考图" options={references.map(r => ({ value: r.id, label: r.name }))} onChange={referenceImageIds => patch({ referenceImageIds })} /></label>}
    <div className="component-range"><span>检查范围</span><InputNumber aria-label="检查开始秒数" value={p.startSeconds} min={0} max={599} disabled={disabled} addonAfter="秒起" onChange={v => patch({ startSeconds: v ?? 0 })} /><span>至</span><InputNumber aria-label="检查结束秒数" value={p.endSeconds} min={0.01} max={600} disabled={disabled} placeholder="视频结尾" addonAfter="秒" onChange={v => patch({ endSeconds: v })} /></div>
    {visual.has(node.kind) && node.kind !== 'specified_action' && <Space wrap><Checkbox checked={p.requireWholeProduct} disabled={disabled} onChange={e => patch({ requireWholeProduct: e.target.checked })}>产品完整入画</Checkbox><Checkbox checked={p.requireClearLogo || node.kind === 'logo_presence'} disabled={disabled || node.kind === 'logo_presence'} onChange={e => patch({ requireClearLogo: e.target.checked })}>Logo可辨认</Checkbox><Checkbox checked={!p.allowOcclusion} disabled={disabled} onChange={e => patch({ allowOcclusion: !e.target.checked })}>不允许遮挡</Checkbox></Space>}
    {node.kind === 'product_mirroring' && <p className="muted">对照正常方向的包装文字或非对称标识；方向无法辨认时待确认。</p>}
    {node.kind === 'exposure_duration' && <Space wrap style={{ marginTop: 10 }}><span>连续至少</span><InputNumber aria-label="最短连续露出秒数" min={0.01} max={600} value={p.minContinuousSeconds} disabled={disabled} addonAfter="秒" onChange={v => patch({ minContinuousSeconds: v ?? 1 })} /><span>累计至少</span><InputNumber aria-label="最短累计露出秒数" min={0.01} max={600} value={p.minTotalSeconds} disabled={disabled} addonAfter="秒" onChange={v => patch({ minTotalSeconds: v ?? 1 })} /></Space>}
    {node.kind === 'exposure_count' && <Space wrap style={{ marginTop: 10 }}><span>至少</span><InputNumber aria-label="最少露出次数" min={1} max={30} value={p.minOccurrences} disabled={disabled} addonAfter="次" onChange={v => patch({ minOccurrences: v ?? 1 })} /><span>两次之间至少间隔</span><InputNumber aria-label="露出计数间隔秒数" min={0} max={60} value={p.occurrenceGapSeconds} disabled={disabled} addonAfter="秒" onChange={v => patch({ occurrenceGapSeconds: v ?? 1 })} /></Space>}
    {['forbidden_expression', 'required_speech'].includes(node.kind) && <label className="component-field">{node.kind === 'required_speech' ? '每项必须口播内容' : '禁用词句（结合语境核对语义变体）'}<Input.TextArea key={`${node.id}-${node.kind}`} aria-label="组件检查词句" defaultValue={p.terms?.join('\n')} rows={3} maxLength={4000} disabled={disabled} placeholder="每行一项，最多20项" onChange={e => patch({ terms: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) })} /></label>}
    {node.kind === 'specified_action' && <label className="component-field">必须展示的动作<Input aria-label="指定动作描述" value={p.action} maxLength={500} disabled={disabled} placeholder="例如：实际饮用产品的完整过程" onChange={e => patch({ action: e.target.value })} /></label>}
    <details className="component-instruction"><summary>查看判定说明</summary><pre>{definition?.prompt.content}</pre></details>
  </div>;
}

export function VideoRuleBuilder({ projectId, rules, onChange, disabled = false, onUploadBusy }: { projectId: string; rules: StandardDraft[]; onChange: (rules: StandardDraft[]) => void; disabled?: boolean; onUploadBusy?: (busy: boolean) => void }) {
  const cache = useQueryClient();
  const catalog = useQuery({ queryKey: ['video-components'], queryFn: videoComponentsApi.catalog });
  const refs = useQuery({ queryKey: ['video-references', projectId], queryFn: () => videoComponentsApi.references(projectId) });
  const [uploading, setUploading] = useState(false), [error, setError] = useState<string>();
  const [openEditor, setOpenEditor] = useState<string>();
  const blocked = disabled || uploading;
  const count = rules.reduce((n, r) => n + (r.componentPlan?.nodes.length || 0), 0);
  const update = (index: number, value: Partial<StandardDraft>) => onChange(rules.map((r, i) => i === index ? { ...r, ...value } : r));
  async function upload(file?: File) {
    if (!file || blocked) return;
    if (file.size > 4 * 1024 * 1024) { setError('每张参考图须在4MiB以内'); return; }
    setUploading(true); onUploadBusy?.(true); setError(undefined);
    try { await videoComponentsApi.upload(projectId, file, crypto.randomUUID()); await cache.invalidateQueries({ queryKey: ['video-references', projectId] }); }
    catch (e) { setError(e instanceof Error ? e.message : '参考图上传失败'); }
    finally { setUploading(false); onUploadBusy?.(false); }
  }
  function append(kind: ComponentKind, text: string, endSeconds?: number) {
    const node = newNode(kind); if (endSeconds) node.parameters.endSeconds = endSeconds;
    onChange([...rules, { text, criterion: '按下方品牌确认的检查条件执行；看不清或无法判断时待确认。', category: '工具组合', importance: 'must', origin: 'manual', sourceBlockId: '', sourceQuote: '', enabled: true, componentPlan: { operator: 'all', sameSegment: false, nodes: [node] } }]);
  }
  return <div className="video-rule-builder">
    <p className="muted">每条规则一行，检查组件显示在规则后。点击组件可设置参考图、时间范围和检查条件。</p>
    <details className="video-reference-materials"><summary>品牌参考素材 · {refs.data?.data.length || 0} 张</summary><div className="video-reference-body">
      <label className="reference-upload">{uploading ? '正在上传…' : '上传产品或商标图片'}<input aria-label="上传品牌参考图" type="file" accept=".png,.jpg,.jpeg,.webp" disabled={blocked} onChange={e => { void upload(e.target.files?.[0]); e.target.value = ''; }} /></label>
      <small className="muted">参考图不产生模型费用。单张≤4MiB；每次审核最多8张、合计≤8MiB。</small>
      {!!refs.data?.data.length && <div className="reference-gallery"><Image.PreviewGroup>{refs.data.data.map(ref => <div className="reference-thumb" key={ref.id}><Image src={referenceImageUrl(projectId, ref.id)} width={66} height={60} style={{ objectFit: 'contain' }} alt={ref.name} /><span title={ref.name}>{ref.name}</span></div>)}</Image.PreviewGroup></div>}
      {refs.isError && <Alert type="error" title="参考图列表读取失败" action={<Button onClick={() => void refs.refetch()}>重试</Button>} />}
    </div></details>
    <Space wrap className="component-presets"><span>推荐规则</span><Button disabled={blocked || count >= 8} onClick={() => append('product_presence', '前15秒完整露出产品', 15)}>前15秒产品露出</Button><Button disabled={blocked || count >= 8} onClick={() => append('product_mirroring', '产品不得镜像')}>产品不得镜像</Button><Button disabled={blocked || count >= 8} onClick={() => append('forbidden_expression', '不使用品牌禁用表达')}>禁用表达</Button><Tag>{count} / 8 个组件</Tag></Space>
    {!rules.length && <Empty description="添加第一条视频规则" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    <div className="video-rule-table">
    <div className="video-rule-columns" aria-hidden="true"><span>序号</span><span>视频要求</span><span>判定说明</span><span>检查组件</span><span>重要性</span><span>操作</span></div>
    {rules.map((rule, index) => {
      const plan = rule.componentPlan;
      const setPlan = (value: ComponentPlan) => update(index, { componentPlan: value });
      const addNode = () => { const node = newNode('product_presence'); setPlan(plan ? { ...plan, nodes: [...plan.nodes, node] } : { operator: 'all', sameSegment: false, nodes: [node] }); setOpenEditor(node.id); };
      return <div className={`video-rule-row ${rule.importance}`} key={index}>
        <span className="video-rule-number">{String(index + 1).padStart(2, '0')}</span>
        <Input aria-label={`视频第${index + 1}条要求`} value={rule.text} maxLength={2000} disabled={blocked} onChange={e => update(index, { text: e.target.value })} />
        <Input.TextArea className="video-rule-criterion" aria-label={`视频第${index + 1}条说明`} value={rule.criterion} autoSize={{ minRows: 1, maxRows: 3 }} maxLength={20000} disabled={blocked} onChange={e => update(index, { criterion: e.target.value })} />
        <div className="video-rule-components">
          {plan?.nodes.map(node => {
            const label = catalog.data?.data.find(d => d.kind === node.kind)?.name || '检查组件';
            const invalid = (needsReference(node.kind) && !node.parameters.referenceImageIds?.length) || (['forbidden_expression', 'required_speech'].includes(node.kind) && !node.parameters.terms?.length) || (node.kind === 'specified_action' && !node.parameters.action?.trim());
            return <Popover key={node.id} destroyOnHidden trigger="click" placement="bottomRight" open={openEditor === node.id} onOpenChange={open => setOpenEditor(open ? node.id : undefined)} content={<div className="video-component-popover"><strong>第 {index + 1} 条 · {label}</strong><NodeEditor node={node} definitions={catalog.data?.data || []} references={refs.data?.data || []} disabled={blocked} onChange={changed => setPlan({ ...plan, sameSegment: plan.sameSegment && visual.has(changed.kind), nodes: plan.nodes.map(n => n.id === node.id ? changed : n) })} onRemove={() => { setOpenEditor(undefined); if (plan.nodes.length === 1) update(index, { componentPlan: null }); else setPlan({ ...plan, nodes: plan.nodes.filter(n => n.id !== node.id) }); }} /><Button size="small" block onClick={() => setOpenEditor(undefined)}>完成设置</Button></div>}><Button size="small" className={`video-component-chip${invalid ? ' is-incomplete' : ''}`} aria-label={`编辑第${index + 1}条${label}`} title={invalid ? '请补全组件条件' : '点击编辑检查条件'}>{label.replace('检查', '')}{invalid && <span aria-hidden="true"> · 待补全</span>}</Button></Popover>;
          })}
          {plan && plan.nodes.length > 1 && <Popover destroyOnHidden trigger="click" placement="bottomRight" content={<div className="video-component-popover"><strong>第 {index + 1} 条 · 组件组合</strong><Space wrap className="component-combination"><span>组合方式</span><Select aria-label="组件组合方式" value={plan.operator} disabled={blocked} onChange={operator => setPlan({ ...plan, operator, sameSegment: false })} options={[{ value: 'all', label: '全部满足 AND' }, { value: 'any', label: '任一满足 OR' }]} />{plan.operator === 'all' && plan.nodes.every(n => visual.has(n.kind)) && <Space><Switch aria-label="同一段画面同时满足" checked={plan.sameSegment} disabled={blocked} onChange={sameSegment => setPlan({ ...plan, sameSegment })} /><span>同一段画面同时满足</span></Space>}</Space></div>}><Button size="small" type="text" aria-label={`第${index + 1}条组件组合`}>{plan.operator === 'all' ? 'AND' : 'OR'}{plan.sameSegment ? ' · 同画面' : ''}</Button></Popover>}
          <Button size="small" type="dashed" aria-label={plan ? '添加共同检查组件' : '添加检查组件'} disabled={blocked || count >= 8 || catalog.isPending || catalog.isError} onClick={addNode}>＋组件</Button>
        </div>
        <Select size="small" aria-label={`视频第${index + 1}条重要性`} disabled={blocked} value={rule.importance} onChange={importance => update(index, { importance })} options={[{ value: 'must', label: '必须满足' }, { value: 'suggested', label: '建议优化' }]} />
        <Button size="small" type="text" danger aria-label={`删除视频第${index + 1}条规则`} disabled={blocked || rules.length === 1} onClick={() => { setOpenEditor(undefined); onChange(rules.filter((_, i) => i !== index)); }}>删除</Button>
      </div>;
    })}
    </div>
    <Button disabled={blocked || count >= 8 || rules.length >= 60} onClick={() => append('product_presence', '产品露出检查')}>添加视频规则</Button>
    {catalog.isError && <Alert type="error" title="检查组件读取失败" action={<Button onClick={() => void catalog.refetch()}>重试</Button>} />}
    {(error || videoRulesError(rules)) && <Alert style={{ marginTop: 12 }} type={error ? 'error' : 'info'} title={error || videoRulesError(rules)} />}
  </div>;
}
