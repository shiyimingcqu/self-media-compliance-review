import { ScriptReviewAside } from './ScriptReviewAside';
import { ReviewDiscussionProvider, CreatorReminders } from '../workflow/ReviewDiscussion';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Drawer, Empty, Input, Modal, Radio, Skeleton, Tag, Tooltip } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { creatorApi } from './api';
import { workflowApi } from '../workflow/api';
import { scriptApi } from '../brief-script/api';
import { evidenceApi } from '../video-model/api';
import { uploadFile, fileContentUrl } from '../../shared/api/client';
import { ErrorState } from '../../shared/ui';
import { ScriptComments } from './ScriptComments';
import { ScriptPreview } from './ScriptPreview';
import { scriptAnnotations } from './scriptAnnotations';
import { ReviewConnections } from './ReviewConnections';
import { ContentReviewNav } from '../workflow/ContentReviewNav';
import { AiElapsed, useAiCallTiming } from '../../shared/ui/AiElapsed';
import './creator.css';

export function CreatorTaskPage() {
  const { taskId = '', projectId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const aiTiming = useAiCallTiming(taskId);
  const { message } = App.useApp(), cache = useQueryClient();
  const tasks = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks });
  const task = tasks.data?.data.find(t => t.id === taskId && t.project_id === projectId);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>();
  const query = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), enabled: !!task, refetchInterval: q => busy || q.state.data?.data.scriptReview?.status === 'processing' ? 2000 : false });
  const settings = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings, enabled: !!task });
  const [mode, setMode] = useState<'file' | 'text'>('file'), [text, setText] = useState(''), [file, setFile] = useState<File>();
  const [submissionOpen, setSubmissionOpen] = useState(false), [activeComment, setActiveComment] = useState<{ id: string; sequence: number }>();
  const [historyOpen, setHistoryOpen] = useState(false);
  const reviewGrid = useRef<HTMLDivElement>(null);
  const generation = useRef(0), lock = useRef(false), upload = useRef<{ key: string; fileId?: string } | null>(null), operation = useRef<{ signature: string; key: string } | null>(null);
  const recheckOperation = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => { generation.current++; lock.current = false; setBusy(false); setText(''); setFile(undefined); setError(undefined); upload.current = null; operation.current = null; return () => { generation.current++; }; }, [taskId, projectId]);
  const view = query.data?.data;
  useEffect(() => {
    if (view?.writable && params.get('action') === 'submit') {
      setSubmissionOpen(true);
      const next = new URLSearchParams(params); next.delete('action'); setParams(next, { replace: true });
    }
  }, [params, view?.writable, setParams]);
  useEffect(() => { if (view && !view.currentScript && !view.scriptReview) setSubmissionOpen(true); }, [taskId, !!view]);
  const annotations = useMemo(() => scriptAnnotations(view?.currentScript || null, view?.scriptReview || null), [view?.currentScript, view?.scriptReview]);
  const selectComment = (id: string) => setActiveComment({ id, sequence: Date.now() });
  const reviewing = busy || view?.scriptReview?.status === 'processing';
  const canSubmit = !!task && view?.writable && view.rulesReady && settings.data?.data.hasKey && !reviewing;
  async function recheck() {
    if (!canSubmit || lock.current || !view?.currentScript || view.currentScript.extractionStatus !== 'ready') return;
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    const body = { scriptVersionId: view.currentScript.scriptVersionId, contextKey: view.contextKey, configRevision: settings.data!.data.revision, allowRealCall: true }, signature = JSON.stringify(body);
    if (recheckOperation.current?.signature !== signature) recheckOperation.current = { signature, key: crypto.randomUUID() };
    try {
      aiTiming.start();
      const result = await workflowApi.analyze(taskId, body, recheckOperation.current.key);
      aiTiming.finish(result.data.status, result.data);
      recheckOperation.current = null;
      await Promise.all([cache.invalidateQueries({ queryKey: ['workflow', taskId] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] }), cache.invalidateQueries({ queryKey: ['project-tasks', projectId] })]);
      if (at === generation.current) {
        if (result.data.status === 'succeeded') message.success('已按当前规则重新验收，脚本文字和可读取的内嵌图片一起送审');
        else setError(result.data.errorMessage || '本次验收尚未完成，请查看审核详情');
      }
    } catch (e) { aiTiming.finish('failed'); if (at === generation.current) setError(e instanceof Error ? e.message : '重新验收失败'); }
    finally { if (at === generation.current) { lock.current = false; setBusy(false); } }
  }
  async function submit() {
    if (!canSubmit || lock.current || (mode === 'file' ? !file : !text.trim() || text.length > 50000)) return;
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    try {
      let fileId: string | undefined;
      if (mode === 'file' && file) {
        if (file.size > 20 * 1024 * 1024) throw new Error('脚本文件请控制在20MB以内');
        const flow = upload.current ||= { key: crypto.randomUUID() };
        if (!flow.fileId) flow.fileId = (await uploadFile(file, 'script', flow.key)).data.id;
        fileId = flow.fileId;
      }
      if (at !== generation.current) return;
      const body = { inputType: mode, text: mode === 'text' ? text : null, fileId: mode === 'file' ? fileId : null, configRevision: settings.data!.data.revision, allowRealCall: true };
      const signature = JSON.stringify(body);
      if (operation.current?.signature !== signature) operation.current = { signature, key: crypto.randomUUID() };
      aiTiming.start();
      const result = await scriptApi.submitAndReview(taskId, body, operation.current!.key);
      aiTiming.finish(result.data.autoReview?.status || 'failed');
      if (result.data.autoReview) operation.current = null;
      await Promise.all([cache.invalidateQueries({ queryKey: ['workflow', taskId] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] }), cache.invalidateQueries({ queryKey: ['project-tasks', projectId] })]);
      if (at !== generation.current) return;
      setSubmissionOpen(false);
      if (result.data.autoReviewError) setError(result.data.autoReviewError);
      else message.success('AI验收反馈已更新');
    } catch (e) { aiTiming.finish('failed'); if (at === generation.current) setError(e instanceof Error ? e.message : '提交失败，请重试'); }
    finally { if (at === generation.current) { lock.current = false; setBusy(false); } }
  }
  if (tasks.isPending) return <Skeleton active />;
  if (tasks.isError) return <ErrorState error={tasks.error} retry={() => tasks.refetch()} />;
  if (!task) return <Empty description="没有找到对应的合作任务，请返回我的合作选择" />;
  return <ReviewDiscussionProvider taskId={taskId} scope="script" reviewId={view?.scriptReview?.id}><div className="creator-workspace creator-script-workspace creator-script-three-column">
    <div className="creator-review-commandbar"><div className="creator-review-identity"><Link to="/creator">← 我的合作</Link><strong title={`${task.project_name} · ${task.creator_name}`}>{task.project_name}</strong><span>{task.creator_name}</span><Tag color="cyan">{view?.scriptApproval ? '品牌已通过' : view?.scriptSubmitted ? '待品牌复核' : view?.scriptSubmissionReady ? '待提交品牌' : 'AI 脚本验收'}</Tag><ContentReviewNav projectId={projectId} taskId={taskId} active="script" compact /></div><div className="creator-review-actions"><span className="creator-brief-label" title={task.briefTitle}>{task.briefTitle}</span><Button size="small" onClick={() => setHistoryOpen(true)}>验收历史</Button>{view?.currentScript && <Tooltip title="使用当前品牌规则重新审核现有脚本及内嵌图片，会调用AI，可能计费；不会重复创建脚本版本。"><Button size="small" loading={reviewing} disabled={!canSubmit || view.currentScript.extractionStatus !== 'ready'} onClick={() => void recheck()}>按当前规则重新验收</Button></Tooltip>}<Button size="small" type="primary" onClick={() => setSubmissionOpen(true)}>{view?.currentScript ? '上传新脚本' : '上传脚本'}</Button></div></div>
    <Drawer title={view?.currentScript ? '提交修改后的脚本' : '提交脚本'} open={submissionOpen} onClose={() => setSubmissionOpen(false)} size={620}>
      <div className="creator-submission-content">
        <div className="brief-reference"><strong>对应 Brief</strong><p>{task.briefTitle}</p>{task.briefFileId && <a href={fileContentUrl(task.briefFileId)} target="_blank" rel="noreferrer">查看审核参考文件 ↗</a>}</div>
        <Radio.Group value={mode} onChange={e => { setMode(e.target.value); setError(undefined); }} disabled={busy} options={[{ value: 'file', label: '上传脚本' }, { value: 'text', label: '粘贴脚本' }]} />
        {mode === 'file' ? <div className="creator-file-upload"><UploadOutlined /><strong>选择脚本文件</strong><input aria-label="上传对应Brief的脚本" type="file" accept=".txt,.doc,.docx,.xls,.xlsx,.pdf" disabled={busy} onChange={e => { setFile(e.target.files?.[0]); upload.current = null; operation.current = null; }} /><small>{file?.name || 'TXT / Word / Excel / PDF，最大20MB'}</small></div> : <Input.TextArea aria-label="达人脚本正文" value={text} onChange={e => setText(e.target.value)} rows={10} maxLength={50000} showCount disabled={busy} style={{ marginTop: 16, marginBottom: 24 }} placeholder="粘贴对应 Brief 的脚本内容" />}
        
        <Button type="primary" block loading={reviewing} disabled={!canSubmit || (mode === 'file' ? !file : !text.trim())} onClick={() => void submit()}>{reviewing ? 'AI 正在验收…' : '提交脚本并进行AI验收'}</Button>
        {!settings.data?.data.hasKey && <p className="muted">品牌尚未配置AI验收服务，请联系品牌后再提交。</p>}
        {view && !view.rulesReady && <Alert type="info" title="品牌尚未完成此任务的审核规则设置" />}
        {error && <Alert type="error" title={error} style={{ marginTop: 14 }} />}
      </div>
    </Drawer>
    <CreatorReminders taskId={taskId} /><div ref={reviewGrid} className="creator-submit-grid creator-review-grid creator-comment-grid">
      <Card title="脚本解析预览" className="creator-reader-card" extra={view?.currentScript && <Tag>{view.currentScript.versionLabel}</Tag>}>
        {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <ScriptPreview taskId={taskId} script={view?.currentScript || null} review={view?.scriptReview || null} annotations={annotations} active={activeComment} onSelect={selectComment} reviewing={reviewing} outdatedReview={!!view?.currentScript && !view.scriptReview && view.scriptReviews.some(r => r.scriptVersionId === view.currentScript!.scriptVersionId)} />}
      </Card>
      <Card title="审核结果" className="creator-reader-card creator-feedback-card" extra={view?.currentScript && <Tag>{view.currentScript.versionLabel}</Tag>}>
        {error && !submissionOpen && <Alert type="error" title={error} />}
        {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : view ? <>
          {busy && view.scriptReview?.status !== 'processing' ? <><div className="ai-timing-row"><AiElapsed run={aiTiming.run} /></div><Alert showIcon type="info" title="AI 正在验收" /></> : <ScriptComments taskId={taskId} timing={aiTiming.run} review={view.scriptReview} acceptance={view.scriptAcceptance} annotations={annotations} active={activeComment} onSelect={selectComment} onRefresh={() => void query.refetch()} refreshing={query.isFetching} />}
        </> : null}
      </Card>
      <ScriptReviewAside task={task} view={view} annotations={annotations} reviewing={reviewing} />
      <ReviewConnections container={reviewGrid} activeId={activeComment?.id} revision={`${view?.currentScript?.scriptVersionId}:${view?.scriptReview?.id}`} />
    </div>
    <Modal title="提交与验收历史" open={historyOpen} onCancel={() => setHistoryOpen(false)} footer={null}>{view?.scriptReviews.length ? view.scriptReviews.map(r => <p key={r.id}><Tag>{r.acceptance?.passed ? '必须项通过' : '待修改 / 未完成'}</Tag>{r.result?.report?.summary || r.errorMessage || '验收中'}</p>) : <Empty description="暂无验收历史" />}</Modal>
  </div></ReviewDiscussionProvider>;
}
