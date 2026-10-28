import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Drawer, Empty, Input, InputNumber, Modal, Popover, Radio, Select, Space, Switch, Tag } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { videoComponentsApi, type ComponentKind, type ComponentNode, type ComponentParameters } from './videoComponentsApi';
import type { StandardDraft } from './standardsApi';
import { readableBaselineRule, reviewDepth, thresholdText, type VideoReviewLevel } from './videoReviewSettings';
import { VideoForbiddenPreset } from './VideoForbiddenPreset';
import { VideoProductPresets } from './VideoProductPresets';
import { videoRuleCategories, videoRuleCategory } from './videoRuleCategories';
import './video-layers.css';

export type VideoLayer = 'baseline' | 'brief' | 'precise';
export const videoLayer = (rule: StandardDraft): VideoLayer => rule.layer || (rule.componentPlan ? 'precise' : 'brief');
export const LAYER_ORDER: VideoLayer[] = ['baseline', 'brief', 'precise'];
const visual = new Set<ComponentKind>(['product_presence', 'logo_presence', 'product_mirroring', 'exposure_duration', 'exposure_count', 'specified_action']);
const needsReference = (kind: ComponentKind) => visual.has(kind) && kind !== 'specified_action';
export function videoLayersError(rules: StandardDraft[], level: VideoReviewLevel = 'strict') {
  const selected = (rule: StandardDraft) => rule.enabled !== false && LAYER_ORDER.indexOf(videoLayer(rule)) < reviewDepth[level];
  if (!rules.some(selected)) return '至少启用一条视频规则';
  if (rules.some(rule => !rule.text.trim() || !rule.criterion.trim())) return '请填写每条规则的要求与判据';
  if (rules.some(rule => selected(rule) && videoLayer(rule) === 'precise' && !rule.componentPlan?.nodes.length)) return '量化检查启用前请补齐并确认组件';
  if (rules.filter(selected).reduce((n, rule) => n + (rule.componentPlan?.nodes.length || 0), 0) > 8) return '每次审核最多启用8个检查组件';
}
const missing = (rule: StandardDraft) => ['product', 'identity'].includes(rule.baselineKey || '') ? !rule.params?.names?.length : rule.baselineKey === 'forbidden' ? !rule.params?.terms?.length : false;

export function VideoLayersEditor({ projectId, rules, defaults, reviewLevel, onReviewLevelChange, onChange, disabled, onUploadBusy }: {
  projectId: string; rules: StandardDraft[]; defaults: StandardDraft[]; reviewLevel: VideoReviewLevel; onReviewLevelChange: (level: VideoReviewLevel) => void; onChange: (rules: StandardDraft[]) => void;
  disabled: boolean; onUploadBusy?: (busy: boolean) => void;
}) {
  const cache = useQueryClient();
  const catalog = useQuery({ queryKey: ['video-components'], queryFn: videoComponentsApi.catalog });
  const refs = useQuery({ queryKey: ['video-references', projectId], queryFn: () => videoComponentsApi.references(projectId) });
  const [uploading, setUploading] = useState(false), [error, setError] = useState<string>();
  const [search, setSearch] = useState(''), [importance, setImportance] = useState('all');
  const [termsText, setTermsText] = useState('');
  const [detailIndex, setDetailIndex] = useState<number>();
  const [factsText, setFactsText] = useState<{ index: number; text: string }>();
  const [editor, setEditor] = useState<{ index: number; node: ComponentNode; existing?: string }>();
  const blocked = disabled || uploading;
  const depth = reviewDepth[reviewLevel];
  const inScope = (rule: StandardDraft) => LAYER_ORDER.indexOf(videoLayer(rule)) < depth;
  const update = (index: number, patch: Partial<StandardDraft>) => onChange(rules.map((rule, n) => n === index ? readableBaselineRule({ ...rule, layer: videoLayer(rule), ...patch, ...(patch.text !== undefined || patch.criterion !== undefined || patch.params !== undefined || patch.componentPlan !== undefined ? { origin: 'manual' as const } : {}) }, rule) : rule));
  function openDetails(index: number) {
    const rule = rules[index];
    setFactsText({ index, text: (['forbidden', 'information_accuracy'].includes(rule.baselineKey || '') ? rule.params?.terms : rule.params?.names)?.join('\n') || '' });
    setDetailIndex(index);
  }
  const allNodes = rules.reduce((n, rule) => n + (rule.componentPlan?.nodes.length || 0), 0);
  const hiddenCount = rules.filter(rule => !inScope(rule)).length;
  const visible = rules.map((rule, index) => ({ rule, index }))
    .filter(({ rule }) => inScope(rule) && !rule.presetKey)
    .filter(({ rule }) => (importance === 'all' || rule.importance === importance) && (rule.text + rule.criterion).toLowerCase().includes(search.trim().toLowerCase()));
  const move = (index: number, delta: number) => {
    const peers = visible.filter(item => videoRuleCategory(item.rule) === videoRuleCategory(rules[index]));
    const target = peers[peers.findIndex(item => item.index === index) + delta]?.index;
    if (target == null) return;
    const next = [...rules];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  function add(layer: VideoLayer) {
    setDetailIndex(rules.length);
    onChange([...rules, { text: '', criterion: '', layer, enabled: false, importance: layer === 'brief' ? 'suggested' : 'must', category: layer === 'brief' ? '文字要求' : '量化检查', origin: 'manual', sourceBlockId: '', sourceQuote: '' }]);
  }
  function candidateNode(index: number, kind?: ComponentKind) {
    const rule = rules[index], thresholds = Object.fromEntries((rule.params?.thresholds || []).map(t => [t.key, t.value]));
    const selected = kind || (thresholds.minOccurrences != null ? 'exposure_count' : 'exposure_duration');
    const node: ComponentNode = { id: crypto.randomUUID(), kind: selected, parameters: {
      referenceImageIds: [], startSeconds: thresholds.startSeconds ?? 0, endSeconds: thresholds.endSeconds ?? null,
      minContinuousSeconds: thresholds.minContinuousSeconds ?? null, minTotalSeconds: thresholds.minTotalSeconds ?? null,
      minOccurrences: thresholds.minOccurrences ?? null, occurrenceGapSeconds: thresholds.occurrenceGapSeconds ?? 0,
      requireWholeProduct: false, requireClearLogo: selected === 'logo_presence', allowOcclusion: false,
      terms: [], action: '',
    } };
    return node;
  }
  function openNode(index: number, node?: ComponentNode) { setTermsText((node?.parameters.terms || []).join('\n')); setEditor({ index, node: node ? structuredClone(node) : candidateNode(index), existing: node?.id }); }
  function nodeError(node: ComponentNode) {
    const p = node.parameters;
    if (needsReference(node.kind) && !p.referenceImageIds?.length) return '请选择品牌参考图';
    if (p.endSeconds != null && p.endSeconds <= p.startSeconds) return '结束时间须大于开始时间';
    if (node.kind === 'exposure_duration' && p.minContinuousSeconds == null && p.minTotalSeconds == null) return '请填写至少一项明确时长，不自动补入1秒';
    if (node.kind === 'exposure_count' && p.minOccurrences == null) return '请填写明确次数，不自动补入1次';
    if (node.kind === 'exposure_count' && p.minOccurrences != null && (!Number.isInteger(p.minOccurrences) || p.minOccurrences < 1 || p.minOccurrences > 30)) return '次数须为1至30之间的整数';
    if (node.kind === 'exposure_duration' && [p.minContinuousSeconds, p.minTotalSeconds].some(v => v != null && (v <= 0 || v > 600))) return '时长须大于0且不超过600秒';
    if (['required_speech', 'forbidden_expression'].includes(node.kind) && !p.terms?.length) return '请填写检查词句';
    if (node.kind === 'specified_action' && !p.action.trim()) return '请填写动作';
  }
  function commitNode() {
    if (!editor || nodeError(editor.node)) return;
    const rule = rules[editor.index], old = rule.componentPlan;
    const nodes = editor.existing ? old!.nodes.map(n => n.id === editor.existing ? editor.node : n) : [...(old?.nodes || []), editor.node];
    update(editor.index, { componentPlan: { operator: old?.operator || 'all', sameSegment: !!old?.sameSegment && nodes.every(n => visual.has(n.kind)), nodes } });
    setEditor(undefined);
  }
  async function upload(file?: File) {
    if (!file || blocked) return;
    setUploading(true); onUploadBusy?.(true); setError(undefined);
    try { await videoComponentsApi.upload(projectId, file, crypto.randomUUID()); await cache.invalidateQueries({ queryKey: ['video-references', projectId] }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '参考图上传失败'); }
    finally { setUploading(false); onUploadBusy?.(false); }
  }
  const enabledInScope = rules.filter(rule => inScope(rule) && rule.enabled !== false);
  const presetMatches = (key: string, title: string) => {
    const rule = rules.find(rule => rule.presetKey === key);
    return (importance === 'all' || (rule?.importance || 'must') === importance)
      && `${title} ${rule?.criterion || ''}`.toLowerCase().includes(search.trim().toLowerCase());
  };
  const hasPresetMatch = depth >= reviewDepth.strict && [
    ['forbidden_expression', '不使用禁用表达'], ['product_count', '产品出现次数检查'], ['product_duration', '产品出现时间检查'],
  ].some(([key, title]) => presetMatches(key, title));
  return <div className="video-layers-editor">
    <div className="video-review-level"><strong>审查规则</strong><Radio.Group aria-label="视频审查档位" optionType="button" buttonStyle="solid" value={reviewLevel} disabled={blocked} onChange={event => onReviewLevelChange(event.target.value)} options={[{ value: 'basic', label: '基础版' }, { value: 'custom', label: '进阶版' }, { value: 'strict', label: '严格版' }]} /></div>
    {rules.some(rule => !rule.layer) && <Alert type="info" title="旧规则已按现有内容继续展示，原有条件与重要性保留；保存新版本后切换为新格式" />}
    <Button disabled={blocked} onClick={() => Modal.confirm({ title:'替换为通用视频模板？', content:'当前视频规则草稿将替换为六条通用检查，不再要求逐条讲出 Brief 产品资料。旧版本和历史报告保留，保存项目规则后生效。', okText:'采用通用模板', cancelText:'取消', onOk:() => { setDetailIndex(undefined); onChange(defaults.map(rule => structuredClone(rule))); onReviewLevelChange('basic'); } })}>替换为通用视频模板</Button>

    <div className="rule-list-toolbar">
      <Space wrap><strong>共 {rules.length} 条</strong><span className="muted">当前档位审核 {enabledInScope.length} 条 · 必须 {enabledInScope.filter(r => r.importance === 'must').length} · 建议 {enabledInScope.filter(r => r.importance === 'suggested').length}</span></Space>
      <Space wrap><Input aria-label="视频搜索规则" prefix={<SearchOutlined />} placeholder="搜索要求或判定说明" allowClear value={search} onChange={event => setSearch(event.target.value)} /><Select aria-label="视频筛选规则重要性" value={importance} onChange={setImportance} options={[{ value: 'all', label: '全部规则' }, { value: 'must', label: '必须满足' }, { value: 'suggested', label: '建议优化' }]} style={{ minWidth: 120 }} /></Space>
    </div>
    <div className="video-rule-groups">
      {videoRuleCategories.map(group => {
        const entries = visible.filter(item => videoRuleCategory(item.rule) === group.key);
        const strict = depth >= reviewDepth.strict;
        const showForbidden = strict && group.key === 'forbidden' && presetMatches('forbidden_expression', '不使用禁用表达');
        const productKeys = [['product_count', '产品出现次数检查'], ['product_duration', '产品出现时间检查']].filter(([key, title]) => presetMatches(key, title)).map(([key]) => key);
        const showProducts = strict && group.key === 'exposure' && productKeys.length > 0;
        if (!entries.length && !showForbidden && !showProducts) return null;
        const count = entries.length + (showForbidden ? 1 : 0) + (showProducts ? productKeys.length : 0);
        return <section className={`video-rule-group group-${group.key}${count > 6 ? ' is-wide' : ''}`} aria-label={group.label} key={group.key}>
          <header><div><strong>{group.label}</strong></div><span>{count} 条</span></header>
          <div className="video-rule-group-body">
          {entries.map(({ rule, index }, position) => <article className={`video-layer-row ${rule.importance}${rule.enabled === false ? ' is-disabled' : ''}`} key={index} aria-label={`视频第${index + 1}条规则`}>
            <div className="video-row-leading"><Switch size="small" aria-label={`启用视频第${index + 1}条`} checked={rule.enabled !== false} disabled={blocked || (videoLayer(rule) === 'precise' && !rule.componentPlan?.nodes.length)} onChange={enabled => update(index, { enabled })} /><small>{String(index + 1).padStart(2, '0')}</small></div>
            <div className="video-rule-main"><Input aria-label={`视频第${index + 1}条要求`} title={rule.text} value={rule.text} disabled={blocked} maxLength={2000} placeholder="填写规则名称" onChange={event => update(index, { text: event.target.value })} /></div>
            <Select size="small" aria-label={`视频第${index + 1}条重要性`} value={rule.importance} disabled={blocked} options={[{ value: 'must', label: '必须' }, { value: 'suggested', label: '建议' }]} onChange={value => update(index, { importance: value })} />
            <Space size={0}><Button size="small" type="text" onClick={() => openDetails(index)} aria-label={`查看视频第${index + 1}条详情`}>{missing(rule) || (videoLayer(rule) === 'precise' && !rule.componentPlan) ? '待配置' : '详情'}</Button><Popover trigger="click" content={<Space><Button size="small" aria-label={`视频上移第${index + 1}条`} disabled={blocked || position === 0} onClick={() => move(index, -1)}>上移</Button><Button size="small" danger aria-label={`删除视频第${index + 1}条`} disabled={blocked} onClick={() => { setDetailIndex(undefined); onChange(rules.filter((_, n) => n !== index)); }}>删除</Button></Space>}><Button type="text" size="small" aria-label={`视频第${index + 1}条更多操作`}>···</Button></Popover></Space>
          </article>)}
          {showForbidden && <VideoForbiddenPreset rules={rules} onChange={onChange} disabled={blocked} />}
          {showProducts && <VideoProductPresets rules={rules} onChange={onChange} references={refs.data?.data || []} disabled={blocked} visibleKeys={productKeys} />}
          </div>
        </section>;
      })}
    </div>

    {depth >= reviewDepth.strict && <>
      <label className="video-preset-upload">上传产品参考图 <input aria-label="默认产品检查上传参考图" type="file" accept=".png,.jpg,.jpeg,.webp" disabled={blocked} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} /></label>
    </>}

    {!visible.length && !hasPresetMatch && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={rules.length ? '没有匹配的规则' : '暂未配置规则'} />}
    <Space wrap className="video-layer-add">
      {depth >= reviewDepth.custom && <Button size="small" disabled={blocked || rules.length >= 60} onClick={() => add('brief')}>添加文字规则</Button>}
      {depth >= reviewDepth.strict && <Button size="small" disabled={blocked || rules.length >= 60} onClick={() => add('precise')}>添加量化检查</Button>}
    </Space>
    {hiddenCount > 0 && <p className="muted">另有 {hiddenCount} 条更高档位的规则已保留，切到更高档位可继续编辑；当前档位审核不使用。</p>}

    <Drawer title={detailIndex == null ? '规则详情' : `第 ${detailIndex + 1} 条 · 规则详情`} open={detailIndex != null} onClose={() => setDetailIndex(undefined)} size={520} mask={false}>
      {detailIndex != null && rules[detailIndex] && (() => {
        const index = detailIndex, rule = rules[index];
        return <div className="video-rule-detail">
          <label>规则名称<Input.TextArea aria-label={`编辑视频第${index + 1}条名称`} value={rule.text} disabled={blocked} maxLength={2000} autoSize={{ minRows: 2, maxRows: 4 }} onChange={event => update(index, { text: event.target.value })} /></label>
          <label>分类<Select aria-label={`视频第${index + 1}条分类`} value={videoRuleCategory(rule)} disabled={blocked} options={videoRuleCategories.map(group => ({ value: group.key, label: group.label }))} onChange={key => update(index, { category: videoRuleCategories.find(group => group.key === key)!.label })} /></label>
          <label>判定说明</label>
      <div className="video-rule-criterion"><Input.TextArea aria-label={`视频第${index + 1}条判定说明`} value={rule.criterion} autoSize={{ minRows: 1, maxRows: 4 }} maxLength={20000} placeholder="详细要求与判定依据" disabled={blocked} onChange={event => update(index, { criterion: event.target.value })} />{rule.sourceQuote && <details className="rule-source-quote"><summary>查看 Brief 原文</summary><blockquote>{rule.sourceQuote}</blockquote></details>}</div>
          <label>检查资料与参数</label>
        <div className="video-layer-conditions">
          {videoLayer(rule) === 'baseline' && rule.baselineKey && !['script_match', 'understandability'].includes(rule.baselineKey) && <div className="video-layer-facts"><strong>{rule.baselineKey === 'competitor' ? '指定竞品（可选，留空由模型判断）' : rule.baselineKey === 'product' ? '确认的产品名称' : rule.baselineKey === 'identity' ? '品牌、产品名称与认可别名' : rule.baselineKey === 'information_accuracy' ? '已确认产品事实（仅核对，不要求必提）' : '确认的禁用词句'}</strong><Input.TextArea aria-label={`视频第${index + 1}条基础资料`} value={factsText?.index === index ? factsText.text : (['forbidden', 'information_accuracy'].includes(rule.baselineKey || '') ? rule.params?.terms : rule.params?.names)?.join('\n') || ''} disabled={blocked} rows={4} placeholder={rule.baselineKey === 'competitor' ? '每行一个竞品；不填写时由模型结合品牌与产品品类判断' : '每行一项；不适用时可关闭该检查'} onChange={event => { setFactsText({ index, text: event.target.value }); update(index, { params: { names: rule.params?.names || [], terms: rule.params?.terms || [], thresholds: rule.params?.thresholds || [], [['forbidden', 'information_accuracy'].includes(rule.baselineKey || '') ? 'terms' : 'names']: event.target.value.split('\n').map(v => v.trim()).filter(Boolean) } }); }} /></div>}
          {videoLayer(rule) === 'baseline' && rule.baselineKey === 'script_match' && <Tag>使用品牌已通过脚本</Tag>}
          {rule.sourceQuote && <Tag>来自 Brief</Tag>}
          {videoLayer(rule) === 'precise' && <>
            {rule.params?.thresholds?.map(t => <Tag key={t.key}>{thresholdText(t.key, t.value)}</Tag>)}
            {rule.componentPlan?.nodes.map(node => <Button size="small" key={node.id} aria-label={`编辑视频第${index + 1}条${node.kind}`} onClick={() => openNode(index, node)}>{catalog.data?.data.find(d => d.kind === node.kind)?.name || node.kind}{node.kind === 'exposure_duration' ? ` · ${node.parameters.minContinuousSeconds != null ? '连续' + node.parameters.minContinuousSeconds + '秒 ' : ''}${node.parameters.minTotalSeconds != null ? '累计' + node.parameters.minTotalSeconds + '秒' : ''}` : node.kind === 'exposure_count' ? ` · ${node.parameters.minOccurrences}次` : ''}</Button>)}
            <Button size="small" disabled={blocked || allNodes >= 8 || catalog.isPending || catalog.isError} onClick={() => openNode(index)}>配置检查组件</Button>
            {rule.componentPlan && rule.componentPlan.nodes.length > 1 && <><Select size="small" aria-label={`第${index + 1}条组件组合`} value={rule.componentPlan.operator} disabled={blocked} options={[{ value: 'all', label: '全部满足 AND' }, { value: 'any', label: '任一满足 OR' }]} onChange={operator => update(index, { componentPlan: { ...rule.componentPlan!, operator, sameSegment: false } })} />{rule.componentPlan.operator === 'all' && rule.componentPlan.nodes.every(node => visual.has(node.kind)) && <Checkbox checked={rule.componentPlan.sameSegment} disabled={blocked} onChange={event => update(index, { componentPlan: { ...rule.componentPlan!, sameSegment: event.target.checked } })}>同一画面同时满足</Checkbox>}</>}
          </>}
        </div>
        </div>;
      })()}
    </Drawer>
    <Modal title="确认量化检查参数" open={!!editor} onCancel={() => { if (!uploading) setEditor(undefined); }} onOk={commitNode} okText="确认组件参数" okButtonProps={{ disabled: !editor || blocked || !!nodeError(editor.node) }} width={660} zIndex={1200}>
      {editor && (() => {
        const node = editor.node, p = node.parameters;
        const patch = (value: Partial<ComponentParameters>) => setEditor({ ...editor, node: { ...node, parameters: { ...p, ...value } } });
        return <div className="video-layer-node-editor"><Select aria-label="检查组件类型" value={node.kind} options={catalog.data?.data.map(definition => ({ value: definition.kind, label: definition.name }))} onChange={kind => { setTermsText(''); setEditor({ ...editor, node: { ...candidateNode(editor.index, kind), id: node.id } }); }} />
          {needsReference(node.kind) && <><Select aria-label="组件参考图" mode="multiple" maxCount={4} placeholder="选择品牌参考图" value={p.referenceImageIds} options={refs.data?.data.map(ref => ({ value: ref.id, label: ref.name }))} onChange={referenceImageIds => patch({ referenceImageIds })} /><label>上传参考图（不调用模型）<input aria-label="上传品牌参考图" type="file" accept=".png,.jpg,.jpeg,.webp" disabled={blocked} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} /></label></>}
          <Space wrap><span>范围</span><InputNumber aria-label="检查开始秒数" min={0} max={599} value={p.startSeconds} onChange={v => patch({ startSeconds: v ?? 0 })} /><span>至</span><InputNumber aria-label="检查结束秒数" min={0.01} max={600} value={p.endSeconds} placeholder="视频结尾" onChange={v => patch({ endSeconds: v })} /><span>秒</span></Space>
          {node.kind === 'exposure_duration' && <Space wrap><span>连续至少</span><InputNumber aria-label="最短连续露出秒数" min={0.01} max={600} value={p.minContinuousSeconds} placeholder="未要求" onChange={v => patch({ minContinuousSeconds: v })} /><span>累计至少</span><InputNumber aria-label="最短累计露出秒数" min={0.01} max={600} value={p.minTotalSeconds} placeholder="未要求" onChange={v => patch({ minTotalSeconds: v })} /></Space>}
          {node.kind === 'exposure_count' && <Space wrap><span>至少出现</span><InputNumber aria-label="最少露出次数" min={1} max={30} value={p.minOccurrences} placeholder="请确认次数" onChange={v => patch({ minOccurrences: v })} /><span>次；合并间隔</span><InputNumber aria-label="露出计数间隔秒数" min={0} max={60} value={p.occurrenceGapSeconds} onChange={v => patch({ occurrenceGapSeconds: v ?? 0 })} /></Space>}
          {visual.has(node.kind) && node.kind !== 'specified_action' && <Space wrap><Checkbox checked={p.requireWholeProduct} onChange={event => patch({ requireWholeProduct: event.target.checked })}>完整入画</Checkbox><Checkbox checked={p.requireClearLogo || node.kind === 'logo_presence'} disabled={node.kind === 'logo_presence'} onChange={event => patch({ requireClearLogo: event.target.checked })}>Logo可辨认</Checkbox><Checkbox checked={!p.allowOcclusion} onChange={event => patch({ allowOcclusion: !event.target.checked })}>不允许遮挡</Checkbox></Space>}
          {['required_speech', 'forbidden_expression'].includes(node.kind) && <Input.TextArea aria-label="组件检查词句" placeholder="每行一项" value={termsText} onChange={event => { setTermsText(event.target.value); patch({ terms: event.target.value.split('\n').map(v => v.trim()).filter(Boolean) }); }} />}
          {node.kind === 'specified_action' && <Input aria-label="指定动作描述" placeholder="必须展示的动作" value={p.action} onChange={event => patch({ action: event.target.value })} />}
          {nodeError(node) && <Alert type="info" title={nodeError(node)} />}
          {editor.existing && <Button danger onClick={() => { const plan = rules[editor.index].componentPlan!; const nodes = plan.nodes.filter(n => n.id !== editor.existing); update(editor.index, { componentPlan: nodes.length ? { ...plan, nodes } : null, enabled: nodes.length ? rules[editor.index].enabled : false }); setEditor(undefined); }}>移除组件</Button>}
        </div>;
      })()}
    </Modal>
    {error && <Alert type="error" title={error} />}
    {(catalog.isError || refs.isError) && <Alert type="error" title="组件或参考素材读取失败" action={<Button onClick={() => { void catalog.refetch(); void refs.refetch(); }}>重试</Button>} />}
  </div>;
}
