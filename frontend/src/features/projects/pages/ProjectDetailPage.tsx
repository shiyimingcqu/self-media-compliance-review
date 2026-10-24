import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Avatar, Breadcrumb, Button, Card, Dropdown, Empty, Select, Skeleton, Space, Table, Tag } from 'antd';
import { ArrowRightOutlined, FolderOpenOutlined, PlusOutlined, UserOutlined, FileTextOutlined, EllipsisOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { projectApi } from '../api';
import { tasksApi, STAGE_LABELS, STAGE_OPTIONS, stageRoute, safePage, safeStage } from '../tasksApi';
import { CreateTaskDrawer } from '../CreateTaskDrawer';
import type { CreatorTask } from '../../../shared/api/types';
import { ErrorState, PageHeader, PlatformBadge, StatusTag } from '../../../shared/ui';
import { formatDate } from '../../../shared/lib/format';
import { useState } from 'react';
import { ProjectBriefPanel } from '../../workflow/ProjectBriefPanel';
import { ProjectRulesPanel } from '../../workflow/ProjectRulesPanel';
import { standardsApi } from '../../workflow/standardsApi';
import { projectBriefApi } from '../../workflow/projectBriefApi';
import './project-detail-compact.css';
import './project-detail-reference.css';

const PAGE_SIZE = 5;
const FETCH_SIZE = 10;

function ProjectCover({ url, brand }: { url?:string; brand:string }) {
  const [failed,setFailed] = useState(false);
  return <span className="project-cover">{url && !failed ? <img src={url} alt="项目 Brief 封面" onError={() => setFailed(true)} /> : <><FolderOpenOutlined /><strong>{brand}</strong><small>品牌内容项目</small></>}</span>;
}
export function ProjectDetailPage() {
  const { projectId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  // URL 参数归一化：非法值（负数/小数/Infinity/非白名单 stage）回默认，不透传后端。
  const page = safePage(params.get('page'));
  const stage = safeStage(params.get('stage'));
  // 接口每次取10条，界面分两页各显示5条，避免长列表撑高概览。
  const fetchPage = Math.ceil(page / 2);
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => projectApi.detail(projectId) });
  const brief = useQuery({ queryKey:['project-brief',projectId],queryFn:()=>projectBriefApi.get(projectId) });
  const standards = useQuery({ queryKey: ['project-standards', projectId], queryFn: () => standardsApi.projectStandards(projectId) });
  const tasks = useQuery({
    queryKey: ['project-tasks', projectId, fetchPage, stage],
    queryFn: ({ signal }) => tasksApi.list(projectId, stage, fetchPage, FETCH_SIZE, signal),
  });

  if (project.isPending) return <Skeleton active paragraph={{ rows: 8 }} />;
  if (project.isError) return <><Breadcrumb items={[{ title: <Link to="/projects">项目列表</Link> }, { title: '项目详情' }]} /><ErrorState error={project.error} retry={() => project.refetch()} /></>;
  const p = project.data.data;

  const columns: ColumnsType<CreatorTask> = [
    { title: '达人', dataIndex: 'creatorName', key: 'creatorName', width: 160,
      render: (_, t) => <Link to={stageRoute(t.stage, t.projectId, t.id)} className="project-name creator-profile-link"><Avatar size={34} icon={<UserOutlined />} />{t.creatorName}</Link> },
    { title: '平台', dataIndex: 'platform', key: 'platform', width: 110,
      render: platform => <PlatformBadge platform={platform} /> },
    { title: '当前阶段', dataIndex: 'stage', key: 'stage', width: 120,
      render: stage => <Tag color="blue" className="project-stage-pill"><i />{STAGE_LABELS[stage] || stage}</Tag> },
    { title: '审核依据', key: 'rule', width: 150, ellipsis: true,
      render: () => <Tag icon={<FileTextOutlined />} color={standards.data?.data.ready ? 'blue' : 'orange'}>{standards.data?.data.ready ? `项目规则 V${standards.data.data.revision}` : '待确认项目规则'}</Tag> },
    { title: '更新时间', dataIndex: 'updatedAt', key: 'updatedAt', width: 170,
      render: v => <span className="date-text">{formatDate(v)}</span> },
    { title: '归档', key: 'archived', width: 90,
      render: (_, t) => t.archivedAt ? <Tag>已归档</Tag> : <span className="muted">—</span> },
    { title: '操作', key: 'action', width: 220,
      render: (_, t) => <Space className="project-task-actions"><Link to={`/projects/${t.projectId}/tasks/${t.id}/script`}>脚本</Link><Link to={`/projects/${t.projectId}/tasks/${t.id}/video`}>视频</Link><Link to={`/projects/${t.projectId}/tasks/${t.id}/image`}>图文</Link><Link aria-label={`打开任务 ${t.creatorName}`} to={stageRoute(t.stage, t.projectId, t.id)}><ArrowRightOutlined /></Link></Space> },
  ];

  return <div className="project-detail-compact project-detail-reference">
    <Breadcrumb items={[{ title: <Link to="/projects">项目列表</Link> }, { title: p.name }]} />
    <div className="project-detail-header panel">
      <ProjectCover key={brief.data?.data.brief?.visionRunId || p.id} brand={p.brand.name} url={brief.data?.data.brief?.visualRun?.snapshot?.pages?.some(page => page.pageNo === 1) ? `/api/v1/projects/${p.id}/brief/vision/${brief.data.data.brief.visualRun.id}/pages/1` : undefined} />
      <div className="project-hero-copy"><PageHeader title={p.name} description={p.description || '保存审核规则，检查达人提交的脚本、视频与图文。让每一次内容审核都有据可循。'} /><Space wrap><span className="project-brand-chip">品牌 <strong>{p.brand.name}</strong></span><StatusTag status={p.status} />{p.platforms.map(platform => <PlatformBadge key={platform} platform={platform} />)}</Space></div>
      <Dropdown trigger={['click']} menu={{items:[{key:'brief',label:<Link to={`/projects/${p.id}/brief-review`}>查看 Brief 与事实卡</Link>},{key:'rules',label:<Link to={`/projects/${p.id}/rules?type=script`}>编辑项目规则</Link>}]}}><Button className="project-hero-more" aria-label="项目更多操作" icon={<EllipsisOutlined />} /></Dropdown>
    </div>
    <div className="project-overview-grid">
    <ProjectBriefPanel key={`brief-${p.id}`} project={p} />
    <ProjectRulesPanel project={p} />
    </div>
    <Card title={<span className="project-section-title"><UserOutlined />项目达人与审核进度 <Link style={{ fontSize: 12, fontWeight: 400, marginLeft: 12 }} to={`/feedback?projectId=${projectId}`}>进度看板与异议 →</Link></span>} className="panel project-creators-panel"
      extra={<Space>
        <Select aria-label="阶段筛选" value={stage} style={{ minWidth: 130 }}
          onChange={v => { const next = new URLSearchParams(params); if (v) next.set('stage', v); else next.delete('stage'); next.delete('page'); setParams(next); }}
          options={STAGE_OPTIONS} />
        <Button aria-label="添加达人" icon={<PlusOutlined />} type="primary"
          disabled={p.status === 'archived'}
          title={p.status === 'archived' ? '归档项目为只读' : undefined}
          onClick={() => setCreateOpen(true)}>添加达人</Button>
      </Space>}>
      {tasks.isPending ? <Skeleton active paragraph={{ rows: 5 }} /> :
       tasks.isError ? <ErrorState error={tasks.error} retry={() => tasks.refetch()} /> :
       <Table<CreatorTask> scroll={{ x:1000 }} size="small" rowKey="id" columns={columns} dataSource={tasks.data.data.slice(((page - 1) % 2) * PAGE_SIZE, ((page - 1) % 2 + 1) * PAGE_SIZE)}
         pagination={{ current: page, pageSize: PAGE_SIZE, showSizeChanger: false, total: tasks.data.total || 0,
           showTotal: total => `共 ${total} 个任务`,
           onChange: pg => { const next = new URLSearchParams(params); next.set('page', String(pg)); setParams(next); } }}
         locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
           description={<><h3>暂无达人</h3><p>确认项目规则后添加达人，每位达人可提交脚本、视频与图文，分别验收。</p></>} /> }} />}
    </Card>
    <CreateTaskDrawer key={p.id} project={p} open={createOpen} onClose={() => setCreateOpen(false)} />
    <div className="workspace-note">本地项目 · 创建于 {formatDate(p.createdAt)} · 版本 {p.revision}</div>
  </div>;
}
