import { FileTextOutlined } from '@ant-design/icons';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Skeleton, Space, Tag } from 'antd';
import type { ProjectDetail } from '../../shared/api/types';
import { standardsApi } from './standardsApi';
import { RuleCategoryButtons } from './RuleCategoryButtons';
import { SubmissionFeedback } from './SubmissionFeedback';

export function ProjectRulesPanel({ project }: { project: ProjectDetail }) {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ['project-standards', project.id], queryFn: () => standardsApi.projectStandards(project.id) });
  const value = query.data?.data;
  return <Card className="panel project-rules-panel" title={<span className="project-section-title"><FileTextOutlined />项目统一审核规则</span>} extra={<SubmissionFeedback projectId={project.id} />}>
    {query.isPending ? <Skeleton active paragraph={{ rows: 1 }} /> : query.isError ? <Alert type="error" title="项目规则读取失败" /> : value && <Space className="project-rules-metadata" wrap><Tag color={value.ready ? 'green' : 'orange'}>{value.ready ? `已确认 · V${value.revision}` : '待品牌确认'}</Tag><span>脚本 {value.script?.rules.length || 0} 条</span><span>图文 {value.image?.rules.length || 0} 条</span><span>视频 {value.video?.rules.length || 0} 条</span><Link to={`/projects/${project.id}/brief-review`}>查看 Brief 与事实卡</Link></Space>}
    {value && !value.ready && <Alert type="info" style={{ marginTop: 12 }} title={value.migrationMessage || '先配置两套项目规则，再添加达人提交脚本和视频'} />}
    {value && (['script', 'video'] as const).some(scope => value.standardWarnings?.[scope]?.length) && <details className="project-rule-notices"><summary>部分规则需完善判据 · 展开查看</summary><Alert style={{ marginTop: 12 }} type="warning" title="部分规则无法脱离 Brief 独立判断" description={<>{(['script', 'video'] as const).flatMap(scope => (value.standardWarnings?.[scope] || []).map(w => <div key={`${scope}-${w.ruleNumber}`}>{scope === 'script' ? '脚本' : '视频'}第 {w.ruleNumber} 条「{w.text}」：{w.reason}</div>))}<div>判断阶段不再提供 Brief 原文。请重新生成规则，或把具体事实写入判据 / 参数后再确认。</div></>} /></details>}
    <RuleCategoryButtons counts={{ script: value?.script?.rules.length || 0, image: value?.image?.rules.length || 0, video: value?.video?.rules.length || 0 }} disabled={project.status === 'archived' || !value} onSelect={type => void navigate(`/projects/${project.id}/rules?type=${type}`)} />
  </Card>;
}

export function ProjectRulesButton({ projectId }: { projectId: string }) {
  return <Link to={`/projects/${projectId}/rules?type=script`}><Button>编辑项目规则</Button></Link>;
}
