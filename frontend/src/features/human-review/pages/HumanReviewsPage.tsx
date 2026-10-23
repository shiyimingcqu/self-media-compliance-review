import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Card, Empty, Input, Segmented, Skeleton, Space, Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../shared/api/client';
import { ErrorState, PageHeader, PlatformBadge } from '../../../shared/ui';
import type { Platform } from '../../../shared/api/types';
import { formatDate } from '../../../shared/lib/format';

type QueueItem = { id: string; creator_name: string; platform: Platform; project_id: string; project_name: string; scope: 'script' | 'video' | 'image'; submittedAt: string; summary: string };

export function HumanReviewsPage() {
  const [scope, setScope] = useState('all'), [search, setSearch] = useState('');
  const query = useQuery({ queryKey: ['workflow-review-queue'], queryFn: () => api<{ data: QueueItem[] }>('/workflow/review-queue'), refetchOnWindowFocus: true });
  const rows = query.data?.data || [];
  const filtered = rows.filter(r => (scope === 'all' || r.scope === scope) && `${r.creator_name} ${r.project_name}`.toLowerCase().includes(search.toLowerCase()));
  return <>
    <PageHeader title="待品牌审核" description="脚本与图文必须项经 AI 验收通过后进入这里，视频完成审核后交品牌复核。" />
    <Card title={`待品牌确认 · ${rows.length}`} extra={<Space><Input.Search aria-label="搜索人工待办" placeholder="搜索达人或项目" value={search} onChange={e => setSearch(e.target.value)} /><Segmented value={scope} onChange={v => setScope(String(v))} options={[{ label: '全部', value: 'all' }, { label: '脚本确认', value: 'script' }, { label: '视频复核', value: 'video' }, { label: '图文复核', value: 'image' }]} /></Space>}>
      {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <Table rowKey={r => `${r.id}-${r.scope}`} dataSource={filtered} pagination={{ pageSize: 10 }} locale={{ emptyText: <Empty description="暂无待品牌确认的审核结果" /> }} columns={[
        { title: '达人', dataIndex: 'creator_name' }, { title: '项目', dataIndex: 'project_name' },
        { title: '平台', dataIndex: 'platform', render: p => <PlatformBadge platform={p} /> },
        { title: '待办', dataIndex: 'scope', render: s => <Tag color={s === 'script' ? 'blue' : 'purple'}>{s === 'script' ? '品牌确认脚本' : s === 'image' ? '品牌复核图文' : '品牌最终复核'}</Tag> },
        { title: 'AI 摘要', dataIndex: 'summary', ellipsis: true },
        { title: '提交时间', dataIndex: 'submittedAt', render: v => formatDate(v) },
        { title: '操作', render: (_, r) => <Link to={`/projects/${r.project_id}/tasks/${r.id}/${r.scope}`}>进入复核 →</Link> },
      ]} />}
    </Card>
  </>;
}
