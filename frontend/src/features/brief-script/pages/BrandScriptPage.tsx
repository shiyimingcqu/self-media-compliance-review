import { useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Empty, Skeleton, Tag } from 'antd';
import { workflowApi } from '../../workflow/api';
import { creatorApi } from '../../creator/api';
import { tasksApi } from '../../projects/tasksApi';
import { ContentReviewNav } from '../../workflow/ContentReviewNav';
import { ReviewDiscussionProvider } from '../../workflow/ReviewDiscussion';
import { BrandReviewActions } from '../../workflow/BrandReviewActions';
import { ScriptPreview } from '../../creator/ScriptPreview';
import { ScriptComments } from '../../creator/ScriptComments';
import { ScriptReviewAside } from '../../creator/ScriptReviewAside';
import { scriptAnnotations } from '../../creator/scriptAnnotations';
import { ReviewConnections } from '../../creator/ReviewConnections';
import { ErrorState } from '../../../shared/ui';
import '../../creator/creator.css';

export function BrandScriptPage() {
  const { projectId = '', taskId = '' } = useParams();
  const task = useQuery({ queryKey: ['task', taskId], queryFn: () => tasksApi.detail(taskId) });
  const creators = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks });
  const valid = task.data?.data.projectId === projectId;
  const query = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), enabled: valid, refetchInterval: q => q.state.data?.data.scriptReview?.status === 'processing' ? 2000 : false });
  const view = query.data?.data;
  const annotations = useMemo(() => scriptAnnotations(view?.currentScript || null, view?.scriptReview || null), [view?.currentScript, view?.scriptReview]);
  const [active, setActive] = useState<{ id: string; sequence: number }>();
  const grid = useRef<HTMLDivElement>(null);
  const select = (id: string) => setActive({ id, sequence: Date.now() });
  if (task.isPending || valid && query.isPending) return <Skeleton active />;
  if (task.isError) return <ErrorState error={task.error} retry={() => task.refetch()} />;
  if (!valid) return <Empty description="审核记录不属于当前项目" />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  if (!view) return null;
  const info = creators.data?.data.find(t => t.id === taskId);
  const materialInfo = info || { creator_name: task.data!.data.creatorName, project_name: projectId, brand_name: '—', platform: task.data!.data.platform, brandDecision: null };
  return <ReviewDiscussionProvider taskId={taskId} scope="script" reviewId={view.scriptReview?.id}><div className="creator-workspace creator-script-workspace creator-script-three-column brand-script-review">
    <header className="creator-review-commandbar"><div className="creator-review-identity"><Link to="/reviews">← 审核队列</Link><strong title={info?.project_name}>{info?.project_name || '脚本审核'}</strong><span>{task.data!.data.creatorName}</span><Tag color="gold">品牌复核</Tag><ContentReviewNav projectId={projectId} taskId={taskId} active="script" compact /></div><div className="creator-review-actions"><Link to={`/projects/${projectId}`}>项目详情</Link><Button size="small" loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button></div></header>
    <div ref={grid} className="creator-submit-grid creator-review-grid creator-comment-grid">
      <Card title="脚本解析预览" className="creator-reader-card" extra={view.currentScript && <Tag>{view.currentScript.versionLabel}</Tag>}><ScriptPreview taskId={taskId} script={view.currentScript} review={view.scriptReview} annotations={annotations} active={active} onSelect={select} reviewing={view.scriptReview?.status === 'processing'} /></Card>
      <Card title="审核结果" className="creator-reader-card creator-feedback-card" extra={view.currentScript && <Tag>{view.currentScript.versionLabel}</Tag>}><ScriptComments review={view.scriptReview} acceptance={view.scriptAcceptance} annotations={annotations} active={active} onSelect={select} onRefresh={() => void query.refetch()} refreshing={query.isFetching} /></Card>
      <ScriptReviewAside task={materialInfo} view={view} annotations={annotations} reviewing={false} actions={<BrandReviewActions taskId={taskId} projectId={projectId} scope="script" view={view} targetId={view.scriptReview?.id} />} />
      <ReviewConnections container={grid} activeId={active?.id} revision={`${view.currentScript?.scriptVersionId}:${view.scriptReview?.id}`} />
    </div>
  </div></ReviewDiscussionProvider>;
}
