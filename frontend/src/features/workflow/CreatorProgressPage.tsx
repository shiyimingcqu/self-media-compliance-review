import { useEffect, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Drawer, Empty, Input, Modal, Select, Skeleton, Space, Table, Tabs, Tag } from 'antd';
import { BellRing, MessageSquare, RefreshCw } from 'lucide-react';
import { useDemoRole } from '../../app/DemoRole';
import { ProjectResourcePage } from '../../app/WorkspacePages';
import { PageHeader, ErrorState } from '../../shared/ui';
import { fileContentUrl } from '../../shared/api/client';
import { formatDate } from '../../shared/lib/format';
import { artifactUrl } from '../video-model/api';
import { discussionsApi, progressLabels, disputeLabels, type DiscussionScope, type Dispute, type Progress } from './discussionsApi';
import { DisputeMessages, useDiscussionAction } from './ReviewDiscussion';
import { SubmissionRecords } from './SubmissionRecords';
import './review-discussion.css';

const scopeLabel = (scope: DiscussionScope) => scope === 'script' ? '脚本' : '视频';
const statusColor = (status: string) => status === 'approved' || status === 'accepted' ? 'green' : status === 'disputed' || status === 'pending' ? 'orange' : status === 'modifying' || status === 'needs_info' ? 'blue' : 'default';

export function CreatorProgressPage() {
  const role = useDemoRole(), [params, setParams] = useSearchParams(), cache = useQueryClient();
  const [scope, setScope] = useState('all'), [status, setStatus] = useState(''), [search, setSearch] = useState(''), [page, setPage] = useState(1);
  const [disputeStatus, setDisputeStatus] = useState('open'), [disputePage, setDisputePage] = useState(1), [selected, setSelected] = useState<Dispute>();
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
    <PageHeader title="达人进度与异议" description="跟踪提交和修改进度，处理逐条异议，并向达人发送站内催办。" />
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
          { title: '操作', render: (_, row) => <Button onClick={() => setSelected(row)}>查看并裁决</Button> },
        ]} />}</div>
      </> },
      { key: 'records', label: '提交记录', children: <SubmissionRecords projectId={projectId} /> },
      { key: 'history', label: '历史未通过记录', children: <ProjectResourcePage scope="feedback" /> },
    ]} />
    <DisputeDecisionDrawer selected={selected} onClose={() => setSelected(undefined)} />
    <Modal title={`催办 ${reminding?.creator_name || ''}`} open={!!reminding} onCancel={() => setReminding(undefined)} onOk={sendReminder} confirmLoading={action.busy} okText="发送站内催办" okButtonProps={{ disabled: !note.trim() }}>
      <p>催办将显示在达人“我的合作”中，不会发送短信或外部消息。</p><Select aria-label="催办阶段" value={reminderScope} onChange={setReminderScope} options={[{ value: 'script', label: '脚本' }, { value: 'video', label: '视频' }]} /><Input.TextArea aria-label="催办内容" value={note} onChange={e => setNote(e.target.value)} rows={4} maxLength={1000} style={{ marginTop: 16 }} />{action.error && <Alert type="error" title={action.error} />}
    </Modal>
  </>;
}

function DisputeDecisionDrawer({ selected, onClose }: { selected?: Dispute; onClose: () => void }) {
  const cache = useQueryClient(), action = useDiscussionAction();
  const [outcome, setOutcome] = useState<'accepted' | 'rejected' | 'needs_info'>('needs_info'), [note, setNote] = useState(''), [reviewed, setReviewed] = useState(false);
  const query = useQuery({ queryKey: ['review-dispute-detail', selected?.id], enabled: !!selected, queryFn: () => discussionsApi.detail(selected!.project_id!, selected!.id), refetchInterval: 15000 });
  const value = query.data?.data;
  useEffect(() => { setNote(''); setReviewed(false); setOutcome('needs_info'); }, [selected?.id]);
  function decide() {
    if (!value) return;
    const body = { outcome, note, revision: value.revision, reviewedMaterial: reviewed };
    void action.run(body, key => discussionsApi.decide(value.project_id!, value.id, body, key), async () => { await query.refetch(); await Promise.all(['review-disputes', 'creator-progress', 'workflow', 'creator-tasks', 'review-discussion', 'workflow-review-queue', 'shell-review-count', 'review-disputes-count'].map(key => cache.invalidateQueries({ queryKey: [key] }))); setNote(''); setReviewed(false); });
  }
  return <Drawer title="规则异议 · 品牌裁决" open={!!selected} onClose={onClose} size={760}>
    {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : value && <>
      <Space wrap><Tag>{value.creator_name} · {value.project_name}</Tag><Tag>{scopeLabel(value.scope)}</Tag><Tag>{disputeLabels[value.status]}</Tag></Space>
      <h2>第 {value.rule.ruleNumber || '?'} 条 · {value.rule.text}</h2><p>{value.rule.criterion}</p>
      {!value.current && <Alert type="info" title="历史版本异议：裁决只保留于这次审核，不改变当前内容资格" />}
      <div className="dispute-ai-reason"><strong>当时 AI 意见</strong><p>{value.finding.description}</p>{value.finding.quote && <blockquote>{value.finding.quote}</blockquote>}</div>
      <section className="dispute-material"><h3>当时提交的材料</h3>{value.scope === 'video' && value.fileId && <video controls preload="metadata" src={fileContentUrl(value.fileId)} />}{value.script && <details open={value.scope === 'script'}><summary>{value.scope === 'script' ? '脚本原文' : '当时参考脚本'}</summary><pre>{value.script}</pre></details>}{value.fileId && <a href={fileContentUrl(value.fileId)} target="_blank" rel="noreferrer">打开原文件 ↗</a>}{value.artifacts.map(item => <div key={item.id}>{item.kind === 'image' ? <a target="_blank" rel="noreferrer" href={artifactUrl(value.review_id, item.id)}>查看截图 · {(item.startMs / 1000).toFixed(1)} 秒 ↗</a> : <video controls src={artifactUrl(value.review_id, item.id)} />}</div>)}</section>
      <DisputeMessages dispute={value} />
      {value.writable && ['pending', 'needs_info'].includes(value.status) && <div className="dispute-ruling"><strong>品牌裁决</strong><p>采纳异议会将本次审核的这条规则记为人工认可，AI 原始意见保留；整份内容仍需完成品牌确认。</p><Select aria-label="异议裁决结果" value={outcome} onChange={setOutcome} options={[{ value: 'needs_info', label: '请达人补充说明' }, { value: 'accepted', label: '采纳异议 · 本条人工通过' }, { value: 'rejected', label: '维持 AI 原判 · 请修改' }]} style={{ width: '100%' }} /><Input.TextArea aria-label="品牌裁决理由" value={note} onChange={e => setNote(e.target.value)} rows={4} maxLength={4000} placeholder="说明依据和后续要求，达人将看到这条回复" /><Checkbox checked={reviewed} onChange={e => setReviewed(e.target.checked)}>已核对本次原始材料、规则及相关证据</Checkbox><Button type="primary" loading={action.busy} disabled={!reviewed || !note.trim()} onClick={decide}>提交品牌裁决</Button>{action.error && <Alert type="error" title={action.error} />}</div>}
    </>}
  </Drawer>;
}
