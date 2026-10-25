import { useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Input, Modal, Select, Space, Table, Tabs, Tag } from 'antd';
import { BellRing, MessageSquare, RefreshCw } from 'lucide-react';
import { useDemoRole } from '../../app/DemoRole';
import { ProjectResourcePage } from '../../app/WorkspacePages';
import { PageHeader, ErrorState } from '../../shared/ui';
import { formatDate } from '../../shared/lib/format';
import { discussionsApi, progressLabels, disputeLabels, type DiscussionScope, type Dispute, type Progress } from './discussionsApi';
import { useDiscussionAction } from './ReviewDiscussion';
import { SubmissionRecords } from './SubmissionRecords';
import './review-discussion.css';

const scopeLabel = (scope: DiscussionScope) => scope === 'script' ? '脚本' : '视频';
const statusColor = (status: string) => status === 'approved' || status === 'accepted' ? 'green' : status === 'disputed' || status === 'pending' ? 'orange' : status === 'modifying' || status === 'needs_info' ? 'blue' : 'default';

export function CreatorProgressPage() {
  const role = useDemoRole(), [params, setParams] = useSearchParams(), cache = useQueryClient();
  const [scope, setScope] = useState('all'), [status, setStatus] = useState(''), [search, setSearch] = useState(''), [page, setPage] = useState(1);
  const [disputeStatus, setDisputeStatus] = useState('open'), [disputePage, setDisputePage] = useState(1);
  const [reminding, setReminding] = useState<Progress>(), [reminderScope, setReminderScope] = useState<DiscussionScope>('script'), [note, setNote] = useState('');
  const action = useDiscussionAction(), tab = params.get('tab') || 'progress';
  const projectId = params.get('projectId');
  const query = useQuery({ queryKey: ['creator-progress', scope, status, search, page, projectId], enabled: role === 'brand', refetchInterval: 15000, queryFn: () => {
    const q = new URLSearchParams({ offset: String((page - 1) * 20), limit: '20', search });
    if (scope !== 'all') q.set('scope', scope); if (status) q.set('status', status); if (projectId) q.set('projectId', projectId);
    return discussionsApi.progress(q);
  } });
  const disputes = useQuery({ queryKey: ['review-disputes', disputeStatus, disputePage, projectId], enabled: role === 'brand', refetchInterval: 15000, queryFn: () => {
    const q = new URLSearchParams({ status: disputeStatus, offset: String((disputePage - 1) * 20), limit: '20' }); if (projectId) q.set('projectId', projectId); return discussionsApi.list(q);
  } });
  if (role !== 'brand') return <Navigate to="/creator" replace />;
  function openReminder(row: Progress) { setReminding(row); setReminderScope(row.focus); setNote(`请及时处理${scopeLabel(row.focus)}，当前状态：${progressLabels[row[row.focus].status]}。如有困难或对 AI 有异议，请在验收页说明。`); }
  function sendReminder() {
    if (!reminding) return;
    const body = { scope: reminderScope, message: note };
    void action.run(body, key => discussionsApi.remind(reminding.id, body, key), async () => { setReminding(undefined); await cache.invalidateQueries({ queryKey: ['creator-progress'] }); });
  }
  function showDisputes() { const next = new URLSearchParams(params); next.set('tab', 'disputes'); setParams(next); }
  return <>
    <PageHeader title="达人进度与异议" />
    <Tabs activeKey={tab} onChange={value => { const next = new URLSearchParams(params); next.set('tab', value); setParams(next); }} items={[
      { key: 'progress', label: '达人进度看板', children: <>
        <div className="progress-toolbar"><Input.Search aria-label="搜索达人进度" placeholder="搜索达人或项目" allowClear onSearch={value => { setSearch(value); setPage(1); }} style={{ width: 260 }} /><Select aria-label="进度检查阶段" value={scope} onChange={value => { setScope(value); setPage(1); }} options={[{ value: 'all', label: '当前进行阶段' }, { value: 'script', label: '脚本进度' }, { value: 'video', label: '视频进度' }]} style={{ minWidth: 160 }} /><Button icon={<RefreshCw size={13} />} onClick={() => void query.refetch()}>刷新</Button></div>
        <div className="creator-progress-metrics">{[['', '全部'], ['not_submitted', '未提交'], ['modifying', '修改中'], ['disputed', '有异议'], ['pending_brand', '待品牌确认'], ['approved', '已通过']].map(([key, label]) => <button key={key} className={`creator-progress-metric${status === key ? ' active' : ''}`} onClick={() => { setStatus(key); setPage(1); }}>{label}<strong>{key ? query.data?.data.counts[key] || 0 : Object.values(query.data?.data.counts || {}).reduce((sum, value) => sum + value, 0)}</strong></button>)}</div>
        <div className="progress-toolbar"><Select aria-label="筛选达人状态" value={status} onChange={value => { setStatus(value); setPage(1); }} options={[{ value: '', label: '全部状态' }, ...Object.entries(progressLabels).map(([value, label]) => ({ value, label }))]} style={{ minWidth: 190 }} /><small>脚本和视频分别记录；品牌最终确认后才显示“品牌已通过”。</small></div>
        <div className="progress-board">{query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <Table<Progress> rowKey="id" loading={query.isPending} dataSource={query.data?.data.items || []} scroll={{ x: 1050 }} pagination={{ current: page, pageSize: 20, total: query.data?.data.total || 0, showSizeChanger: false, onChange: setPage }} columns={[
          { title: '达人 / 项目', render: (_, row) => <div className="progress-cell"><strong>{row.creator_name}</strong><small>{row.project_name}</small></div> },
          ...(['script', 'video'] as const).map(kind => ({ title: `${scopeLabel(kind)}状态`, render: (_: unknown, row: Progress) => <Tag color={statusColor(row[kind].status)}>{progressLabels[row[kind].status]}</Tag> })),
          { title: '最近催办', render: (_, row) => row.lastReminder ? <div className="progress-cell"><span>{formatDate(row.lastReminder.created_at)}</span><small>{row.lastReminder.read_at ? '达人已读' : '达人未读'}</small></div> : '—' },
          { title: '操作', render: (_, row) => <Space wrap><Link to={`/projects/${row.project_id}/tasks/${row.id}/${row.focus}`}>查看材料</Link>{[row.script.status, row.video.status].includes('disputed') && <Button size="small" icon={<MessageSquare size={12} />} onClick={showDisputes}>查看异议</Button>}<Button size="small" icon={<BellRing size={12} />} onClick={() => openReminder(row)}>催办达人</Button></Space> },
        ]} />}</div>
      </> },
      { key: 'disputes', label: '异议处理', children: <>
        <div className="progress-toolbar"><Select aria-label="异议处理状态" value={disputeStatus} onChange={value => { setDisputeStatus(value); setDisputePage(1); }} options={[{ value: 'open', label: '待处理 / 待补充' }, { value: 'all', label: '全部异议' }, { value: 'accepted', label: '已采纳异议' }, { value: 'rejected', label: '已维持原判' }]} style={{ minWidth: 180 }} /><Button onClick={() => void disputes.refetch()}>刷新异议</Button></div>
        <div className="progress-board">{disputes.isError ? <ErrorState error={disputes.error} retry={() => disputes.refetch()} /> : <Table<Dispute> scroll={{ x: 760 }} rowKey="id" loading={disputes.isPending} dataSource={disputes.data?.data.items || []} pagination={{ current: disputePage, pageSize: 20, total: disputes.data?.data.total || 0, showSizeChanger: false, onChange: setDisputePage }} locale={{ emptyText: <Empty description="暂无对应状态的异议" /> }} columns={[
          { title: '达人 / 项目', render: (_, row) => <div className="progress-cell"><strong>{row.creator_name}</strong><small>{row.project_name}</small></div> },
          { title: '争议规则', render: (_, row) => <div className="progress-cell"><Tag>{scopeLabel(row.scope)} · 第 {row.rule.ruleNumber || '?'} 条</Tag><strong>{row.rule.text}</strong><small>{row.messages.filter(m => m.actor === 'creator').at(-1)?.body}</small></div> },
          { title: '状态', render: (_, row) => <Tag color={statusColor(row.status)}>{disputeLabels[row.status]}</Tag> },
          { title: '更新时间', dataIndex: 'updated_at', render: value => formatDate(value) },
          { title: '操作', render: (_, row) => <Link to={`/projects/${row.project_id}/review-disputes/${row.id}`}><Button>查看并裁决</Button></Link> },
        ]} />}</div>
      </> },
      { key: 'records', label: '提交记录', children: <SubmissionRecords projectId={projectId} /> },
      { key: 'history', label: '历史未通过记录', children: <ProjectResourcePage scope="feedback" /> },
    ]} />
    <Modal title={`催办 ${reminding?.creator_name || ''}`} open={!!reminding} onCancel={() => setReminding(undefined)} onOk={sendReminder} confirmLoading={action.busy} okText="发送站内催办" okButtonProps={{ disabled: !note.trim() }}>
      <p>催办将显示在达人“我的合作”中，不会发送短信或外部消息。</p><Select aria-label="催办阶段" value={reminderScope} onChange={setReminderScope} options={[{ value: 'script', label: '脚本' }, { value: 'video', label: '视频' }]} /><Input.TextArea aria-label="催办内容" value={note} onChange={e => setNote(e.target.value)} rows={4} maxLength={1000} style={{ marginTop: 16 }} />{action.error && <Alert type="error" title={action.error} />}
    </Modal>
  </>;
}
