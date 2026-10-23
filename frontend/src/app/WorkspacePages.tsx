import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate } from 'react-router-dom';
import { Input, Pagination, Skeleton, Tag } from 'antd';
import { Folder, ArrowUpRight, Search } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip } from 'recharts';
import { projectApi } from '../features/projects/api';
import { creatorApi } from '../features/creator/api';
import { SubmissionFeedback } from '../features/workflow/SubmissionFeedback';
import { PageHeader, ErrorState } from '../shared/ui';
import { formatDate, platformNames } from '../shared/lib/format';
import { useDemoRole } from './DemoRole';

export function ProjectResourcePage({ scope }: { scope: 'rules' | 'feedback' }) {
  const role = useDemoRole();
  const [search, setSearch] = useState(''), [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ['resource-projects', search, page], queryFn: () => projectApi.list(new URLSearchParams({ q: search, page: String(page), pageSize: '12' }).toString()), enabled: role === 'brand' });
  if (role !== 'brand') return <Navigate to="/creator" replace />;
  return <><PageHeader title={scope === 'rules' ? '规则库' : '达人反馈'} description={scope === 'rules' ? '按活动维护审核要求，冻结版本后用于该活动下的全部达人。' : '对照提交材料与当时规则复盘反馈，让修改要求清晰、有据。'} /><div className="resource-toolbar"><Input aria-label="搜索活动资源" prefix={<Search size={14} />} placeholder="搜索活动名称" value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} /><span>{query.data?.total ?? '—'} 个活动</span></div>{query.isPending ? <div className="panel panel-padding"><Skeleton active paragraph={{ rows: 5 }} /></div> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : query.data.data.length ? <div className="resource-grid">{query.data.data.map(project => <article className="resource-card" key={project.id}><header><Folder size={16} /><Tag>{project.brand.name}</Tag><span className="mono">{String(project.taskCount).padStart(2, '0')} 位达人</span></header><h3>{project.name}</h3><p>{project.description || '尚未填写活动说明'}</p><footer>{scope === 'rules' ? <div className="resource-links">{[{ key: 'script', name: '脚本规则' }, { key: 'video', name: '视频规则' }, { key: 'image', name: '图文规则' }].map(type => <Link key={type.key} to={`/projects/${project.id}/rules?type=${type.key}`}>{type.name}<ArrowUpRight size={12} /></Link>)}</div> : <SubmissionFeedback projectId={project.id} />}<small className="mono">{formatDate(project.updatedAt)}</small></footer></article>)}</div> : <div className="quiet-empty"><span>{search ? '没有找到匹配活动' : '暂无活动资源'}</span><small>创建活动后，在这里维护规则和查看反馈。</small><Link to="/projects">前往活动管理</Link></div>}<Pagination current={page} pageSize={12} total={query.data?.total || 0} hideOnSinglePage showSizeChanger={false} onChange={setPage} /></>;
}
export function DashboardPage() {
  const role = useDemoRole();
  const projects = useQuery({ queryKey: ['dashboard'], queryFn: projectApi.dashboard, enabled: role === 'brand' });
  const tasks = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks, enabled: role === 'brand' });
  if (role !== 'brand') return <Navigate to="/creator" replace />;
  if (projects.isPending || tasks.isPending) return <Skeleton active paragraph={{ rows: 8 }} />;
  if (projects.isError || tasks.isError) return <ErrorState error={projects.error || tasks.error!} retry={() => { void projects.refetch(); void tasks.refetch(); }} />;
  const values = tasks.data.data;
  const confirmed = values.filter(task => task.brandApproved).length;
  const pending = values.filter(task => task.scriptAcceptance.passed && !task.brandApproved).length;
  const channels = Object.entries(platformNames).map(([platform, name]) => ({ name, count: values.filter(task => task.platform === platform).length }));
  return <><PageHeader title="审核数据看板" description="查看当前工作空间的真实活动与提交状态。" /><div className="dashboard-metrics">{[{ label: '进行中活动', value: projects.data.data.activeProjects }, { label: '达人合作任务', value: values.length }, { label: '待品牌确认脚本', value: pending }, { label: '品牌已确认脚本', value: confirmed }].map(item => <article className="panel dashboard-metric" key={item.label}><span>{item.label}</span><strong className="mono">{String(item.value).padStart(2, '0')}</strong><small>当前工作空间</small></article>)}</div><div className="dashboard-panels"><section className="panel panel-padding"><header className="desk-section-heading"><h3>投放平台分布</h3><span>合作任务数</span></header>{values.length ? <ResponsiveContainer width="100%" height={260}><BarChart layout="vertical" data={channels} margin={{ left: 0, right: 24 }}><XAxis type="number" allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: 'var(--text-tertiary)', fontSize: 11, fontFamily: 'var(--font-mono)' }} /><YAxis dataKey="name" type="category" axisLine={false} tickLine={false} width={68} tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} /><Tooltip cursor={{ fill: 'var(--hover-surface)' }} contentStyle={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', borderRadius: 6, color: 'var(--text-primary)' }} /><Bar dataKey="count" name="合作任务" fill="var(--accent)" barSize={12} radius={[0, 2, 2, 0]} isAnimationActive={false} /></BarChart></ResponsiveContainer> : <div className="quiet-empty"><span>暂无合作任务数据</span><small>添加达人后会显示实际平台分布。</small></div>}</section><section className="panel panel-padding"><header className="desk-section-heading"><h3>数据口径</h3><span>可验证数据</span></header><dl className="dashboard-definitions"><dt>统计范围</dt><dd>当前工作空间的达人合作任务</dd><dt>品牌已确认</dt><dd>以当前版本的品牌脚本确认为准</dd><dt>判定质量指标</dt><dd>AI 命中率、漏判数和人工节省时长尚无完整标注数据，暂不展示估算数值。</dd></dl><Link to="/reviews">进入审核队列 <ArrowUpRight size={12} /></Link></section></div></>;
}
