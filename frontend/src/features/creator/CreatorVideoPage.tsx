import { CreatorReminders } from '../workflow/ReviewDiscussion';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Skeleton, Space, Tag } from 'antd';
import { creatorApi } from './api';
import { workflowApi } from '../workflow/api';
import { ContentReviewNav } from '../workflow/ContentReviewNav';
import { EvidenceConsole } from '../video-model/components/EvidenceConsole';
import { ErrorState } from '../../shared/ui';
import './creator.css';

export function CreatorVideoPage() {
  const { projectId = '', taskId = '' } = useParams();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const tasks = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks });
  const task = tasks.data?.data.find(t => t.id === taskId && t.project_id === projectId);
  const query = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), enabled: !!task });
  if (tasks.isPending || (!!task && query.isPending)) return <Skeleton active />;
  if (tasks.isError || query.isError) return <ErrorState error={tasks.error || query.error!} retry={() => { void tasks.refetch(); void query.refetch(); }} />;
  if (!task || !query.data) return <Alert type="error" title="未找到此合作任务" />;
  const view = query.data.data;
  return <div className="media-review-workspace video-review-workspace">
    <div className="creator-review-commandbar"><div className="creator-review-identity"><Link to="/creator">← 我的合作</Link><strong>{task.creator_name} · 视频验收</strong><ContentReviewNav projectId={projectId} taskId={taskId} active="video" compact /></div><Space size={6}><Button size="small" onClick={() => void query.refetch()}>刷新结果</Button><Button size="small" onClick={() => setDetailsOpen(true)}>审核依据与品牌意见</Button></Space></div>
    <div className="media-review-context"><Tag color={view.finalDecision?.decision === 'approved' ? 'green' : view.videoAllowed ? 'cyan' : 'orange'}>{view.finalDecision ? view.finalDecision.decision === 'approved' ? '品牌已通过视频' : '品牌退回修改' : view.videoAllowed ? '可提交完整成片' : '暂不可提交'}</Tag></div>
    <Drawer title="视频审核依据与品牌意见" open={detailsOpen} onClose={() => setDetailsOpen(false)} size={640}>
      {!view.videoAllowed && <Alert type="warning" title="视频验收尚未开放" description={view.rulesMessage || '请检查项目规则及合作任务状态。'} action={<Link to={`/creator/projects/${projectId}/tasks/${taskId}`}>查看脚本进度</Link>} />}
      {view.finalDecision && <Alert type={view.finalDecision.decision === 'approved' ? 'success' : 'warning'} title="品牌复核意见" description={view.finalDecision.note} />}
      <h3>{view.scriptApproval ? '品牌已通过脚本' : '当前脚本（尚未通过，仅供查看）'} · {view.currentScript?.versionLabel || '待确认'}</h3><p style={{ whiteSpace: 'pre-wrap' }}>{view.currentScript?.text || view.currentScript?.sourceMap.map(block => block.text).join('\n') || '尚未提交脚本，可先上传视频'}</p>
      <h3>本次视频规则</h3>{view.standardSets?.video?.rules.map((rule, index) => <div key={index}><strong>第 {index + 1} 条 · {rule.text}</strong><p>{rule.criterion}</p></div>)}
    </Drawer>
    <CreatorReminders taskId={taskId} /><EvidenceConsole key={taskId} taskId={taskId} disabled={!view.videoAllowed} compact />
  </div>;
}
