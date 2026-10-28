import { AiElapsed, useAiCallTiming } from '../../shared/ui/AiElapsed';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Skeleton, Space, Tag } from 'antd';
import { FileTextOutlined, UploadOutlined } from '@ant-design/icons';
import type { ProjectDetail } from '../../shared/api/types';
import { uploadFile } from '../../shared/api/client';
import { projectBriefApi } from './projectBriefApi';
import { decompositionModelApi } from './decompositionModelApi';
import { evidenceApi } from '../video-model/api';
import './project-brief.css';
import { briefProgressLabel } from './briefProgress';

export function ProjectBriefPanel({ project }: { project: ProjectDetail }) {
  const query = useQuery({ queryKey: ['project-brief', project.id], queryFn: () => projectBriefApi.get(project.id), refetchInterval: q => ['queued', 'processing'].includes(q.state.data?.data.brief?.visualRun?.status || '') || ['queued', 'vision', 'facts'].includes(q.state.data?.data.brief?.processing?.status || '') ? 2000 : false });
  const factsQuery = useQuery({ queryKey: ['project-brief-facts', project.id], queryFn: () => projectBriefApi.facts(project.id), refetchInterval: q => q.state.data?.data.lastRun?.status === 'processing' ? 2000 : false });
  const settings = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings });
  const decomposition = useQuery({ queryKey: ['decomposition-model-settings'], queryFn: () => decompositionModelApi.settings() });
  const cache = useQueryClient(), { message } = App.useApp();
  const [file, setFile] = useState<File>(), [busy, setBusy] = useState(false), [error, setError] = useState<string>();
  const navigate = useNavigate();
  const manualTiming = useAiCallTiming(project.id);
  const reviewPath = `/projects/${project.id}/brief-review`;
  const visionOperation = useRef<{ signature: string; key: string } | null>(null);
  const lock = useRef(false), operation = useRef<{ uploadKey: string; parseKey: string; fileId?: string; revision: number } | null>(null);
  const brief = query.data?.data.brief;
  const processing = brief?.processing;
  const autoRunning = ['queued', 'vision', 'facts'].includes(processing?.status || '');
  const processingLabels: Record<string, string> = { queued: '已排队，等待自动处理', vision: '正在解析图文并生成事实卡', facts: '正在自动提取事实卡', succeeded: '事实卡已就绪，规则生成将自动引用', blocked: '自动处理待配置', failed: '自动处理未完成', interrupted: '自动处理已暂停', superseded: 'Brief 已更新，本次处理已停止' };
  const partial = brief?.pageChecks ? brief.pageChecks.length > 0 : !!brief?.uncoveredSources.length;
  const run = brief?.visualRun;
  const recognizing = ['queued', 'processing'].includes(run?.status || '');
  const progressLabel = briefProgressLabel(run);
  const appliedVision = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!brief?.visionRunId || appliedVision.current === brief.visionRunId) return;
    appliedVision.current = brief.visionRunId;
    for (const queryKey of [['project-brief-facts', project.id], ['project', project.id], ['project-standards', project.id], ['project-tasks', project.id], ['workflow'], ['task'], ['creator-tasks'], ['workflow-review-queue']]) void cache.invalidateQueries({ queryKey });
  }, [brief?.visionRunId, project.id, cache]);
  useEffect(() => { void cache.invalidateQueries({ queryKey: ['project-brief-facts', project.id] }); }, [processing?.status, brief?.sourceVersionId, cache, project.id]);
  async function continueProcessing() {
    if (!brief || busy) return;
    setBusy(true); setError(undefined);
    try { await projectBriefApi.process(project.id, brief.sourceVersionId, crypto.randomUUID()); await query.refetch(); }
    catch (e) { setError(e instanceof Error ? e.message : '自动处理启动失败'); }
    finally { setBusy(false); }
  }
  const uncoveredPages = new Set((brief?.uncoveredSources || []).filter((item): item is string => typeof item === 'string' && /^page:\d+$/.test(item))).size;
  async function recognize() {
    if (!brief || !settings.data?.data.hasKey || lock.current || recognizing) return;
    lock.current = true; setBusy(true); setError(undefined);
    const body = { sourceVersionId: brief.sourceVersionId, briefRevision: brief.revision, configRevision: settings.data.data.revision, allowRealCall: true, extractFacts: true, forceReprocess: !!brief.visionRunId }, signature = JSON.stringify(body);
    if (visionOperation.current?.signature !== signature) visionOperation.current = { signature, key: crypto.randomUUID() };
    try {
      manualTiming.start();
      const result = await projectBriefApi.vision(project.id, body, visionOperation.current!.key);
      manualTiming.finish(result.data.status, result.data);
      visionOperation.current = null;
      cache.setQueryData(['project-brief', project.id], (old: { data: import('./projectBriefApi').ProjectBriefView } | undefined) => old?.data.brief ? { ...old, data: { ...old.data, brief: { ...old.data.brief, visualRun: result.data } } } : old);
      await query.refetch();
      message.success(result.data.status === 'succeeded' ? '已复用本文件的图文解析结果' : 'AI 图文解析已开始，完成后自动更新');
    } catch (e) { manualTiming.finish('failed'); setError(e instanceof Error ? e.message : '图文解析请求失败'); }
    finally { lock.current = false; setBusy(false); }
  }
  async function parse() {
    if (!file || lock.current || !query.data) return;
    lock.current = true; setBusy(true); setError(undefined);
    operation.current ||= { uploadKey: crypto.randomUUID(), parseKey: crypto.randomUUID(), revision: query.data.data.revision };
    try {
      const flow = operation.current;
      if (!flow.fileId) flow.fileId = (await uploadFile(file, 'brief', flow.uploadKey)).data.id;
      const result = (await projectBriefApi.parse(project.id, { fileId: flow.fileId, title: file.name.slice(0, 100), revision: flow.revision, autoProcess: true }, flow.parseKey)).data;
      if (result.registration.extractionStatus !== 'ready') throw new Error(result.registration.error?.message || 'Brief 未提取到可读内容，原有 Brief 保留，请检查文件后重新上传');
      setFile(undefined); operation.current = null; message.success(result.brief?.processing ? 'Brief 已上传，后台将自动解析图文并提取事实卡' : '项目 Brief 已上传并解析');
      await Promise.all([['project-brief-facts', project.id], ['project-brief', project.id], ['project', project.id], ['project-tasks', project.id], ['workflow'], ['task'], ['creator-tasks'], ['workflow-review-queue']].map(queryKey => cache.invalidateQueries({ queryKey })));
    } catch (e) { if (e && typeof e === 'object' && 'code' in e && e.code === 'REVISION_CONFLICT') { operation.current = null; void query.refetch(); } setError(e instanceof Error ? e.message : '上传或解析失败'); }
    finally { lock.current = false; setBusy(false); }
  }
  const facts = factsQuery.data?.data.sourceVersionId === brief?.sourceVersionId ? factsQuery.data?.data.facts : undefined;
  const factsRun = factsQuery.data?.data.lastRun;
  const factsLock = useRef(false), factsOperation = useRef<{ signature: string; key: string } | null>(null);
  async function extractFacts() {
    if (brief?.pageCount) { await recognize(); return; }
    if (!brief || !decomposition.data?.data.hasKey || factsLock.current) return;
    factsLock.current = true; setBusy(true); setError(undefined);
    const body = { sourceVersionId: brief.sourceVersionId, configRevision: decomposition.data.data.revision, allowRealCall: true }, signature = JSON.stringify(body);
    if (factsOperation.current?.signature !== signature) factsOperation.current = { signature, key: crypto.randomUUID() };
    try {
      manualTiming.start();
      const response = await projectBriefApi.extractFacts(project.id, body, factsOperation.current!.key);
      manualTiming.finish(response.data.status, response.data);
      factsOperation.current = null;
      await factsQuery.refetch();
      message.success('Brief 事实卡已生成，生成脚本/视频规则时会自动参考');
    } catch (e) { manualTiming.finish('failed'); setError(e instanceof Error ? e.message : '事实卡解构失败'); await factsQuery.refetch(); }
    finally { factsLock.current = false; setBusy(false); }
  }
  const factCount = facts ? (facts.brand?.value ? 1 : 0) + [facts.products, facts.competitors, facts.sellingPoints, facts.requiredTerms, facts.forbiddenTerms, facts.publishRules, facts.thresholds, facts.freeform].reduce((sum, items) => sum + (items?.length || 0), 0) : 0;
  const pendingCount = (factsRun?.result?.pendingFacts?.length || 0) + (brief?.pageChecks?.length || 0);
  return <Card id="project-brief" className="panel project-brief-panel brief-overview-card" title={<span className="project-section-title"><FileTextOutlined />Brief 资料</span>}>
    {query.isPending ? <Skeleton active paragraph={{ rows: 2 }} /> : query.isError ? <Alert type="error" title="Brief 读取失败" action={<Button onClick={() => void query.refetch()}>重试</Button>} /> : brief ? <>
      <div className="brief-document-card"><div className="project-brief-summary"><FileTextOutlined /><strong title={brief.title}>{brief.title}</strong><Tag color={recognizing ? 'blue' : partial ? 'orange' : 'green'}>{recognizing ? 'AI 图文解析中' : partial ? '有局部提示' : brief.uncoveredSources.length ? '已核对' : brief.visionRunId ? 'AI 图文已解析' : '解析完成'} · V{brief.revision}</Tag><AiElapsed run={manualTiming.run && (busy || manualTiming.run.status === 'failed') ? manualTiming.run : processing || run || factsRun} /></div>
      <div className="brief-overview-counts"><span>{brief.pageCount ? `${brief.pageCount} 页原文` : `${brief.blocks.length} 段原文`}</span><span>{facts ? `${factCount} 条有效事实` : '事实卡未就绪'}</span>{!!pendingCount && <Link to={reviewPath + '?tab=pending'}>{pendingCount} 条待核对</Link>}{brief.updatedAt && <span>更新于 {new Date(brief.updatedAt).toLocaleDateString('zh-CN')}</span>}</div></div>
      
      {processing && <div className={`brief-overview-state ${['failed', 'blocked', 'interrupted'].includes(processing.status) ? 'has-warning' : ''}`}><span title={processing.errorMessage || processingLabels[processing.status]}>{processingLabels[processing.status]}</span>{!autoRunning && !['succeeded', 'superseded'].includes(processing.status) && <Button size="small" loading={busy} disabled={busy || project.status === 'archived'} onClick={() => void continueProcessing()}>继续自动处理</Button>}</div>}
      {partial && !autoRunning && <div className="brief-overview-notice">{brief.pageChecks?.length || uncoveredPages ? `${brief.pageChecks?.length || uncoveredPages} 页有局部提示` : '部分资料有待核对项'} · <Link to={reviewPath + '?tab=pending'}>查看待核对</Link></div>}
      <Space className="brief-vision-actions" wrap><Button size="small" loading={recognizing || busy} disabled={!(brief.pageCount ? settings.data?.data.hasKey : decomposition.data?.data.hasKey) || recognizing || autoRunning || busy || project.status === 'archived'} onClick={() => void extractFacts()}>{brief.pageCount ? brief.visionRunId ? '重新图文识别并提取事实' : 'AI 图文解析' : 'AI 提取 Brief 事实卡'}</Button></Space>
      {progressLabel && <div className="brief-overview-state" role="status">{progressLabel}</div>}
      {(processing?.status === 'blocked' || (!processing && !(brief.pageCount ? settings.data?.data.hasKey : decomposition.data?.data.hasKey))) && <Link to="/settings/models">配置模型 Key</Link>}
    </> : <div className="project-brief-empty"><FileTextOutlined /><span>还没有项目 Brief，请先上传品牌要求。</span></div>}
    <div className="brief-primary-actions">{brief && <Button className="brief-view-action" onClick={() => navigate(reviewPath)}>查看解析内容</Button>}<div className="project-brief-upload"><label className="project-brief-select"><UploadOutlined /> {file ? file.name : brief ? '选择更新后的 Brief' : '选择 Brief 文件'}<input aria-label="上传项目Brief" type="file" accept=".pdf,.txt" disabled={busy || project.status === 'archived'} onChange={e => { setFile(e.target.files?.[0]); operation.current = null; setError(undefined); e.target.value = ''; }} /></label><Button type="primary" loading={busy} disabled={!file || !query.data || project.status === 'archived'} onClick={() => void parse()}>上传并解析</Button></div></div>
    {error && <Alert type="error" title={error} style={{ marginTop: 8 }} />}
  </Card>;
}
