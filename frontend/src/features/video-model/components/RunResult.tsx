import { CreatorReviewResponse, RuleDisputeButton } from '../../workflow/ReviewDiscussion';
import { reasonSummary, secondsLabel } from '../reviewPresentation';
import { AiElapsed } from '../../../shared/ui/AiElapsed';
import { useEffect, useState } from 'react';
import { PipelineProgress } from './PipelineProgress';
import { Alert, Drawer, Space, Tag, Typography } from 'antd';
import { ArrowUpRight, MessageSquareWarning, ChevronDown } from 'lucide-react';
import { Button } from '../../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../../components/ui/tabs';
import type { EvidenceRun } from '../api';
import { artifactUrl } from '../api';
import { formatDate, formatVideoTime } from '../../../shared/lib/format';
const object = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const text = (v: unknown) => typeof v === 'string' ? v : '';
const list = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const layerNames: Record<string, string> = { baseline: '基础检查', brief: '文字要求', precise: '量化检查' };
export const runLabels: Record<string, string> = { queued: '等待调用', processing: '真实调用中', succeeded: '调用完成', failed: '调用失败', interrupted: '状态待核实' };
export const findingDomId = (runId: string, issueId: string) => `finding-${runId}-${issueId}`;
export function RunResult({ responseInSidebar = false, run, seek, onDisagree, selectedIssue, onFilterChange, onHoverFinding }: { responseInSidebar?: boolean; run: EvidenceRun; seek?: (ms: number, issueId?: string, endMs?: number) => void; onDisagree?: (note: string) => void; selectedIssue?: string; onFilterChange?: (filter: string) => void; onHoverFinding?: (issueId?: string) => void }) {
  const [filter, setFilter] = useState('failed'), [preview, setPreview] = useState<{ id: string; label: string; startMs: number }>();
  const result = object(run.result), report = object(result.report), artifacts = list(result.artifacts).map(object);
  const rules = list(object(run.snapshot).rules).map(object);
  const findings = list(report.findings).map((raw, index) => { const finding = object(raw), rule = rules.find(rule => rule.id === finding.rule_id); return { finding, rule, index, advisory: rule?.importance === 'suggested' || finding.verdict === 'not_applicable' }; });
  const normal = findings.filter(row => !row.advisory), advisory = findings.filter(row => row.advisory);
  const failed = normal.filter(row => row.finding.verdict === 'non_compliant');
  const uncertain = normal.filter(row => ['needs_confirmation', 'pending', 'unknown'].includes(text(row.finding.verdict)));
  const passed = normal.filter(row => row.finding.verdict === 'compliant');
  useEffect(() => { setFilter('failed'); setPreview(undefined); }, [run.id]);
  // 左侧时间轴跟随右侧分类：状态变化时把当前筛选同步出去。
  useEffect(() => { onFilterChange?.(filter); }, [filter, onFilterChange]);
  useEffect(() => {
    if (!selectedIssue) return;
    const finding = list(object(object(run.result).report).findings).map(object).find(row => row.id === selectedIssue);
    if (!finding) return;
    const rule = list(object(run.snapshot).rules).map(object).find(row => row.id === finding.rule_id);
    setFilter(rule?.importance === 'suggested' || finding.verdict === 'not_applicable' ? 'advisory' : finding.verdict === 'non_compliant' ? 'failed' : finding.verdict === 'compliant' ? 'passed' : 'uncertain');
  }, [selectedIssue, run.id, run.result, run.snapshot]);
  const visible = filter === 'failed' ? failed : filter === 'uncertain' ? uncertain : filter === 'passed' ? passed : filter === 'advisory' ? advisory : normal;
  function renderCard({ finding, rule, index, advisory: isAdvisory }: typeof findings[number]) {
    const ids = list(finding.evidence_ids).map(text), evidence = artifacts.filter(artifact => ids.includes(text(artifact.id)));
    const state = isAdvisory ? 'advisory' : finding.verdict === 'compliant' ? 'pass' : finding.verdict === 'non_compliant' ? 'fail' : 'uncertain';
    const labels = { advisory: '参考', pass: '通过', fail: '未通过', uncertain: '待确定' };
    const number = Number(finding.rule_number || rule?.ruleNumber) || index + 1;
    const confidence = typeof finding.confidence === 'number' && finding.confidence >= 0 && finding.confidence <= 1 ? finding.confidence : undefined;
    const first = evidence[0];
    return <article key={text(finding.id) || index} id={findingDomId(run.id, text(finding.id))} className={`finding-card desk-finding state-${state}${selectedIssue === finding.id ? ' is-focused' : ''}`} aria-label={`第${number}条${labels[state]}`} onMouseEnter={() => onHoverFinding?.(text(finding.id))} onMouseLeave={() => onHoverFinding?.(undefined)}>
      {state === 'uncertain' && <div className="finding-attention">证据或信息仍需人工核对</div>}
      <header><span className="rule-number">R{String(number).padStart(2, '0')}</span><span className={`desk-status state-${state}`}>{labels[state]}</span><span className="finding-layer">{layerNames[text(finding.layer) || text(rule?.layer)] || (rule?.componentPlan ? '量化检查' : '文字要求')}</span>{confidence != null ? <span className="confidence mono" aria-label={`置信度${Math.round(confidence * 100)}%`}><span className="confidence-segments">{Array.from({ length: 10 }, (_, n) => <i key={n} className={n < Math.round(confidence * 10) ? 'filled' : ''} />)}</span>{confidence.toFixed(2)}</span> : <span className="finding-confidence-empty">{isAdvisory ? '仅供参考' : '需品牌复核'}</span>}</header>
      <h3>{rule ? text(rule.text) : `第 ${number} 条审核发现`}</h3><p className="finding-reason-summary">{reasonSummary(text(finding.description))}</p>{text(finding.description).length > 110 && <details className="finding-reason-details"><summary>查看详细原因</summary><p>{text(finding.description)}</p></details>}
      {!!evidence.length && <div className="finding-evidence">{evidence.map(artifact => <div key={text(artifact.id)} className="evidence-artifact">{artifact.kind === 'image' ? <><button className="evidence-time mono" onClick={() => seek?.(Number(artifact.startMs) || 0, text(finding.id))}>{formatVideoTime(Number(artifact.startMs) || 0)}</button><button className="evidence-thumbnail" aria-label={`放大问题${number}截图证据`} onClick={() => setPreview({ id: text(artifact.id), label: `第 ${number} 条 · ${text(rule?.text)}`, startMs: Number(artifact.startMs) || 0 })}><img src={artifactUrl(run.id, text(artifact.id))} alt={`问题${number}截图证据`} /><span>截图证据</span></button></> : <Button variant="outline" size="sm" className="evidence-play-range" onClick={() => seek?.(Number(artifact.startMs) || 0, text(finding.id), Number(artifact.endMs) > Number(artifact.startMs) ? Number(artifact.endMs) : undefined)}>▷ {Number(artifact.endMs) > Number(artifact.startMs) ? `播放 ${secondsLabel(Number(artifact.startMs) || 0)}至 ${secondsLabel(Number(artifact.endMs))}` : `从 ${secondsLabel(Number(artifact.startMs) || 0)}播放`}</Button>}</div>)}</div>}
      {!ids.length && <div className="finding-no-evidence">{text(finding.evidence_note) || text(object(finding.evidence_basis).note) || (finding.observation_type === 'absence' ? '范围检查记录 · 请结合原视频核对' : '未附媒体证据 · 请结合说明核对原视频')}</div>}
      {!!list(result.componentResults).filter(raw => object(raw).ruleId === finding.rule_id).length && <details className="finding-calculation"><summary>查看工具计算与组合结果</summary>{list(result.componentResults).map(object).filter(row => row.ruleId === finding.rule_id).map(row => <div key={text(row.ruleId)}><p>{text(row.reason)}</p>{list(row.components).map(object).map(component => { const metrics = object(component.metrics); return <div key={text(component.componentId)}><span>{text(component.reason)}</span>{typeof metrics.totalMs === 'number' && <small className="mono">累计 {metrics.totalMs / 1000}s · 连续 {Number(metrics.longestContinuousMs || 0) / 1000}s</small>}{typeof metrics.count === 'number' && <small className="mono">有效出现 {metrics.count} 次</small>}</div>; })}</div>)}</details>}
      <footer><span>{isAdvisory ? '建议项 · 不阻断通过' : '必须项'}</span>{first && <Button variant="ghost" size="sm" onClick={() => seek?.(Number(first.startMs) || 0, text(finding.id))}>定位原视频<ArrowUpRight size={11} /></Button>}{onDisagree && <Button variant="ghost" size="sm" className="finding-disagree" onClick={() => onDisagree(`第 ${number} 条「${text(rule?.text) || text(finding.rule_id)}」AI 判定需调整：`)}><MessageSquareWarning size={12} />AI 判错了</Button>}</footer>
      {['non_compliant', 'needs_confirmation', 'pending'].includes(text(finding.verdict)) && <RuleDisputeButton ruleId={text(finding.rule_id)} />}
    </article>;
  }
  return <div className="desk-run-result"><div className="run-meta"><span className={`desk-status ${run.status === 'succeeded' ? 'state-pass' : run.status === 'failed' ? 'state-fail' : 'state-uncertain'}`}>{runLabels[run.status]}</span><span className="mono">配置 V{run.configRevision}</span><small>{formatDate(run.createdAt)}</small>{object(result.transport).mode === 'temporary_upload' && <Tag>文件引用</Tag>}<AiElapsed run={run} /></div>
    <PipelineProgress run={run} />
    {['queued', 'processing'].includes(run.status) && <><Alert type="info" title={run.snapshot.pipelineVersion === 'localized_refinement_v1' ? '正在整片定位与按片段精判' : '正在分析完整视频与按需取证'} description="可离开页面，返回后继续查看。" /><div className="evidence-loading" aria-label="正在加载审核证据">{[0,1,2].map(key => <div key={key}><i /><span /><span /></div>)}</div></>}
    {run.errorMessage && <Alert showIcon type="error" title={run.errorCode || '未完成'} description={run.errorMessage} />}
    {run.status === 'succeeded' && <>{run.kind === 'connection' ? <Alert type="info" title="文本连接可用，视频与音轨能力仍待验证" description={text(result.text)} /> : <>{list(object(result.ruleCoverage).unconfiguredRules).length > 0 && <Alert type="info" title="部分规则待品牌配置，不属于达人内容违规" description={list(object(result.ruleCoverage).unconfiguredRules).map(object).map(rule => <div key={text(rule.ruleId)}>第 {Number(rule.ruleNumber) || '?'} 条 · {text(rule.text)}：{text(rule.reason)}</div>)} />}
    {!responseInSidebar && <CreatorReviewResponse needsResponse={failed.length + uncertain.length > 0} />}
    <Tabs value={filter} onValueChange={setFilter}><TabsList aria-label="证据筛选">{[{ value: 'failed', label: '未通过', count: failed.length }, { value: 'uncertain', label: '待确定', count: uncertain.length }, { value: 'passed', label: '已通过', count: passed.length }, { value: 'all', label: '全部', count: findings.length }, { value: 'advisory', label: '仅参考', count: advisory.length }].map(tab => <TabsTrigger key={tab.value} value={tab.value}>{tab.label}<span className="mono">{tab.count}</span></TabsTrigger>)}</TabsList>
    <TabsContent value={filter} className="desk-findings-list">{visible.map(renderCard)}{filter === 'all' && !!advisory.length && <details className="advisory-findings"><summary><ChevronDown size={12} />仅供参考 <span className="mono">{advisory.length}</span> 条</summary>{advisory.map(renderCard)}</details>}{!(filter === 'all' ? findings.length : visible.length) && <div className="quiet-empty"><span>{filter === 'failed' ? '暂无未通过项' : '当前分类暂无审核结果'}</span><small>可切换其他分类查看判断和证据。</small></div>}</TabsContent></Tabs></>}</>}
    {run.status === 'failed' && artifacts.length > 0 && <section className="partial-evidence"><h3>已生成的取证（报告未完成，需人工核对）</h3>{artifacts.map(artifact => <div key={text(artifact.id)} className="evidence-artifact"><span className="mono">{formatVideoTime(Number(artifact.startMs) || 0)}</span>{artifact.kind === 'image' ? <button className="evidence-thumbnail" onClick={() => setPreview({ id: text(artifact.id), label: '未完成报告的取证截图', startMs: Number(artifact.startMs) || 0 })}><img src={artifactUrl(run.id, text(artifact.id))} alt="未完成报告的取证截图" /></button> : <video controls src={artifactUrl(run.id, text(artifact.id))} />}</div>)}</section>}
    {run.status === 'failed' && list(result.modelResponses).length > 0 && <details><summary>查看模型返回内容（排查报告格式）</summary>{list(result.modelResponses).map((raw, i) => <pre key={i} className="provider-diagnostic">{text(object(raw).response)}</pre>)}</details>}
    {!!list(result.providerRequestIds).length && <details className="provider-trace"><summary>调用诊断与用量</summary><Typography.Paragraph>请求 ID：{list(result.providerRequestIds).map(text).filter(Boolean).join(' / ')}<br />用量：{JSON.stringify(result.usage || [])}</Typography.Paragraph></details>}
    <Drawer title={preview?.label || '证据截图'} open={!!preview} onClose={() => setPreview(undefined)} size={440} mask={false}><div className="evidence-preview-drawer">{preview && <><span className="mono">{formatVideoTime(preview.startMs)}</span><img src={artifactUrl(run.id, preview.id)} alt={preview.label} /><Button variant="outline" onClick={() => seek?.(preview.startMs)}>定位原视频<ArrowUpRight size={13} /></Button></>}</div></Drawer>
  </div>;
}
