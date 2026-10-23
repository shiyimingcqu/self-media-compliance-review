import { useState } from 'react';
import { Button, Empty, Input, Select, Space } from 'antd';
import { ArrowUpOutlined, DeleteOutlined, SearchOutlined } from '@ant-design/icons';
import type { StandardDraft } from './standardsApi';
import { ScriptRuleTable } from './ScriptRuleTable';

export function TextRuleEditor({ rules, onChange, disabled, scope = 'script' }: { rules: StandardDraft[]; onChange: (rules: StandardDraft[]) => void; disabled: boolean; scope?: 'script' | 'image' | 'video' }) {
  const [search, setSearch] = useState(''), [importance, setImportance] = useState('all');
  const prefix = scope === 'image' ? '图文' : scope === 'video' ? '视频' : '';
  const update = (i: number, value: Partial<StandardDraft>) => onChange(rules.map((r, n) => n === i ? { ...r, ...value, origin: 'manual' } : r));
  const visible = rules.map((rule, index) => ({ rule, index })).filter(({ rule }) => (importance === 'all' || rule.importance === importance) && (rule.text + rule.criterion).toLowerCase().includes(search.trim().toLowerCase()));
  if (scope === 'script') return <ScriptRuleTable rules={rules} onChange={onChange} disabled={disabled} />;
  return <div className="text-rule-editor">
    <div className="rule-list-toolbar"><Space wrap><strong>共 {rules.length} 条</strong><span className="muted">必须 {rules.filter(r => r.importance === 'must').length} · 建议 {rules.filter(r => r.importance === 'suggested').length}</span></Space><Space wrap><Input aria-label={`${prefix}搜索规则`} prefix={<SearchOutlined />} placeholder="搜索要求或判定说明" allowClear value={search} onChange={e => setSearch(e.target.value)} /><Select aria-label={`${prefix}筛选规则重要性`} value={importance} onChange={setImportance} options={[{ value: 'all', label: '全部规则' }, { value: 'must', label: '必须满足' }, { value: 'suggested', label: '建议优化' }]} style={{ minWidth: 120 }} /></Space></div>
    <div className="compact-rule-columns" aria-hidden="true"><span>编号</span><span>要求</span><span>判定说明</span><span>重要性</span><span>操作</span></div>
    {visible.map(({ rule, index: i }) => <article className={`compact-rule-row ${rule.importance}`} key={i} aria-label={`${prefix}第${i + 1}条规则`}>
      <span className="compact-rule-number">{String(i + 1).padStart(2, '0')}</span>
      <Input.TextArea aria-label={`${prefix}第${i + 1}条要求`} value={rule.text} autoSize={{ minRows: 1, maxRows: 3 }} maxLength={2000} placeholder="写清需要满足的要求" disabled={disabled} onChange={e => update(i, { text: e.target.value })} />
      <div className="compact-rule-criterion"><Input.TextArea aria-label={`${prefix}第${i + 1}条判定说明`} value={rule.criterion} autoSize={{ minRows: 1, maxRows: 4 }} maxLength={20000} placeholder="详细要求与判定依据" disabled={disabled} onChange={e => update(i, { criterion: e.target.value })} />{rule.sourceQuote && <details className="rule-source-quote"><summary>查看 Brief 原文</summary><blockquote>{rule.sourceQuote}</blockquote></details>}</div>
      <Select aria-label={`${prefix}第${i + 1}条重要性`} value={rule.importance} disabled={disabled} onChange={value => update(i, { importance: value })} options={[{ value: 'must', label: '必须满足' }, { value: 'suggested', label: '建议优化' }]} />
      <Space size={0}><Button type="text" size="small" title="上移" aria-label={`${prefix}上移第${i + 1}条`} icon={<ArrowUpOutlined />} disabled={disabled || i === 0} onClick={() => onChange(rules.map((r, n) => n === i - 1 ? rules[i] : n === i ? rules[i - 1] : r))} /><Button type="text" size="small" danger title="删除" aria-label={`${prefix}删除第${i + 1}条`} icon={<DeleteOutlined />} disabled={disabled || rules.length === 1} onClick={() => onChange(rules.filter((_, n) => n !== i))} /></Space>
    </article>)}
    {!visible.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的规则" />}
  </div>;
}
