import { BrandReviewActions } from './BrandReviewActions';
import { AiElapsed, useAiCallTiming } from '../../shared/ui/AiElapsed';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Alert, App, Button, Card, Checkbox, Empty, Input, Radio, Skeleton, Space, Steps, Tag, Typography } from 'antd';
import { workflowApi, isRecoverableOutputError } from './api';
import { evidenceApi } from '../video-model/api';
import { formatDate } from '../../shared/lib/format';
import { ErrorState } from '../../shared/ui';
import './workflow.css';
import { TaskStandardsEditor } from './PublishTaskDrawer';

const verdicts: Record<string, string> = { compliant: 'AI 建议符合', non_compliant: '建议修改', needs_confirmation: '待确认', not_applicable: '不适用', pending: '未判定' };
const stages = ['requirements', 'script', 'video', 'human', 'completed'];

export function WorkflowPanel({ taskId, projectId, scope, selectedScriptVersionId, compact = false, selectedVideoRunId, suggestedNote }: { taskId: string; projectId: string; scope: 'script' | 'video'; selectedScriptVersionId?: string; compact?: boolean; selectedVideoRunId?: string; suggestedNote?: string }) {
  const aiTiming = useAiCallTiming(taskId);
  const { modal, message } = App.useApp();
  const cache = useQueryClient();
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), refetchInterval: q => q.state.data?.data.scriptReview?.status === 'processing' ? 2000 : false });
  const settings = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings });
  const [note, setNote] = useState(''), [reviewed, setReviewed] = useState(false), [decision, setDecision] = useState<'approved' | 'returned'>('approved');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>();
  const lock = useRef(false), analysisKey = useRef<{ signature: string; key: string } | null>(null), decisionKey = useRef<{ signature: string; key: string } | null>(null), revalidateKey = useRef<string | null>(null);
  const view = query.data?.data;
  useEffect(() => { if (suggestedNote) { setNote(suggestedNote); window.setTimeout(() => document.getElementById(`review-note-${taskId}`)?.focus(), 0); } }, [suggestedNote, taskId]);
  useEffect(() => { setReviewed(false); setNote(''); decisionKey.current = null; }, [view?.contextKey, view?.scriptReview?.id, view?.latestVideoRunId]);
  const refresh = async () => {
    await Promise.all([cache.invalidateQueries({ queryKey: ['workflow', taskId] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] }), cache.invalidateQueries({ queryKey: ['task', taskId] }), cache.invalidateQueries({ queryKey: ['project-tasks', projectId] }), cache.invalidateQueries({ queryKey: ['project', projectId] }), cache.invalidateQueries({ queryKey: ['projects'] })]);
  };
  function analyze() {
    if (!view?.currentScript || !settings.data?.data.hasKey || lock.current) return;
    const body = { scriptVersionId: view.currentScript.scriptVersionId, contextKey: view.contextKey, configRevision: settings.data.data.revision, allowRealCall: true };
    lock.current = true;
    modal.confirm({ title: '确认真实审核当前脚本？', content: `将脚本文本和已确认的脚本阶段规则发送至已保存的模型服务（${settings.data.data.model}），一次请求，可能计费。AI 结果仍需品牌确认。`, okText: '确认调用模型', onCancel: () => { lock.current = false; }, onOk: async () => {
      setBusy(true); setError(undefined);
      const signature = JSON.stringify(body);
      if (analysisKey.current?.signature !== signature) analysisKey.current = { signature, key: crypto.randomUUID() };
      try { aiTiming.start(); const r = await workflowApi.analyze(taskId, body, analysisKey.current!.key); aiTiming.finish(r.data.status, r.data); analysisKey.current = null; await refresh(); if (r.data.status === 'succeeded') message.success('脚本 AI 审核完成，等待品牌确认'); else setError(r.data.errorMessage || '审核未完成'); }
      catch (e) { aiTiming.finish('failed'); setError(e instanceof Error ? e.message : '脚本审核失败'); await refresh(); }
      finally { setBusy(false); lock.current = false; }
    } });
  }
  async function submitDecision(nextDecision: 'approved' | 'returned' = decision) {
    if (!view || lock.current) return;
    const targetId = scope === 'script' ? view.scriptReview?.id : view.latestVideoRunId;
    if (!targetId) return;
    const body = { scope, targetId, contextKey: view.contextKey, decision: nextDecision, note: note.trim(), reviewedMaterial: reviewed };
    const signature = JSON.stringify(body);
    if (decisionKey.current?.signature !== signature) decisionKey.current = { signature, key: crypto.randomUUID() };
    lock.current = true; setBusy(true); setError(undefined);
    try { await workflowApi.decide(taskId, body, decisionKey.current!.key); decisionKey.current = null; setNote(''); setReviewed(false); await refresh(); message.success(nextDecision === 'approved' ? '品牌确认已保存' : '已退回修改'); if (nextDecision === 'approved') navigate('/reviews'); }
    catch (e) { setError(e instanceof Error ? e.message : '提交失败'); }
    finally { lock.current = false; setBusy(false); }
  }
  async function revalidate() {
    const reviewId = view?.scriptReview?.id;
    if (!reviewId || lock.current) return;
    lock.current = true; setBusy(true); setError(undefined);
    if (!revalidateKey.current) revalidateKey.current = crypto.randomUUID();
    try { const r = await workflowApi.revalidate(reviewId, revalidateKey.current); revalidateKey.current = null; await refresh(); if (r.data.status === 'succeeded') message.success('已用本地保存的模型返回完成重新校验，未再次调用模型'); else setError(r.data.errorMessage || '重新校验未完成'); }
    catch (e) { setError(e instanceof Error ? e.message : '重新校验失败'); await refresh(); }
    finally { lock.current = false; setBusy(false); }
  }
  if (query.isPending) return <Skeleton active />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  if (!view || view.projectId !== projectId) return <Alert type="error" title="任务不属于当前项目" />;
  const videoSelectionMismatch = compact && scope === 'video' && (!selectedVideoRunId || selectedVideoRunId !== view.latestVideoRunId);
  const historical = videoSelectionMismatch || scope === 'script' && selectedScriptVersionId && selectedScriptVersionId !== view.currentScript?.scriptVersionId;
  const canAnalyze = view.writable && view.rulesReady && view.currentScript?.extractionStatus === 'ready' && !historical && settings.data?.data.hasKey && view.scriptReview?.status !== 'processing';
  const canDecide = view.writable && !historical && (scope === 'script' ? view.brandReviewAllowed : view.videoReviewReady);
  const reviewStatus = view.scriptReview?.status, reviewError = view.scriptReview?.errorCode;
  const needsBillingCheck = scope === 'script' && !!view.scriptReview && !historical && (reviewStatus === 'interrupted' || (reviewStatus === 'failed' && !!reviewError && !isRecoverableOutputError(reviewError) && reviewError !== 'NEEDS_MANUAL_REVIEW'));
  const canRevalidate = scope === 'script' && !!view.scriptReview && !historical && view.scriptReview.status === 'failed' && isRecoverableOutputError(reviewError) && !!view.scriptReview.result?.modelResponse;
  if (compact) return <BrandReviewActions taskId={taskId} projectId={projectId} scope={scope} view={view} targetId={scope === 'video' ? selectedVideoRunId : view.scriptReview?.id} />;
  return <div className="workflow-panel">{scope === 'script' && <div className="ai-timing-row"><AiElapsed run={aiTiming.run || view.scriptReview} /></div>}
    <Card title="审核流程与版本依据" extra={<Link to={`/projects/${projectId}/briefs`}>查看 Brief</Link>}>
      <Steps size="small" current={Math.max(0, stages.indexOf(view.stage))} items={[{ title: '要求确认' }, { title: '脚本与品牌确认' }, { title: '视频取证' }, { title: '人工复核' }, { title: '审核完成' }]} />
      <Space wrap style={{ marginTop: 20 }}><Tag>脚本标准 {view.standardSets?.script?.id.slice(0, 8) || '待确认'}</Tag><Tag>视频标准 {view.standardSets?.video?.id.slice(0, 8) || '待确认'}</Tag><Tag>当前脚本 {view.currentScript?.versionLabel || '未提交'}</Tag><Tag color={view.scriptApproval ? 'green' : 'orange'}>{view.scriptApproval ? '品牌已确认脚本' : '脚本待品牌确认'}</Tag>{view.finalDecision && <Tag color={view.finalDecision.decision === 'approved' ? 'green' : 'red'}>{view.finalDecision.decision === 'approved' ? '品牌最终通过' : '视频退回修改'}</Tag>}</Space>
      {!view.rulesReady && <Alert style={{ marginTop: 12 }} type="warning" title={view.rulesMessage} />}
      {historical && <Alert type="info" title="正在查看历史脚本；审核与品牌决定仅针对当前最新脚本。" />}
      {scope === 'video' && view.scriptApproval && view.currentScript && <details className="workflow-rules"><summary>查看品牌已通过的脚本 {view.currentScript.versionLabel}</summary><Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 360, overflow: 'auto' }}>{view.currentScript.text || view.currentScript.sourceMap.map(block => block.text).join('\n')}</Typography.Paragraph><p className="muted">品牌意见：{view.scriptApproval.note}</p></details>}
      {scope === 'video' && <div style={{ marginTop: 14 }}><TaskStandardsEditor projectId={projectId} taskId={taskId} taskRevision={view.taskRevision || 1} initial={view.standardSets?.script && view.standardSets.video ? { scriptRules: view.standardSets.script.rules, videoRules: view.standardSets.video.rules, sourceVersionId: view.standardSets.script.source_version_id } : undefined} /></div>}
      <details className="workflow-rules"><summary>{scope === 'script' ? '脚本检查标准' : '视频审查标准'} · 独立规则集</summary>
        {!view.standardSets?.[scope] && <Alert type="warning" title="尚无独立标准，请由品牌先审核确认；以下旧要求仅作参考。" />}
        {view.rules.filter(rule => rule.reviewStages.includes(scope)).map(rule => <div className="workflow-rule" key={rule.id}><Space wrap><Tag>{rule.category}</Tag><Tag color={scope === 'script' ? 'blue' : 'cyan'}>{scope === 'script' ? '脚本检查' : '视频审查'}</Tag></Space><p>{rule.text}</p>{rule.criterion && <p><strong>判定口径：</strong>{rule.criterion}</p>}</div>)}
      </details>
    </Card>
    {scope === 'script' && <Card title="AI 脚本审核" extra={view.scriptReview && <Button onClick={analyze} loading={busy} disabled={!canAnalyze}>重新审核当前脚本</Button>}>
      {!settings.data?.data.hasKey && <p><Link to="/settings/models">先配置模型服务与 Key</Link></p>}
      {view.scriptReview ? <><Space><Tag color={view.scriptReview.status === 'succeeded' ? 'green' : view.scriptReview.status === 'interrupted' ? 'red' : 'orange'}>{view.scriptReview.status === 'succeeded' ? 'AI 审核完成，仍需品牌确认' : view.scriptReview.status === 'processing' ? '审核中' : view.scriptReview.status === 'interrupted' ? '审核被服务重启中断' : '审核未完成'}</Tag>{canRevalidate && <Button size="small" style={{ marginLeft: 8 }} loading={busy} onClick={() => void revalidate()}>本地重新校验已存返回</Button>}{needsBillingCheck && <Button size="small" style={{ marginLeft: 8 }} disabled={!canAnalyze} loading={busy} onClick={analyze}>核实用量后重新发起</Button>}<small>{formatDate(view.scriptReview.createdAt)}</small></Space>{view.scriptReview.errorMessage && <Alert style={{ marginTop: 12 }} type="error" title={view.scriptReview.errorCode} description={view.scriptReview.errorMessage} />}
        {view.scriptReview.result?.recovery?.requiresManualReview && <Alert type="warning" showIcon title="格式已修复，须人工核对" description="修复候选仅供核对，不能自动获得通过资格。" />}<Typography.Paragraph style={{ marginTop: 12 }}>{(view.scriptReview.result?.report || view.scriptReview.result?.partialReport)?.summary}</Typography.Paragraph>
        {(view.scriptReview.result?.report || view.scriptReview.result?.partialReport)?.findings.map((finding, i) => <Card key={`${finding.rule_id}-${i}`} size="small" className="workflow-finding" title={<Space><Tag color={finding.verdict === 'non_compliant' ? 'red' : 'blue'}>{verdicts[finding.verdict] || finding.verdict}</Tag><span>{view.rules.find(r => r.id === finding.rule_id)?.text || finding.rule_id}</span></Space>}>
          {finding.quote && <blockquote>{finding.quote}</blockquote>}<p>{finding.description}</p>{finding.suggestion && <p><strong>修改建议：</strong>{finding.suggestion}</p>}
        </Card>)}
        {!!view.scriptReview.result?.providerRequestIds?.length && <small className="muted">请求 ID：{view.scriptReview.result.providerRequestIds.join(' / ')} · 用量 {JSON.stringify(view.scriptReview.result.usage)}</small>}
        {view.scriptReview.status !== 'succeeded' && view.scriptReview.result?.modelResponse && <details><summary>模型返回内容</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{view.scriptReview.result.modelResponse}</pre></details>}
      </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="提交脚本后自动按已保存的详细规则审核，反馈显示在这里" />}
      <p className="muted">脚本能检查台词、镜头安排与内容承诺；实际画面、口播执行与时长仍需成片核验。AI 不会自动确认通过。</p>
    </Card>}
    <Card title={scope === 'script' ? '品牌确认脚本' : '品牌最终复核'} extra={scope === 'video' && view.latestVideoRunId ? <Tag>当前视频 {view.latestVideoRunId.slice(0, 8)}</Tag> : undefined}>
      {scope === 'script' && view.scriptApproval && <Alert style={{ marginBottom: 14 }} type="success" title="当前脚本已由品牌确认通过，可继续审核对应视频" description={view.scriptApproval.note} />}
      {scope === 'video' && !view.videoAllowed && <Alert type="warning" title="当前任务暂不可提交视频，请检查规则及合作状态" action={<Link to={`/projects/${projectId}/tasks/${taskId}/script`}>前往脚本页</Link>} />}
      
      {scope === 'video' && view.videoReviewReady && view.videoComponentAcceptance && (!view.videoComponentAcceptance.passed ? <Alert type="warning" style={{ marginBottom: 14 }} title={view.videoComponentAcceptance.message} description={view.videoComponentAcceptance.blockingRules.map(r => `第${r.ruleNumber || '?'}条：${r.text}`).join('；')} /> : !!view.videoComponentAcceptance.reviewCount && <Alert type="info" style={{ marginBottom: 14 }} title={view.videoComponentAcceptance.message} description={view.videoComponentAcceptance.reviewRules?.map(r => `第${r.ruleNumber || '?'}条：${r.text}`).join('；')} />)}
      <Radio.Group aria-label="品牌审核决定" value={decision} onChange={e => setDecision(e.target.value)} disabled={!canDecide || busy} options={[{ label: '确认通过', value: 'approved', disabled: scope === 'video' && view.videoComponentAcceptance?.passed === false }, { label: '退回修改', value: 'returned' }]} />
      <Input.TextArea style={{ marginTop: 14 }} aria-label="品牌审核意见" placeholder="填写审核意见；与 AI 建议不一致时，请说明判断依据" rows={3} maxLength={2000} value={note} onChange={e => setNote(e.target.value)} disabled={!canDecide || busy} />
      <Checkbox style={{ marginTop: 12 }} checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={!canDecide || busy}>{scope === 'script' ? '我已核对当前脚本、Brief 要求、AI 建议及解析遗漏内容' : '我已核对完整音视频、Brief、脚本状态及取证结果'}</Checkbox>
      <div style={{ marginTop: 16 }}><Space wrap><Button type="primary" disabled={!canDecide || !reviewed || !note.trim() || (scope === 'video' && decision === 'approved' && view.videoComponentAcceptance?.passed === false)} loading={busy} onClick={() => void submitDecision()}>保存品牌决定</Button>{scope === 'script' && view.videoAllowed && <Link to={`/projects/${projectId}/tasks/${taskId}/video`}><Button>进入视频审核 →</Button></Link>}</Space></div>
      {error && <Alert style={{ marginTop: 14 }} type="error" title={error} />}
    </Card>
    {!!view.history.length && <details className="workflow-history"><summary>品牌决定历史（{view.history.length}）</summary>{view.history.map(item => <p key={item.id}><Tag>{item.scope === 'script' ? '脚本' : '视频'}</Tag><Tag color={item.decision === 'approved' ? 'green' : 'orange'}>{item.decision === 'approved' ? '通过' : '退回'}</Tag>{item.note} <small>{formatDate(item.created_at)}</small></p>)}</details>}
    {view.scriptReviews.length > 1 && <details className="workflow-history"><summary>脚本 AI 审核历史（{view.scriptReviews.length}）</summary>{view.scriptReviews.map(r => <div className="workflow-rule" key={r.id}><Tag>{r.status}</Tag><small>{formatDate(r.createdAt)} · 脚本 {r.scriptVersionId.slice(0, 8)}</small><p>{r.result?.report?.summary || r.errorMessage}</p>{r.result?.report?.findings.map((f, i) => <p key={`${f.rule_id}-${i}`}>{verdicts[f.verdict]}：{f.description}</p>)}</div>)}</details>}
  </div>;
}
