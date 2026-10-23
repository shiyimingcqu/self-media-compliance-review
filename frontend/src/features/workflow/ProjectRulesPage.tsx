import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Breadcrumb, Skeleton, Space, Tag } from 'antd';
import { projectApi } from '../projects/api';
import { standardsApi } from './standardsApi';
import { SubmissionFeedback } from './SubmissionFeedback';
import { SimpleReviewDrawer } from './SimpleReviewDrawer';
import { useDemoRole } from '../../app/DemoRole';
import { ErrorState } from '../../shared/ui';
import type { RuleCategory } from './RuleCategoryButtons';

export function ProjectRulesPage() {
  const { projectId = '' } = useParams(), [params, setParams] = useSearchParams();
  const role = useDemoRole();
  const category: RuleCategory = params.get('type') === 'image' ? 'image' : params.get('type') === 'video' ? 'video' : 'script';
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => projectApi.detail(projectId), enabled: role === 'brand' });
  const standards = useQuery({ queryKey: ['project-standards', projectId], queryFn: () => standardsApi.projectStandards(projectId), enabled: role === 'brand' });
  if (role !== 'brand') return <Navigate to="/creator" replace />;
  if (project.isError || standards.isError) return <ErrorState error={project.error || standards.error || new Error('项目规则读取失败')} retry={() => { void project.refetch(); void standards.refetch(); }} />;
  if (!project.data || !standards.data) return <Skeleton active paragraph={{ rows: 8 }} />;
  const p = project.data.data, v = standards.data.data;
  const initial = v.script && v.video ? { imageRules: v.image?.rules, scriptRules: v.script.rules, videoRules: v.video.rules, sourceVersionId: v.script.source_version_id } : undefined;
  return <div className={`project-rules-page${category === 'script' ? ' is-script' : ''}`}>
    <Breadcrumb items={[{ title: <Link to="/projects">项目列表</Link> }, { title: <Link to={`/projects/${p.id}`}>{p.name}</Link> }, { title: '项目审核规则' }]} />
    <div className="project-rules-page-heading"><div><h1>{category === 'script' ? `${p.name} · 审核规则` : '项目审核规则'}</h1><p>{category === 'script' ? '制定本项目的内容审核规则，AI 将基于规则对脚本、图片与视频进行自动审核。' : `${p.name} · 本项目所有达人共用`}</p></div><Space wrap><SubmissionFeedback projectId={p.id} initialScope={category === 'image' ? undefined : category} /><Tag color={v.ready ? 'green' : 'orange'}>{v.ready ? `已确认 V${v.revision}` : '待确认'}</Tag></Space></div>
    <SimpleReviewDrawer key={`${p.id}-${v.revision}`} project={p} open pageMode initial={initial} excludedVideoRuleTexts={v.excludedVideoRuleTexts} initialCategory={category} onCategoryChange={type => setParams({ type }, { replace: true })} projectRulesRevision={v.revision} legacyCandidates={v.legacyCandidates} onClose={() => {}} />
  </div>;
}
