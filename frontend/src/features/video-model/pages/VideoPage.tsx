import { useState } from 'react';
import { Alert, Button, Drawer, Skeleton, Space, Tag } from 'antd';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { tasksApi } from '../../projects/tasksApi';
import { ErrorState } from '../../../shared/ui';
import { EvidenceConsole } from '../components/EvidenceConsole';
import { workflowApi } from '../../workflow/api';
import { WorkflowPanel } from '../../workflow/WorkflowPanel';
import { SubmissionFeedback } from '../../workflow/SubmissionFeedback';
import { ContentReviewNav } from '../../workflow/ContentReviewNav';
import '../../creator/creator.css';

export function VideoPage() {
  const { projectId = '', taskId = '' } = useParams();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [feedbackNote, setFeedbackNote] = useState('');
  const task = useQuery({ queryKey: ['task', taskId], queryFn: () => tasksApi.detail(taskId), enabled: !!taskId });
  const valid = task.data?.data.projectId === projectId;
  const workflow = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), enabled: !!valid });
  const eligible = valid && workflow.data?.data.videoAllowed;
  return <div className="media-review-workspace video-review-workspace">
    <div className="creator-review-commandbar"><div className="creator-review-identity"><Link to={`/projects/${projectId}`}>← 项目详情</Link><strong>{task.data?.data.creatorName || '合作任务'} · 视频验收</strong><ContentReviewNav projectId={projectId} taskId={taskId} active="video" compact /></div><Space size={6}><SubmissionFeedback projectId={projectId} taskId={taskId} initialScope="video" /><Link to="/settings/models">模型设置</Link><Button size="small" onClick={() => setDetailsOpen(true)}>审核依据与品牌复核</Button></Space></div>
    {task.isPending ? <Skeleton active /> : task.isError ? <ErrorState error={task.error} retry={() => task.refetch()} /> : !valid ? <Alert showIcon type="error" title="任务不属于当前项目" /> : <>
      <div className="media-review-context"><span>{eligible ? workflow.data?.data.scriptApproval ? `对照已通过脚本 ${workflow.data?.data.currentScript?.versionLabel || ''} · AI 建议仍需品牌复核` : '可先提交成片 · 暂无已通过脚本，脚本匹配项待品牌确认' : workflow.data?.data.rulesMessage || '请检查项目规则及合作任务状态'}</span><Tag color={eligible ? 'cyan' : 'orange'}>{eligible ? '可提交成片' : '暂不可提交'}</Tag></div>
      {workflow.isError && <ErrorState error={workflow.error} retry={() => workflow.refetch()} />}
      <EvidenceConsole key={`evidence-${taskId}`} taskId={taskId} disabled={!eligible} compact onDisagree={setFeedbackNote} reviewActions={runId => <WorkflowPanel taskId={taskId} projectId={projectId} scope="video" compact selectedVideoRunId={runId} suggestedNote={feedbackNote} />} />
      <Drawer title="视频审核依据与品牌最终复核" open={detailsOpen} onClose={() => setDetailsOpen(false)} size={760}><WorkflowPanel key={`workflow-${taskId}`} taskId={taskId} projectId={projectId} scope="video" /></Drawer>
    </>}
  </div>;
}
