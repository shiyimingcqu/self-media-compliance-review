import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Image, Input, Modal, Pagination, Select, Skeleton, Space, Tag } from 'antd';
import { api, fileContentUrl } from '../../shared/api/client';
import { ErrorState } from '../../shared/ui';
import { formatDate, formatVideoTime } from '../../shared/lib/format';
import { artifactUrl } from '../video-model/api';
import './submission-feedback.css';

type Scope = 'script' | 'video';
type Outcome = 'needs_changes' | 'needs_confirmation' | 'call_failed' | 'returned';
const layerNames: { [key: string]: string } = { baseline: '第一层 · 基础', brief: '第二层 · Brief', precise: '第三层 · 详细' };
type Issue = { layer?: string; ruleId: string; ruleNumber?: number; ruleText: string; criterion: string; importance: string; verdict: string; description: string; suggestion: string; quote: string; blocking: boolean };
type Record = { unconfiguredRules?: { ruleId: string; ruleNumber?: number; layer: string; text: string; reason: string }[]; configurationRequired?: boolean; id: string; taskId: string; creatorName: string; scope: Scope; status: string; outcome: Outcome; createdAt: string; versionLabel: string; rulesCurrent: boolean; taskArchived: boolean; summary: string; errorCode?: string; errorMessage?: string; brandNote?: string; reportVerified: boolean; issues: Issue[]; scriptText?: string; fileId?: string; rules?: { id: string; ruleNumber?: number; text: string; criterion: string; importance: string }[]; artifacts?: { id: string; kind: string; startMs: number }[] };
type Feedback = { items: Record[]; total: number; counts: { [key in Outcome]: number } };
const labels: { [key in Outcome]: string } = { needs_changes: '内容需修改', needs_confirmation: '待人工确认', call_failed: '调用异常', returned: '品牌退回' };
const verdictLabels: { [key: string]: string } = { non_compliant: '需修改', needs_confirmation: '待确认', pending: '未判定' };

export function SubmissionFeedback({ projectId, taskId, initialScope }: { projectId: string; taskId?: string; initialScope?: Scope }) {
  const [open, setOpen] = useState(false), [scope, setScope] = useState<Scope | 'all'>(initialScope || 'all');
  const [creatorDraft, setCreatorDraft] = useState(''), [creatorName, setCreatorName] = useState('');
  const [layer, setLayer] = useState('all');
  const [outcome, setOutcome] = useState<Outcome | 'all'>('all'), [page, setPage] = useState(1), [selected, setSelected] = useState<Record>();
  useEffect(() => { setScope(initialScope || 'all'); setPage(1); setSelected(undefined); }, [projectId, taskId, initialScope]);
  const query = useQuery({ queryKey: ['submission-feedback', projectId, taskId, scope, outcome, page, creatorName, layer], enabled: open,
    queryFn: () => { const params = new URLSearchParams({ outcome, offset: String((page - 1) * 20), limit: '20' }); if (scope !== 'all') params.set('scope', scope); if (taskId) params.set('taskId', taskId); if (creatorName) params.set('creatorName', creatorName); if (layer !== 'all') params.set('layer', layer); return api<{ data: Feedback }>(`/projects/${projectId}/submission-feedback?${params}`); } });
  const detail = useQuery({ queryKey: ['submission-feedback-detail', projectId, selected?.scope, selected?.id], enabled: !!selected,
    queryFn: () => api<{ data: Record }>(`/projects/${projectId}/submission-feedback/${selected!.scope}/${selected!.id}`) });
  const value = detail.data?.data;
  return <>
    <Button size="small" onClick={() => setOpen(true)}>查看未通过记录</Button>
    <Drawer title={taskId ? '本达人脚本 / 视频未通过记录' : '项目脚本 / 视频未通过记录'} open={open} onClose={() => setOpen(false)} size={720}>
      
      <Space wrap className="submission-feedback-filters">{!taskId && <Input.Search aria-label="按达人搜索未通过记录" placeholder="搜索达人名称" maxLength={50} allowClear value={creatorDraft} onChange={event => setCreatorDraft(event.target.value)} onSearch={text => { setCreatorName(text.trim()); setPage(1); }} style={{ width: 180 }} />}<Select aria-label="未通过记录类型" value={scope} onChange={v => { setScope(v); setPage(1); }} options={[{ value: 'all', label: '脚本和视频' }, { value: 'script', label: '脚本提交' }, { value: 'video', label: '视频提交' }]} /><Select aria-label="未通过记录原因" value={outcome} onChange={v => { setOutcome(v); setPage(1); }} options={[{ value: 'all', label: '全部原因' }, ...Object.entries(labels).map(([key, label]) => ({ value: key, label }))]} /><Select aria-label="按视频规则层级筛选" value={layer} onChange={v => { setLayer(v); setPage(1); }} options={[{ value: 'all', label: '全部层级' }, ...Object.entries(layerNames).map(([value,label]) => ({ value, label }))]} /><Button onClick={() => void query.refetch()}>刷新记录</Button></Space>
      {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <>
        <div className="submission-feedback-counts">{Object.entries(query.data.data.counts).map(([key, count]) => <span key={key}>{labels[key as Outcome]} <strong>{count}</strong></span>)}</div>
        {!query.data.data.items.length ? <Empty description="当前筛选下暂无未通过记录" /> : query.data.data.items.map(item => <article className="submission-feedback-record" key={`${item.scope}-${item.id}`}>
          <Space wrap><Tag color={item.outcome === 'call_failed' ? 'default' : item.outcome === 'needs_changes' ? 'orange' : 'blue'}>{item.configurationRequired && item.outcome === 'needs_confirmation' ? '待品牌配置' : labels[item.outcome]}</Tag><strong>{item.creatorName}</strong><span>{item.scope === 'script' ? '脚本' : '视频'} · {item.versionLabel}</span><Tag>{item.rulesCurrent ? '当前规则' : '历史规则'}</Tag>{item.taskArchived && <Tag>合作已归档</Tag>}</Space>
          <small>{formatDate(item.createdAt)}</small>
          {item.outcome === 'call_failed' ? <p>{item.errorCode} · {item.errorMessage || '审核调用未完成，不能据此认定内容不合格。'}</p> : <p>{item.brandNote || item.summary || '请查看当时的逐条审核反馈。'}</p>}
          {item.issues.filter(issue => issue.blocking).slice(0, 3).map((issue, n) => <div className="submission-feedback-issue-preview" key={n}>{issue.layer && <Tag>{layerNames[issue.layer]}</Tag>}<strong>第 {issue.ruleNumber || '?'} 条 · {issue.ruleText}</strong><p>{issue.description}</p></div>)}
          {!item.reportVerified && item.issues.length > 0 && <small>报告未完整校验，反馈仅供诊断。</small>}
          <Button type="link" size="small" onClick={() => setSelected(item)}>查看当时规则、材料与反馈</Button>
        </article>)}
        <Pagination size="small" current={page} pageSize={20} total={query.data.data.total} showSizeChanger={false} onChange={setPage} showTotal={total => `共 ${total} 条记录`} />
      </>}
    </Drawer>
    <Modal title={selected ? `${selected.creatorName} · ${selected.versionLabel} · 历史审核` : '历史审核'} open={!!selected} onCancel={() => setSelected(undefined)} footer={<Button onClick={() => setSelected(undefined)}>返回记录并调整规则</Button>} width={1000}>
      {detail.isPending ? <Skeleton active /> : detail.isError ? <ErrorState error={detail.error} retry={() => detail.refetch()} /> : value && <div className="submission-feedback-detail">
        <div className="submission-feedback-material"><h3>当时材料</h3>{value.scope === 'video' && value.fileId && <video controls preload="metadata" src={fileContentUrl(value.fileId)} />}{value.scriptText ? <details open={value.scope === 'script'}><summary>{value.scope === 'script' ? '提交的脚本' : '当时对照脚本'}</summary><pre>{value.scriptText}</pre></details> : value.scope === 'script' && <p>此记录未保存可展示的脚本文本。</p>}{value.scope === 'script' && value.fileId && <a href={fileContentUrl(value.fileId)} target="_blank" rel="noreferrer">打开当时原文件 ↗</a>}
          {!!value.artifacts?.length && <><h3>当时生成的证据</h3>{!value.reportVerified && <p>调用未完成，附件仅供人工核对。</p>}{value.artifacts.map(artifact => <div key={artifact.id}><small>{formatVideoTime(artifact.startMs)}</small>{artifact.kind === 'image' ? <Image src={artifactUrl(value.id, artifact.id)} alt="历史审核截图" /> : <video controls preload="metadata" src={artifactUrl(value.id, artifact.id)} />}</div>)}</>}
        </div>
        <div><Space wrap><Tag>{labels[value.outcome]}</Tag><Tag>{value.rulesCurrent ? '当时使用当前规则' : '当时使用历史规则'}</Tag></Space>{value.errorCode && <Alert type="warning" title={value.errorCode} description={value.errorMessage} />}{value.brandNote && <p><strong>品牌意见：</strong>{value.brandNote}</p>}<p>{value.summary}</p>{value.unconfiguredRules?.map(rule => <Alert key={rule.ruleId} type="info" title={`第 ${rule.ruleNumber || '?'} 条 · ${rule.text} · 待品牌配置`} description={rule.reason} />)}
          {value.issues.map((issue, n) => <section className="submission-feedback-issue" key={n}><Tag>{verdictLabels[issue.verdict] || issue.verdict}</Tag>{issue.layer && <Tag>{layerNames[issue.layer]}</Tag>}<strong>第 {issue.ruleNumber || '?'} 条 · {issue.ruleText}</strong><p className="submission-feedback-criterion"><strong>当时判据：</strong>{issue.criterion || '未保存详细判据'}</p>{issue.quote && <blockquote>{issue.quote}</blockquote>}<p>{issue.description}</p>{issue.suggestion && <p><strong>AI 建议：</strong>{issue.suggestion}</p>}</section>)}
          <details><summary>查看当时全部规则</summary>{value.rules?.map((rule, n) => <p key={rule.id || n}><strong>第 {rule.ruleNumber || n + 1} 条 · {rule.text}</strong><br />{rule.criterion}</p>)}</details>
          <p className="muted">记录只用于复盘；调整规则需回到编辑页核对并保存，不会自动修改历史结论。</p>
        </div>
      </div>}
    </Modal>
  </>;
}
