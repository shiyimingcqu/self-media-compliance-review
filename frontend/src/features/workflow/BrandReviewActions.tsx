import { useEffect, useState } from 'react';
import { Alert, App, Button, Checkbox, Input } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { workflowApi, type Workflow } from './api';
import { useDiscussionAction } from './ReviewDiscussion';

export function BrandReviewActions({ taskId, projectId, scope, view, targetId }: { taskId: string; projectId: string; scope: 'script' | 'video'; view: Workflow; targetId?: string }) {
  const cache = useQueryClient(), { message } = App.useApp(), action = useDiscussionAction();
  const navigate = useNavigate();
  const [returning, setReturning] = useState(false), [note, setNote] = useState(''), [reviewed, setReviewed] = useState(false);
  const [confirmed, setConfirmed] = useState<string[]>([]);
  const currentId = scope === 'script' ? view.scriptReview?.id : view.latestVideoRunId;
  const historical = !targetId || targetId !== currentId;
  const available = view.writable && !historical && (scope === 'script' ? view.scriptSubmitted && view.scriptReview?.status === 'succeeded' : view.videoReviewReady && view.videoSubmitted !== false);
  const pendingChecks = scope === 'script' && view.scriptAcceptance.status === 'needs_confirmation'
    ? view.scriptAcceptance.checks.filter(check => ['needs_confirmation', 'pending'].includes(check.verdict)) : [];
  const canConfirm = view.rulesReady && pendingChecks.length > 0;
  const canApprove = available && (scope === 'script' ? view.brandReviewAllowed || canConfirm && pendingChecks.every(check => confirmed.includes(check.rule_id)) : view.videoComponentAcceptance?.passed !== false);
  const decision = historical ? null : scope === 'script' ? view.history.find(item => item.scope === 'script' && item.target_id === currentId) : view.finalDecision;
  useEffect(() => { setReturning(false); setNote(''); setReviewed(false); setConfirmed([]); }, [targetId, view.contextKey]);
  function submit(outcome: 'approved' | 'returned') {
    if (!targetId || !available || !reviewed || outcome === 'approved' && !canApprove || outcome === 'returned' && !note.trim()) return;
    const body = { scope, targetId, contextKey: view.contextKey, decision: outcome, note: outcome === 'approved' ? '品牌已核对当前材料，审核通过。' : note.trim(), reviewedMaterial: reviewed, ...(scope === 'script' && outcome === 'approved' ? { confirmedRuleIds: confirmed } : {}) };
    void action.run(body, key => workflowApi.decide(taskId, body, key), async () => {
      setReturning(false); setNote(''); setReviewed(false);
      await Promise.all(['workflow', 'task', 'creator-tasks', 'workflow-review-queue', 'shell-review-count', 'project-tasks', 'creator-progress', 'submission-records', 'projects'].map(key => cache.invalidateQueries({ queryKey: [key] })));
      await cache.invalidateQueries({ queryKey: ['project', projectId] });
      message.success(outcome === 'approved' ? '审核通过，已通知达人' : '已退回，达人可查看修改要求');
      if (outcome === 'approved') navigate('/reviews');
    });
  }
  return <section className="brand-review-actions" aria-label="品牌审核操作">
    <h3>品牌审核</h3>
    {decision && <Alert type={decision.decision === 'approved' ? 'success' : 'warning'} title={decision.decision === 'approved' ? '品牌审核通过' : '已让达人继续修改'} description={decision.note} />}
    {!available && !decision && <p className="brand-review-status">{historical ? '请查看当前版本审核记录' : !view.writable ? '当前任务不可编辑' : (scope === 'script' ? !view.scriptSubmitted : view.videoSubmitted === false) ? '等待达人提交品牌' : '审核报告尚未完成'}</p>}
    {available && canConfirm && <section className="brand-pending-checks" aria-label="待确定必须项确认"><h3>待确定必须项 · {pendingChecks.length} 项</h3><p>请核对原稿，逐项确认符合要求后，再审核通过；如需调整，可让达人继续修改。</p>{pendingChecks.map((check, index) => <div key={`${check.rule_id}-${index}`}><strong>{check.rule_number ? `第 ${check.rule_number} 条 · ` : ''}{check.requirement}</strong><p>{check.reason}</p><Checkbox checked={confirmed.includes(check.rule_id)} disabled={action.busy} onChange={e => setConfirmed(ids => e.target.checked ? [...ids, check.rule_id] : ids.filter(id => id !== check.rule_id))}>已核对，确认符合此项要求</Checkbox></div>)}</section>}
    {available && !canApprove && !canConfirm && <p className="brand-review-status">{scope === 'script' ? view.scriptAcceptance.message : '视频必须项尚未通过'}。可处理异议，或让达人继续修改。</p>}
    {available && canApprove && !reviewed && <p className="brand-review-status">勾选下方“已核对本次材料及审核结果”后，即可审核通过。</p>}
    <Checkbox checked={reviewed} disabled={!available || action.busy} onChange={e => setReviewed(e.target.checked)}>已核对本次材料及审核结果</Checkbox>
    <Button type="primary" block loading={action.busy && !returning} disabled={!canApprove || !reviewed || action.busy} onClick={() => submit('approved')}>审核通过</Button>
    <Button block disabled={!available || action.busy} onClick={() => setReturning(!returning)}>{returning ? '取消退回' : '让达人继续修改'}</Button>
    {returning && <div className="brand-return-form"><Input.TextArea aria-label="达人修改要求" placeholder="请写明哪里需要修改、希望如何调整，达人会看到这些内容" value={note} onChange={e => setNote(e.target.value)} rows={4} maxLength={2000} showCount disabled={action.busy} /><Button type="primary" block loading={action.busy} disabled={!reviewed || !note.trim() || action.busy} onClick={() => submit('returned')}>确认退回修改</Button></div>}
    {action.error && <Alert type="error" title={action.error} />}
  </section>;
}
