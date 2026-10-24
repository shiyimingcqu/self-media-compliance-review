import { CreatorReminders } from '../workflow/ReviewDiscussion';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, Empty, Progress, Skeleton } from 'antd';
import { Check, ChevronRight, Clock3, FileText, Image as ImageIcon, PlaySquare, UploadCloud } from 'lucide-react';
import { creatorApi, type CreatorTask, type Delivery, type DeliveryKind } from './api';
import { PageHeader, ErrorState, PlatformBadge } from '../../shared/ui';
import { formatDate } from '../../shared/lib/format';
import { useCreatorIdentity } from '../../app/CreatorIdentity';
import { deliveryAction, deliveryLabels, deliveryProgress, taskDeliveries } from './deliveryProgress';
import './creator.css';
import './creator-home.css';
import '../workflow/content-review.css';

const labels = { script: '脚本', video: '视频', image: '图文' };
const icons = { script: FileText, video: PlaySquare, image: ImageIcon };

function DeliveryTile({ task, kind, delivery, optional, otherChosen }: { task: CreatorTask; kind: DeliveryKind; delivery: Delivery; optional: boolean; otherChosen: boolean }) {
  const Icon = icons[kind], active = delivery.status !== 'not_submitted';
  const waiting = delivery.submitted && !delivery.approved && !['returned', 'modifying', 'disputed'].includes(delivery.status);
  const tone = delivery.approved ? 'complete' : ['returned', 'needs_response', 'call_failed'].includes(delivery.status) ? 'attention' : waiting ? 'submitted' : active ? 'active' : 'empty';
  const base = `/creator/projects/${task.project_id}/tasks/${task.id}`;
  const action = deliveryAction(delivery);
  const href = `${base}${kind === 'script' ? '' : '/' + kind}${action.upload ? '?action=submit' : ''}`;
  const stamp = delivery.submittedAt || delivery.uploadedAt;
  const status = !active && optional ? otherChosen ? '无需提交' : '二选一' : waiting ? '已提交' : deliveryLabels[delivery.status] || '待查看';
  const button = action.label;
  return <article className={`cooperation-delivery is-${tone}${optional ? ' is-optional' : ''}`} aria-label={`${labels[kind]}交付`}>
    <header><span className="cooperation-type-icon"><Icon size={26} strokeWidth={1.7} /></span><h3>{labels[kind]}</h3><span className={`delivery-status status-${delivery.status}`}>{delivery.approved || waiting ? <Check size={14} /> : <Clock3 size={14} />}{status}</span></header>
    <div className="cooperation-delivery-content">
      {active ? <><ol className="delivery-stepper" aria-label={`${labels[kind]}进度`}>
        {[['提交材料', delivery.submitted], ['品牌复核', delivery.approved], ['完成', delivery.approved]].map(([label, done], index) => <li key={String(label)} className={done ? 'is-done' : (index === 0 && !delivery.submitted || index === 1 && waiting) ? 'is-current' : ''}><i>{done ? <Check size={12} /> : null}</i><span>{label}</span></li>)}
      </ol><p className="delivery-detail">{delivery.status === 'pending_review' ? '材料已上传，尚未进行 AI 验收' : delivery.status === 'reviewing' ? 'AI 正在验收，完成后显示结果' : ['returned', 'modifying', 'needs_response', 'disputed', 'call_failed', 'ready_to_submit'].includes(delivery.status) ? deliveryLabels[delivery.status] : delivery.approved ? '品牌已确认本次内容' : '等待品牌复核'}</p><p className="delivery-timestamp">{stamp ? `${delivery.submittedAt ? '提交于' : '上传于'} ${formatDate(stamp)}` : task.currentVersion && kind === 'script' ? `当前版本 ${task.currentVersion}` : '尚未提交品牌'}<ChevronRight size={15} /></p></> : <p className="delivery-empty-copy">{kind === 'script' ? '提交合作脚本，核对内容与卖点安排' : otherChosen ? `已选择${kind === 'video' ? '图文' : '视频'}交付，无需再提交${labels[kind]}` : `可选择${labels[kind]}作为本次成片内容`}</p>}
    </div>
    <Link className={`cooperation-action${!active && kind === 'video' && !otherChosen ? ' is-primary' : ''}`} to={href}>{action.upload || delivery.status === 'pending_review' ? <UploadCloud size={20} /> : <FileText size={19} />}{button}</Link>
  </article>;
}

export function CreatorHomePage() {
  const query = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks, refetchInterval: 15000 });
  const { creator } = useCreatorIdentity();
  const tasks = query.data?.data || [], names = [...new Set(tasks.map(t => t.creator_name))];
  const selected = creator || names[0];
  const visible = selected ? tasks.filter(t => t.creator_name === selected) : tasks;
  return <div className="creator-workspace creator-home-workspace">
    <PageHeader title="我的合作" description="选择对应的合作任务，提交脚本，以及视频或图文任选一种。AI 验收后由品牌复核。" />
    {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : !visible.length ? <Card><Empty description={names.length ? '该达人暂无合作记录，可在右上角切换达人' : '暂无合作记录，请切换品牌端添加合作任务'} /></Card> : <div className="cooperation-list">{visible.map(task => {
      const deliveries = taskDeliveries(task), progress = deliveryProgress(deliveries);
      return <section className="cooperation-card" key={task.id} aria-label={`合作任务 ${task.project_name}`}>
        <div className="cooperation-heading"><div className="cooperation-project"><div className="cooperation-badges"><span className="cooperation-brand">{task.brand_name}</span><PlatformBadge platform={task.platform} /></div><h2>{task.project_name}</h2><p className="cooperation-brief"><FileText size={19} />{task.briefTitle}</p></div>
          <div className="cooperation-progress" aria-label={`合作进度 ${progress.submitted}/2 已提交`}><Progress type="circle" percent={progress.submitted * 50} size={38} strokeWidth={12} strokeColor={progress.complete ? '#32a267' : '#d4ad66'} railColor="#e9edf3" format={() => null} /><span>{progress.submitted}/2 已提交<small>{progress.complete ? '合作内容已完成' : '脚本＋成片（视频或图文）'}</small></span></div>
        </div>
        <CreatorReminders taskId={task.id} />
        <div className="cooperation-deliveries"><DeliveryTile task={task} kind="script" delivery={deliveries.script} optional={false} otherChosen={false} /><div className="cooperation-material-choice"><div className="cooperation-choice-label"><span>成片交付</span><span>视频 / 图文，任选一种即可</span></div><div className="cooperation-material-options"><DeliveryTile task={task} kind="video" delivery={deliveries.video} optional otherChosen={progress.imageChosen && !progress.videoChosen} /><DeliveryTile task={task} kind="image" delivery={deliveries.image} optional otherChosen={progress.videoChosen && !progress.imageChosen} /></div></div></div>
      </section>;
    })}</div>}
  </div>;
}
