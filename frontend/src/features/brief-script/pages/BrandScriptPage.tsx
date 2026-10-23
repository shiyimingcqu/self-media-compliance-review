import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Checkbox, Empty, Input, Radio, Skeleton, Space, Tag, Typography } from 'antd';
import { workflowApi } from '../../workflow/api';
import { tasksApi } from '../../projects/tasksApi';
import { TaskStandardsEditor } from '../../workflow/PublishTaskDrawer';
import { ContentReviewNav } from '../../workflow/ContentReviewNav';
import { SubmissionFeedback } from '../../workflow/SubmissionFeedback';
import { ScriptFeedback } from '../../creator/ScriptFeedback';
import { PageHeader, ErrorState } from '../../../shared/ui';
import '../../creator/creator.css';

export function BrandScriptPage() {
  const { projectId = '', taskId = '' } = useParams();
  const { message } = App.useApp(), cache = useQueryClient();
  const task = useQuery({ queryKey: ['task', taskId], queryFn: () => tasksApi.detail(taskId) });
  const valid = task.data?.data.projectId === projectId;
  const query = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), enabled: valid, refetchInterval: q => q.state.data?.data.scriptReview?.status === 'processing' ? 2000 : q.state.data?.data.scriptReview?.status === 'failed' && q.state.data.data.scriptReview.errorCode === 'SCRIPT_REPORT_INVALID' ? 5000 : false });
  const [note, setNote] = useState(''), [reviewed, setReviewed] = useState(false), [decision, setDecision] = useState<'approved' | 'returned'>('approved');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>();
  const view = query.data?.data;
  const lock = useRef(false), generation = useRef(0);
  useEffect(() => { generation.current++; return () => { generation.current++; }; }, [taskId, projectId]);
  useEffect(() => { setNote(''); setReviewed(false); setSubmission(undefined); }, [view?.contextKey, view?.scriptReview?.id]);
  const [submission, setSubmission] = useState<{ signature: string; key: string }>();
  async function submit() {
    if (lock.current || !view?.brandReviewAllowed || !view.scriptReview || busy || !reviewed || !note.trim()) return;
    const body = { scope: 'script', targetId: view.scriptReview.id, contextKey: view.contextKey, decision, note: note.trim(), reviewedMaterial: reviewed };
    const signature = JSON.stringify(body), key = submission?.signature === signature ? submission.key : crypto.randomUUID();
    lock.current = true; const at = generation.current;
    setSubmission({ signature, key }); setBusy(true); setError(undefined);
    try {
      await workflowApi.decide(taskId, body, key); if (at === generation.current) { setSubmission(undefined); setNote(''); setReviewed(false); }
      await Promise.all([cache.invalidateQueries({ queryKey: ['workflow', taskId] }), cache.invalidateQueries({ queryKey: ['task', taskId] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] }), cache.invalidateQueries({ queryKey: ['project-tasks', projectId] })]);
      if (at === generation.current) message.success(decision === 'approved' ? '品牌已确认通过' : '已反馈给达人修改');
    } catch (e) { if (at === generation.current) setError(e instanceof Error ? e.message : '提交失败'); }
    finally { lock.current = false; if (at === generation.current) setBusy(false); }
  }
  if (task.isPending || (valid && query.isPending)) return <Skeleton active />;
  if (task.isError) return <ErrorState error={task.error} retry={() => task.refetch()} />;
  if (!valid) return <Empty description="审核记录不属于当前项目" />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  if (!view) return null;
  const initial = view.standardSets?.script && view.standardSets.video ? { scriptRules: view.standardSets.script.rules, videoRules: view.standardSets.video.rules, sourceVersionId: view.standardSets.script.source_version_id } : undefined;
  return <div className="creator-workspace">
    <Link to={`/projects/${projectId}`}>← 返回品牌项目</Link>
    <PageHeader title={`${task.data!.data.creatorName} · 脚本复核`} description="达人提交脚本，AI 必须项验收通过后，再由品牌作最终审查。" action={<Space><SubmissionFeedback projectId={projectId} taskId={taskId} initialScope="script" /><TaskStandardsEditor projectId={projectId} taskId={taskId} taskRevision={view.taskRevision || 1} initial={initial} /></Space>} />
    <ContentReviewNav projectId={projectId} taskId={taskId} active="script" />
    {!view.brandReviewAllowed && <Alert style={{ marginBottom: 20 }} showIcon type="info" title="尚未进入品牌待审" description={view.scriptAcceptance.message} />}
    {view.scriptApproval && <Alert style={{ marginBottom: 20 }} showIcon type="success" title="品牌已确认通过" description={view.scriptApproval.note} action={<Link to={`/projects/${projectId}/tasks/${taskId}/video`}>进入视频审核 →</Link>} />}
    <div className="creator-submit-grid">
      <Card title="达人提交的脚本" extra={view.currentScript && <Tag>{view.currentScript.versionLabel}</Tag>}>
        {view.currentScript ? <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 650, overflow: 'auto', lineHeight: 1.9 }}>{view.currentScript.text || view.currentScript.sourceMap.map(b => b.text).join('\n')}</Typography.Paragraph> : <Empty description="等待达人提交脚本" />}
      </Card>
      <Card title="AI 必须项验收与反馈"><ScriptFeedback review={view.scriptReview} acceptance={view.scriptAcceptance} onRefresh={() => void query.refetch()} refreshing={query.isFetching} /></Card>
    </div>
    {view.brandReviewAllowed && <Card title="品牌审查" style={{ marginTop: 22 }}>
      <Radio.Group aria-label="品牌审查决定" value={decision} onChange={e => setDecision(e.target.value)} disabled={busy} options={[{ label: '通过脚本', value: 'approved' }, { label: '退回修改', value: 'returned' }]} />
      <Input.TextArea aria-label="品牌审查意见" placeholder="填写品牌审查意见，达人可以看到这条反馈" rows={3} maxLength={2000} value={note} onChange={e => setNote(e.target.value)} disabled={busy} style={{ marginTop: 14, marginBottom: 12 }} />
      <Checkbox checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={busy}>我已核对脚本与品牌要求</Checkbox>
      <div style={{ marginTop: 16 }}><Space><Button type="primary" loading={busy} disabled={!reviewed || !note.trim()} onClick={() => void submit()}>确认品牌审查结果</Button>{view.videoAllowed && <Link to={`/projects/${projectId}/tasks/${taskId}/video`}>查看视频审核</Link>}</Space></div>
      {error && <Alert style={{ marginTop: 14 }} type="error" title={error} />}
    </Card>}
  </div>;
}
