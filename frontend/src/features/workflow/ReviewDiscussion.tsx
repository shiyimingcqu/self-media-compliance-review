import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Drawer, Input, Space, Tag } from 'antd';
import { MessageSquare, CheckCheck } from 'lucide-react';
import { useDemoRole } from '../../app/DemoRole';
import { discussionsApi, disputeLabels, type Discussion, type DiscussionScope, type Dispute } from './discussionsApi';
import { formatDate } from '../../shared/lib/format';
import './review-discussion.css';

type Context = { taskId: string; scope: DiscussionScope; reviewId: string; data?: Discussion; error?: Error | null; disputing: boolean; setDisputing: (value: boolean) => void; refresh: () => Promise<void> };
const DiscussionContext = createContext<Context | null>(null);
export function ReviewDiscussionProvider({ taskId, scope, reviewId, children }: { taskId?: string; scope: DiscussionScope; reviewId?: string; children: ReactNode }) {
  const cache = useQueryClient();
  const [disputing, setDisputing] = useState(false);
  const query = useQuery({ queryKey: ['review-discussion', taskId, scope, reviewId], queryFn: () => discussionsApi.get(taskId!, scope, reviewId!), enabled: !!taskId && !!reviewId, refetchInterval: 15000 });
  useEffect(() => { setDisputing(false); }, [reviewId]);
  async function refresh() {
    await Promise.all([query.refetch(), cache.invalidateQueries({ queryKey: ['creator-progress'] }), cache.invalidateQueries({ queryKey: ['review-disputes'] }), cache.invalidateQueries({ queryKey: ['workflow', taskId] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['shell-review-count'] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] }), cache.invalidateQueries({ queryKey: ['review-disputes-count'] }), cache.invalidateQueries({ queryKey: ['submission-records'] })]);
  }
  return <DiscussionContext.Provider value={taskId && reviewId ? { taskId, scope, reviewId, data: query.data?.data, error: query.error, disputing, setDisputing, refresh } : null}>{children}</DiscussionContext.Provider>;
}

export function useDiscussionAction() {
  const key = useRef<{ signature: string; key: string } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>();
  async function run(body: unknown, send: (key: string) => Promise<unknown>, done: () => void | Promise<void>) {
    if (busy) return;
    const signature = JSON.stringify(body);
    if (key.current?.signature !== signature) key.current = { signature, key: crypto.randomUUID() };
    setBusy(true); setError(undefined);
    try { await send(key.current.key); key.current = null; await done(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '操作失败，请重试'); }
    finally { setBusy(false); }
  }
  return { busy, error, run };
}

export function CreatorReviewResponse({ needsResponse, canSubmitScript = false, canSubmitVideo = false, submissionBlockedReason, sidebar = false, onRecognize, recognizing, recognizeDisabled }: { needsResponse: boolean; canSubmitScript?: boolean; canSubmitVideo?: boolean; submissionBlockedReason?: string; sidebar?: boolean; onRecognize?: () => void; recognizing?: boolean; recognizeDisabled?: boolean }) {
  const context = useContext(DiscussionContext), role = useDemoRole(), action = useDiscussionAction();
  const navigate = useNavigate();
  if (!context || role !== 'creator' || !needsResponse) return null;
  const { data, taskId, scope, reviewId } = context;
  const open = data?.disputes.filter(d => ['pending', 'needs_info'].includes(d.status)).length || 0;
  const submittingToBrand = scope === 'script' ? canSubmitScript : canSubmitVideo;
  const submissionBlocked = submittingToBrand && data?.submissionReady === false;
  const submitted = submittingToBrand && data?.response?.action === 'submitted';
  function respond() {
    if (submissionBlocked) return;
    const body = { scope, reviewId, action: submittingToBrand ? 'submitted' : 'modifying', revision: data?.response?.revision || 0 };
    void action.run(body, key => discussionsApi.respond(taskId, body, key), async () => {
      await context!.refresh();
      if (submittingToBrand) navigate('/creator');
    });
  }
  return <section className={`creator-review-response${sidebar ? ' is-sidebar' : ''}`} aria-label="选择审核后的处理方式">
    <strong>{submittingToBrand ? '提交品牌审核' : sidebar ? '审核意见' : '这次审核意见，你准备怎么处理？'}</strong>
    {context.error && <Alert type="error" title="处理状态加载失败，请刷新后重试" />}
    {!data?.current && data && <p>这是历史审核，处理状态和异议仅供查看。</p>}
    {submissionBlocked && data?.current && <Alert type="warning" title="暂不能提交品牌" description={submissionBlockedReason || '当前报告仍有资料或规则未完成检查，请品牌核对配置及审核结果。'} />}
    {data?.submissionNotice && <p role="note">{data.submissionNotice}</p>}
    <Space wrap><Button type={sidebar || submittingToBrand ? 'primary' : 'default'} icon={<CheckCheck size={14} />} loading={action.busy} disabled={!data?.writable || submissionBlocked || submitted || recognizing} onClick={respond}>{submittingToBrand ? submitted ? '已提交品牌' : '提交品牌' : data?.response?.action === 'modifying' ? '已收到，修改中' : '收到，我去修改'}</Button><Button icon={<MessageSquare size={14} />} type={sidebar || context.disputing ? 'primary' : 'default'} disabled={!data?.writable || action.busy || recognizing} onClick={() => context.setDisputing(true)}>我对 AI 有异议</Button>
      {onRecognize && (context.disputing || data?.response?.action === 'disputing' || open > 0) && <Button type="primary" loading={recognizing} disabled={!data?.writable || recognizeDisabled || action.busy} onClick={onRecognize}>重新识别</Button>}
    </Space>
    {context.disputing && <p>请选择有异议的规则填写理由。</p>}
    {!!open && <p>{open} 条异议待处理</p>}
    {action.error && <Alert type="error" title={action.error} />}
  </section>;
}

export function DisputeMessages({ dispute }: { dispute: Dispute }) {
  return <div className="dispute-messages">{dispute.messages.map(item => <article key={item.id} className={`dispute-message actor-${item.actor}`}><header><strong>{item.actor === 'creator' ? '达人说明' : '品牌回复'}</strong><small>{formatDate(item.created_at)}</small></header>{item.kind !== 'comment' && <Tag>{disputeLabels[item.kind as keyof typeof disputeLabels] || item.kind}</Tag>}{item.location && <p className="dispute-location">材料位置：{item.location}</p>}<p>{item.body}</p></article>)}</div>;
}

export function RuleDisputeButton({ ruleId }: { ruleId: string }) {
  const context = useContext(DiscussionContext), role = useDemoRole(), action = useDiscussionAction();
  const [open, setOpen] = useState(false), [message, setMessage] = useState(''), [location, setLocation] = useState('');
  if (!context) return null;
  const dispute = context.data?.disputes.find(d => d.rule_id === ruleId);
  if (role === 'creator' && !dispute && !context.disputing) return null;
  if (role !== 'creator' && !dispute) return null;
  const closed = !!dispute && ['accepted', 'rejected'].includes(dispute.status);
  const writable = role === 'creator' && context.data?.writable && !closed;
  function send() {
    const body = { scope: context!.scope, reviewId: context!.reviewId, ruleId, message, location, revision: dispute?.revision || 0 };
    void action.run(body, key => discussionsApi.dispute(context!.taskId, body, key), async () => { setMessage(''); setLocation(''); await context!.refresh(); });
  }
  return <div className="rule-dispute-action">
    {dispute && <Tag color={dispute.status === 'accepted' ? 'green' : dispute.status === 'rejected' ? 'default' : 'orange'}>{disputeLabels[dispute.status]}</Tag>}
    {(dispute || role === 'creator') && <Button type="text" size="small" icon={<MessageSquare size={12} />} disabled={!context.data} onClick={() => { context.setDisputing(true); setOpen(true); }}>{dispute ? '查看异议与回复' : '对这条提出异议'}</Button>}
    <Drawer title="这条规则的异议与裁决" open={open} onClose={() => setOpen(false)} size={520}>
      {dispute ? <><h3>第 {dispute.rule.ruleNumber || '?'} 条 · {dispute.rule.text}</h3><Tag>{disputeLabels[dispute.status]}</Tag><DisputeMessages dispute={dispute} /></> : <p>请填写异议理由和对应材料位置。</p>}
      {!context.data?.current && <Alert type="info" title="历史异议，仅供查看；当前版本需要单独核对" />}
      {writable && <div className="dispute-compose"><Input aria-label="异议材料位置" placeholder="位置（可选）：脚本第几行、视频几秒至几秒" maxLength={300} value={location} onChange={e => setLocation(e.target.value)} /><Input.TextArea aria-label="异议具体理由" placeholder="请说明实际提交了什么、AI 为什么判断有误，以及希望品牌核对的内容" value={message} onChange={e => setMessage(e.target.value)} maxLength={4000} showCount rows={6} /><Button type="primary" loading={action.busy} disabled={!message.trim()} onClick={send}>{dispute ? '补充说明并通知品牌' : '提交异议给品牌'}</Button></div>}
      {role === 'brand' && <Link to="/feedback?tab=disputes">前往异议处理后台 →</Link>}
      {action.error && <Alert type="error" title={action.error} />}
    </Drawer>
  </div>;
}

export function CreatorReminders({ taskId }: { taskId: string }) {
  const query = useQuery({ queryKey: ['creator-reminders', taskId], queryFn: () => discussionsApi.reminders(taskId), refetchInterval: 30000 });
  const [error, setError] = useState<string>();
  const unread = query.data?.data.filter(item => !item.read_at) || [];
  async function read(id: string) { try { await discussionsApi.read(taskId, id); await query.refetch(); } catch (e) { setError(e instanceof Error ? e.message : '确认失败'); } }
  return <>{unread.map(item => <Alert className="creator-reminder" key={item.id} type="warning" title={`品牌催办 · ${item.scope === 'script' ? '脚本' : '视频'}`} description={item.message} action={<Button aria-label="收到" size="small" onClick={() => void read(item.id)}>收到</Button>} />)}{error && <Alert type="error" title={error} />}{query.isError && <small>催办消息读取失败，<button onClick={() => void query.refetch()}>重试</button></small>}</>;
}
