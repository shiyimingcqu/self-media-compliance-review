import { useQuery } from '@tanstack/react-query';
import { Alert, Card, Skeleton, Tabs, Tag } from 'antd';
import { standardsApi } from './standardsApi';

export function PromptCatalog() {
  const query = useQuery({ queryKey: ['workflow-prompts'], queryFn: standardsApi.prompts });
  return <Card style={{ marginTop: 24 }} title="各 AI 环节的独立提示词" extra={<Tag>服务端版本管理</Tag>}>
    <p className="muted">每个调用环节使用专用提示词。调用记录冻结提示词内容和版本；这里查看当前版本，不触发模型调用。</p>
    {query.isPending ? <Skeleton active /> : query.isError ? <Alert type="error" title="提示词读取失败" /> : <Tabs items={query.data.data.map(item => ({ key: item.key, label: item.title, children: <><p>{item.purpose} <Tag>{item.version}</Tag></p><pre style={{ whiteSpace: 'pre-wrap', lineHeight: 1.85, background: 'var(--bg-inset)', padding: 18, borderRadius: 10, maxHeight: 420, overflow: 'auto' }}>{item.content}</pre></> }))} />}
  </Card>;
}
