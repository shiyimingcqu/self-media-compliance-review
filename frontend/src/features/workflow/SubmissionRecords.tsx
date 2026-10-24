import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Image, Input, Select, Skeleton, Space, Table, Tag } from 'antd';
import { api, fileContentUrl } from '../../shared/api/client';
import { ErrorState } from '../../shared/ui';
import { formatDate } from '../../shared/lib/format';
import { artifactUrl } from '../video-model/api';
import { postAssetUrl, type PostVersion } from '../image-posts/api';

const scopes: Record<string, string> = { script: '脚本', video: '视频', image: '图文' };
const outcomes: Record<string, string> = { processing: '审核中', needs_changes: '未通过', needs_confirmation: '待确定 / 待补全', call_failed: '调用异常', pending_brand: '验收满足', approved: '品牌已通过', returned: '品牌退回' };
type Submission = { id: string; taskId: string; scope: string; projectId: string; projectName: string; creatorName: string; outcome: string; status: string; versionLabel: string; createdAt: string; summary: string; errorCode?: string; errorMessage?: string; manualAcceptedCount: number; reportVerified: boolean; taskArchived: boolean; brandDecision?: { decision: string; note: string }; material?: { script?: string; fileId?: string; post?: PostVersion }; rules?: { id: string; ruleNumber?: number; text: string; criterion: string }[]; findings?: { rule_id: string; rule_number?: number; verdict: string; description?: string; reason?: string; quote?: string; suggestion?: string }[]; artifacts?: { id: string; kind: string; startMs: number }[] };

export function SubmissionRecords({ projectId }: { projectId?: string | null }) {
  const [scope, setScope] = useState('all'), [outcome, setOutcome] = useState('all'), [search, setSearch] = useState(''), [page, setPage] = useState(1), [selected, setSelected] = useState<Submission>();
  const query = useQuery({ queryKey: ['submission-records', projectId, scope, outcome, search, page], refetchInterval: 15000, queryFn: () => {
    const params = new URLSearchParams({ outcome, search, offset: String((page - 1) * 20), limit: '20' });
    if (projectId) params.set('projectId', projectId); if (scope !== 'all') params.set('scope', scope);
    return api<{ data: { items: Submission[]; total: number } }>(`/workflow/submission-records?${params}`);
  } });
  const detail = useQuery({ queryKey: ['submission-record-detail', selected?.scope, selected?.id], enabled: !!selected, queryFn: () => api<{ data: Submission }>(`/projects/${selected!.projectId}/submission-records/${selected!.scope}/${selected!.id}`) });
  const value = detail.data?.data;
  return <>
    <div className="progress-toolbar"><Input.Search aria-label="搜索提交记录" placeholder="搜索达人或项目" allowClear onSearch={value => { setSearch(value); setPage(1); }} style={{ width: 240 }} /><Select aria-label="提交记录类型" value={scope} onChange={value => { setScope(value); setPage(1); }} options={[{ value: 'all', label: '全部内容类型' }, ...Object.entries(scopes).map(([value, label]) => ({ value, label }))]} style={{ minWidth: 150 }} /><Select aria-label="提交记录结果" value={outcome} onChange={value => { setOutcome(value); setPage(1); }} options={[{ value: 'all', label: '全部验收结果' }, ...Object.entries(outcomes).map(([value, label]) => ({ value, label }))]} style={{ minWidth: 175 }} /><Button onClick={() => void query.refetch()}>刷新提交记录</Button></div>
    <p className="muted">每次验收独立保留；记录反映当时结果，品牌待审资格以当前版本为准。</p>
    <div className="progress-board">{query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <Table<Submission> rowKey={row => `${row.scope}-${row.id}`} loading={query.isPending} dataSource={query.data?.data.items || []} scroll={{ x: 950 }} pagination={{ current: page, pageSize: 20, total: query.data?.data.total || 0, showSizeChanger: false, onChange: setPage }} locale={{ emptyText: <Empty description="暂无对应的提交记录" /> }} columns={[
      { title: '达人 / 项目', render: (_, row) => <div className="progress-cell"><strong>{row.creatorName}</strong><small>{row.projectName}</small></div> },
      { title: '提交内容', render: (_, row) => <div className="progress-cell"><Tag>{scopes[row.scope]}</Tag><small>{row.versionLabel}</small></div> },
      { title: '当次结果', render: (_, row) => <Space orientation="vertical" size={4}><Tag color={row.outcome === 'needs_changes' || row.outcome === 'returned' ? 'red' : row.outcome === 'call_failed' ? 'default' : row.outcome === 'approved' || row.outcome === 'pending_brand' ? 'green' : 'orange'}>{outcomes[row.outcome]}</Tag>{row.manualAcceptedCount > 0 && <small>含品牌采纳异议</small>}</Space> },
      { title: '反馈摘要', render: (_, row) => <span>{row.errorMessage || row.brandDecision?.note || row.summary || (row.status === 'processing' || row.status === 'queued' ? '审核处理中' : '查看逐条结果')}</span>, ellipsis: true },
      { title: '提交验收时间', dataIndex: 'createdAt', render: value => formatDate(value) },
      { title: '操作', render: (_, row) => <Button size="small" onClick={() => setSelected(row)}>查看提交记录</Button> },
    ]} />}</div>
    <Drawer title={selected ? `${selected.creatorName} · ${scopes[selected.scope]}提交记录` : '提交记录'} open={!!selected} onClose={() => setSelected(undefined)} size={820}>
      {detail.isPending ? <Skeleton active /> : detail.isError ? <ErrorState error={detail.error} retry={() => detail.refetch()} /> : value && <>
        <Space wrap><Tag>{outcomes[value.outcome]}</Tag><span>{value.projectName}</span><small>{formatDate(value.createdAt)}</small>{value.taskArchived && <Tag>已归档</Tag>}</Space>
        {value.errorCode && <Alert type="warning" title={value.errorCode} description={value.errorMessage} />}
        {!value.reportVerified && <p>审核尚未完整完成或校验通过，已有内容仅供查看，不代表达人违规或验收通过。</p>}
        {value.brandDecision && <p><strong>品牌意见：</strong>{value.brandDecision.note}</p>}
        <p>{value.summary}</p>
        <section className="dispute-material"><h3>当时提交材料</h3>
          {value.scope === 'video' && value.material?.fileId && <video controls preload="metadata" src={fileContentUrl(value.material.fileId)} />}
          {value.material?.script && <details open={value.scope === 'script'}><summary>{value.scope === 'script' ? '脚本原文' : '当时参考脚本'}</summary><pre>{value.material.script}</pre></details>}
          {value.scope === 'script' && value.material?.fileId && <a href={fileContentUrl(value.material.fileId)} target="_blank" rel="noreferrer">打开当时原文件 ↗</a>}
          {value.material?.post && <><h4>{value.material.post.title}</h4><p style={{ whiteSpace: 'pre-wrap' }}>{value.material.post.body}</p><Image.PreviewGroup>{value.material.post.images.map(image => <figure key={image.id}><Image width={200} src={postAssetUrl(value.taskId, image.id)} alt={`图文原图${image.ordinal}`} /><figcaption>{image.caption}</figcaption></figure>)}</Image.PreviewGroup></>}
        </section>
        {value.findings?.map((finding, index) => { const rule = value.rules?.find(r => r.id === finding.rule_id); return <section className="dispute-message" key={`${finding.rule_id}-${index}`}><strong>第 {rule?.ruleNumber || finding.rule_number || index + 1} 条 · {rule?.text || '审核意见'}</strong><Tag color={finding.verdict === 'non_compliant' ? 'red' : finding.verdict === 'compliant' ? 'green' : 'orange'}>{({ compliant: '通过', non_compliant: '未通过', needs_confirmation: '待确定', pending: '未判定', not_applicable: '不适用' } as Record<string, string>)[finding.verdict] || finding.verdict}</Tag><details><summary>查看当时判据</summary><p>{rule?.criterion}</p></details>{finding.quote && <blockquote>{finding.quote}</blockquote>}<p>{finding.description || finding.reason}</p>{finding.suggestion && <p>建议：{finding.suggestion}</p>}</section>; })}
        {value.artifacts?.map(artifact => <section key={artifact.id}><small>{(artifact.startMs / 1000).toFixed(1)} 秒</small>{artifact.kind === 'image' ? <Image width={240} src={artifactUrl(value.id, artifact.id)} alt="当时审核截图" /> : <video controls preload="metadata" style={{ width: '100%', maxHeight: 360 }} src={artifactUrl(value.id, artifact.id)} />}</section>)}
      </>}
    </Drawer>
  </>;
}
