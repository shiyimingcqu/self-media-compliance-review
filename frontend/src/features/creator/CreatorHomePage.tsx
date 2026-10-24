import { progressLabels } from '../workflow/discussionsApi';
import { CreatorReminders } from '../workflow/ReviewDiscussion';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Empty, Skeleton, Space, Tag } from 'antd';
import { ArrowRightOutlined, FileTextOutlined } from '@ant-design/icons';
import { creatorApi } from './api';
import { PageHeader, ErrorState, PlatformBadge } from '../../shared/ui';
import { useCreatorIdentity } from '../../app/CreatorIdentity';
import './creator.css';
import '../workflow/content-review.css';

export function CreatorHomePage() {
  const query = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks });
  const { creator } = useCreatorIdentity();
  const tasks = query.data?.data || [];
  const names = [...new Set(tasks.map(t => t.creator_name))];
  const selected = creator || names[0];
  const visible = selected ? tasks.filter(t => t.creator_name === selected) : tasks;
  return <div className="creator-workspace">
    <PageHeader title="我的合作" description="选择对应的合作任务，分别提交脚本、完整视频或图文。AI 验收后由品牌复核。" action={selected ? <span className="muted">当前达人 · {selected}（右上角可切换）</span> : undefined} />
    {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : !visible.length ? <Card><Empty description={names.length ? "该达人暂无合作记录，可在右上角切换达人" : "暂无合作记录，请切换品牌端添加对应 Brief 的审核记录"} /></Card> : <div className="creator-task-grid">{visible.map(task => <Card className="creator-task-card" key={task.id}>
      <Space><Tag color="cyan">{task.brand_name}</Tag><PlatformBadge platform={task.platform} /></Space>
      <h2>{task.project_name}</h2><CreatorReminders taskId={task.id} /><p className="creator-brief"><FileTextOutlined /> {task.briefTitle}</p>
      <p className="muted">脚本：{task.scriptProgress ? progressLabels[task.scriptProgress] : task.brandApproved ? '品牌已通过脚本' : task.brandDecision?.decision === 'returned' ? '品牌已退回，请按意见修改' : task.scriptAcceptance.passed ? 'AI 验收通过，等待品牌审核' : task.currentVersion ? task.scriptAcceptance.message : '待提交对应 Brief 的脚本'}</p>
      <div className="creator-delivery-links"><Link to={`/creator/projects/${task.project_id}/tasks/${task.id}`}><Button type="primary" icon={<ArrowRightOutlined />}>脚本验收</Button></Link><Link to={`/creator/projects/${task.project_id}/tasks/${task.id}/video`}><Button>视频验收</Button></Link><Link to={`/creator/projects/${task.project_id}/tasks/${task.id}/image`}><Button>图文验收</Button></Link></div><p className="muted">视频：{task.videoProgress ? progressLabels[task.videoProgress] : task.videoAllowed ? '可提交成片' : '待品牌确认脚本'} · 图文：{task.imageBrandApproved ? '品牌已通过' : task.imageVersion ? task.imageAcceptance?.message || '已保存图文' : task.imageRulesReady ? '可提交图文' : '待配置规则'}</p>
    </Card>)}</div>}
  </div>;
}
