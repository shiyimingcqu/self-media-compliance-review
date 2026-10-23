import { anchorsForFilter, reviewAnchors } from '../reviewPresentation';
import { AiElapsed, useAiCallTiming } from '../../../shared/ui/AiElapsed';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Drawer, Input, Modal, Select, Space, Tag } from 'antd';
import { uploadFile, fileContentUrl } from '../../../shared/api/client';
import { artifactUrl as artifactUrlForAnchor, evidenceApi, preparedVideoUrl } from '../api';
import type { RunInput, Preparation } from '../api';
import { ErrorState } from '../../../shared/ui';
import { creatorApi } from '../../creator/api';
import { ReviewPlayer, type EvidenceAnchor } from './ReviewPlayer';
import { RunResult, runLabels, findingDomId } from './RunResult';
import { formatDate, formatVideoTime } from '../../../shared/lib/format';
import '../evidence.css';

export function EvidenceConsole({ taskId, disabled = false, compact = false, reviewActions, onDisagree }: { taskId?: string; disabled?: boolean; compact?: boolean; reviewActions?: (runId?: string) => ReactNode; onDisagree?: (note: string) => void }) {
  const aiTiming = useAiCallTiming(taskId || 'video-test');
  const { message, modal } = App.useApp(), cache = useQueryClient();
  const player = useRef<HTMLVideoElement>(null), busy = useRef(false);
  const [selectedIssue, setSelectedIssue] = useState<string>();
  // 右侧分类筛选联动左侧时间轴：未通过只留红条、待确定只留黄条、已通过只留绿条。
  const [anchorFilter, setAnchorFilter] = useState('failed');
  const [hoverFinding, setHoverFinding] = useState<string>();
  const playbackEnd = useRef<number | undefined>(undefined);
  const highlightTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(highlightTimer.current), []);
  const taskDetails = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks, enabled: !!taskId && compact });
  const taskDetail = taskDetails.data?.data.find(task => task.id === taskId);
  const [params, setParams] = useSearchParams();
  const [uploaded, setUploaded] = useState<{ id: string; name: string }>();
  const [preset, setPreset] = useState<'balanced' | 'compact' | 'original'>('balanced');
  const [preparation, setPreparation] = useState<Preparation>();
  const [uploading, setUploading] = useState(false), [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();
  const [uploadOpen, setUploadOpen] = useState(false), [historyOpen, setHistoryOpen] = useState(false);
  const [testRule, setTestRule] = useState('观察视频中出现的主体、可见文字和可听口播；不确定时标待确认，不捏造。');
  const request = useRef<{ signature: string; key: string } | null>(null);
  const settings = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings });
  const runs = useQuery({ queryKey: ['evidence-runs', taskId || 'tests'], queryFn: () => evidenceApi.list(taskId), refetchInterval: q => q.state.data?.data.some(r => ['queued', 'processing'].includes(r.status)) ? 2000 : false });
  const active = runs.data?.data.find(r => r.id === params.get('run')) || runs.data?.data[0];
  useEffect(() => {
    if (taskId && active?.id) {
      void cache.invalidateQueries({ queryKey: ['workflow', taskId] });
      if (active.status === 'succeeded') {
        void cache.invalidateQueries({ queryKey: ['workflow-review-queue'] });
        void cache.invalidateQueries({ queryKey: ['task', taskId] });
        void cache.invalidateQueries({ queryKey: ['project-tasks'] });
      }
    }
  }, [active?.id, active?.status, taskId, cache]);
  const canSend = !!preparation;
  const running = runs.data?.data.some(r => ['queued', 'processing'].includes(r.status));
  const choose = (id: string) => { const next = new URLSearchParams(params); next.set('run', id); setParams(next, { replace: true }); };

  async function pick(file?: File) {
    if (!file || busy.current || uploading || starting) return;
    if (!file.name.toLowerCase().endsWith('.mp4')) { message.error('请选择完整MP4视频；上传后在本地生成审核副本'); return; }
    setUploading(true); setError(undefined); setPreparation(undefined);
    try { const r = await uploadFile(file, 'video', crypto.randomUUID()); setUploaded({ id: r.data.id, name: r.data.originalName }); request.current = null; const prepared = await evidenceApi.prepare(r.data.id, preset); setPreparation(prepared.data); message.success('本地视频准备完成，尚未发送给模型'); }
    catch (e) { setError(e instanceof Error ? e.message : '上传失败'); }
    finally { setUploading(false); }
  }
  async function changePreset(value: 'balanced' | 'compact' | 'original') {
    setPreset(value); setPreparation(undefined); request.current = null;
    if (!uploaded) return;
    setUploading(true); setError(undefined);
    try { const r = await evidenceApi.prepare(uploaded.id, value); setPreparation(r.data); }
    catch (e) { setError(e instanceof Error ? e.message : '视频准备失败'); }
    finally { setUploading(false); }
  }
  function start() {
    if (busy.current || !uploaded || !canSend || !settings.data?.data.hasKey) return;
    busy.current = true;
    modal.confirm({ title: '确认发送完整视频进行真实调用？', content: '将完整审核副本与规则发送至所选服务；千问服务会先上传副本至其临时存储，再通过文件引用审核。原片留在本机用于取证；最多四次模型请求，可能计费，不自动重试。', okText: '确认真实调用', cancelText: '取消', onCancel: () => { busy.current = false; },
      onOk: async () => {
        setStarting(true); setError(undefined); aiTiming.start();
        const payload: RunInput = { kind: 'video', configRevision: settings.data!.data.revision, allowRealCall: true, fileId: uploaded.id, taskId, testRule, compressionPreset: preset };
        const signature = JSON.stringify(payload);
        if (request.current?.signature !== signature) request.current = { signature, key: crypto.randomUUID() };
        try { const r = await evidenceApi.start(payload, request.current!.key); aiTiming.finish(r.data.status, r.data); choose(r.data.id); setUploadOpen(false); await cache.invalidateQueries({ queryKey: ['evidence-runs', taskId || 'tests'] }); request.current = null; }
        catch (e) { aiTiming.finish('failed'); setError(e instanceof Error ? e.message : '提交失败'); }
        finally { setStarting(false); busy.current = false; }
      },
    });
  }
  // 查看历史结果时播放其对应原件，避免把证据定位到新上传的视频。
  const src = active?.fileId || uploaded?.id;
  const asObject = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const asList = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(asObject) : [];
  const result = asObject(active?.result), media = asObject(result.media), findings = asList(asObject(result.report).findings), artifacts = asList(result.artifacts), rules = asList(active?.snapshot.rules);
  const previewInfo = asObject(asObject(result.preparation).original);
  const activePreparation = active ? previewInfo : asObject(preparation?.original);
  const allAnchors: EvidenceAnchor[] = reviewAnchors(active?.result, active?.snapshot).map(anchor => ({ ...anchor, thumbnail: anchor.artifactId && active ? artifactUrlForAnchor(active.id, anchor.artifactId) : undefined }));
  const anchors = anchorsForFilter(allAnchors, anchorFilter);
  useEffect(() => { playbackEnd.current = undefined; }, [src]);
  useEffect(() => {
    const video = player.current;
    if (!video) return;
    const stopAtEnd = () => { if (playbackEnd.current != null && video.currentTime >= playbackEnd.current) { video.pause(); video.currentTime = playbackEnd.current; playbackEnd.current = undefined; } };
    video.addEventListener('timeupdate', stopAtEnd);
    return () => video.removeEventListener('timeupdate', stopAtEnd);
  }, [src]);
  function seekTo(ms: number, issueId?: string, endMs?: number) {
    playbackEnd.current = endMs != null && endMs > ms ? endMs / 1000 : undefined;
    if (player.current) {
      player.current.currentTime = Math.max(0, ms / 1000);
      if (playbackEnd.current != null) void player.current.play().catch(() => message.info('请点击播放器继续播放该片段'));
    }
    if (issueId && active) {
      setSelectedIssue(issueId); window.clearTimeout(highlightTimer.current);
      window.requestAnimationFrame(() => document.getElementById(findingDomId(active.id, issueId))?.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
      highlightTimer.current = window.setTimeout(() => setSelectedIssue(undefined), 1500);
    }
  }
  const totals = { pass: 0, manual: 0, fail: 0, advisory: 0 };
  for (const finding of findings) { const rule = rules.find(rule => rule.id === finding.rule_id); if (rule?.importance === 'suggested' || finding.verdict === 'not_applicable') totals.advisory++; else if (finding.verdict === 'compliant') totals.pass++; else if (finding.verdict === 'non_compliant') totals.fail++; else totals.manual++; }

  const uploadControls = <>    <Alert showIcon type="info" title={taskId ? '按品牌视频规则进行审查' : '独立能力测试，不改变业务审核状态'} description={taskId ? '可在脚本通过前上传验收；有已通过脚本时进行对照，没有时脚本匹配项待确认。脚本或规则变更后需重新验收，原片用于取证。' : '自行上传完整短视频，确认后发送；测试结果不证明全片覆盖率。'} />
    <Select aria-label="视频压缩档位" value={preset} style={{ width: '100%', marginTop: 16 }} disabled={uploading || starting || running || disabled} onChange={value => void changePreset(value)} options={[{ value: 'balanced', label: '均衡压缩 · 长边最多1280px' }, { value: 'compact', label: '紧凑压缩 · 长边最多960px' }, { value: 'original', label: '使用原片 · 不压缩' }]} />
    <div className="evidence-upload"><label htmlFor="evidence-video-input">选择完整 MP4 视频</label><input id="evidence-video-input" type="file" aria-label="上传完整测试视频" accept="video/mp4,.mp4" disabled={uploading || starting || running || disabled} onChange={e => { void pick(e.target.files?.[0]); e.target.value = ''; }} /><small>{uploading ? '正在上传 / 本地压缩，请稍候（不产生模型费用）…' : uploaded?.name || '上传后生成全片副本，原片保留；无截断、无主动降帧'}</small></div>
    {preparation && <Alert style={{ marginBottom: 16 }} showIcon type={canSend ? 'success' : 'warning'} title={`${(preparation.originalBytes / 1_000_000).toFixed(2)} MB → ${(preparation.preparedBytes / 1_000_000).toFixed(2)} MB`} description={<>{Number(preparation.original.width)}×{Number(preparation.original.height)} → {Number(preparation.prepared.width)}×{Number(preparation.prepared.height)}；保留完整时长与音轨，证据从原片生成。<br />原片与发送副本均不设平台大小上限，最终是否接受以模型服务返回为准。压缩不保证 token 同比减少，字幕或 Logo 不清可改用原片。</>} />}
    {preparation && uploaded && <details style={{ marginBottom: 16 }}><summary style={{ cursor: 'pointer', color: 'var(--accent)' }}>预览将发送给模型的完整副本</summary><video controls preload="none" key={`${uploaded.id}-${preset}`} src={preparedVideoUrl(uploaded.id, preset)} style={{ width: '100%', maxHeight: 360, marginTop: 10 }} /></details>}
    {!taskId && <div style={{ marginTop: 16 }}><label htmlFor="video-test-rule">本次测试规则</label><Input.TextArea id="video-test-rule" value={testRule} onChange={e => setTestRule(e.target.value)} rows={3} maxLength={1000} showCount /></div>}
    {settings.isError && <ErrorState error={settings.error} retry={() => settings.refetch()} />}
    {settings.data && !settings.data.data.hasKey && <Alert style={{ marginTop: 16 }} type="warning" showIcon title="尚未配置 API Key" description={<Link to="/settings/models">去模型设置保存 Key</Link>} />}
    {error && <Alert style={{ marginTop: 16 }} type="error" showIcon title={error} />}
    <div className="ai-timing-row"><Button style={{ marginTop: 20 }} type="primary" loading={starting || uploading} disabled={disabled || !uploaded || !canSend || !settings.data?.data.hasKey || running || !testRule.trim()} onClick={start}>真实调用并收集证据</Button><AiElapsed run={starting || aiTiming.run?.status === 'failed' ? aiTiming.run : active} /></div>
</>;
  return <>
    {compact && <div className="media-review-toolbar"><Space size={8} wrap><Tag>{active ? runLabels[active.status] : '尚未提交视频'}</Tag><small>完整原片 · 截图与片段可定位播放</small></Space><Space size={6}><Button size="small" onClick={() => setHistoryOpen(true)}>调用历史{runs.data?.data.length ? ` · ${runs.data.data.length}` : ''}</Button><Button size="small" type="primary" disabled={disabled || running} onClick={() => setUploadOpen(true)}>上传 / 验收视频</Button></Space></div>}
    <div className={`review-grid video-layout${compact ? ' media-review-grid desk-evidence-grid' : ''}`}><Card className="video-material-card" title={taskId ? '完整视频审核与取证' : '视频真实调用测试'} extra={<Tag>原片不限大小 · 10分钟内</Tag>}>
    {!compact && uploadControls}
    <ReviewPlayer src={src ? fileContentUrl(src) : ''} player={player} anchors={anchors} durationHint={Number(media.durationMs || activePreparation.durationMs || 0) / 1000} fps={Number(activePreparation.fps) || undefined} onSeek={seekTo} hoverId={hoverFinding} filter={anchorFilter} />{!src && compact && <Button className="desk-player-upload" disabled={disabled} onClick={() => setUploadOpen(true)}>上传完整视频</Button>}
  </Card><Card title="模型反馈与证据" extra={<Button type="text" onClick={() => runs.refetch()}>刷新记录</Button>}>
    {runs.isError ? <ErrorState error={runs.error} retry={() => runs.refetch()} /> : <>
      {!compact && !!runs.data?.data.length && <Select aria-label="取证调用历史" style={{ width: '100%', marginBottom: 18 }} value={active?.id} onChange={choose} options={runs.data.data.map(r => ({ value: r.id, label: `${r.kind === 'connection' ? '文本测试' : '视频取证'} · ${runLabels[r.status]} · ${formatDate(r.createdAt)}` }))} />}
      {active ? <RunResult key={active.id} run={active} seek={seekTo} selectedIssue={selectedIssue} onDisagree={onDisagree} onFilterChange={setAnchorFilter} onHoverFinding={setHoverFinding} /> : <div className="document-placeholder"><h3>等待一次显式测试</h3><p>这里将显示模型反馈、截图、有声片段和请求用量。</p></div>}
    </>}
  </Card>{compact && <aside className="desk-review-aside"><section className="desk-video-info"><header><h3>视频信息</h3><span className="mono">{active ? active.id.slice(0, 8).toUpperCase() : '—'}</span></header><dl><dt>达人</dt><dd>{taskDetail?.creator_name || '—'}</dd><dt>活动</dt><dd>{taskDetail?.project_name || '—'}</dd><dt>品牌</dt><dd>{taskDetail?.brand_name || '—'}</dd><dt>平台</dt><dd>{taskDetail ? ({ douyin: '抖音', xiaohongshu: '小红书', bilibili: 'B站' }[taskDetail.platform]) : '—'}</dd><dt>提交时间</dt><dd className="mono">{active ? formatDate(active.createdAt) : '—'}</dd><dt>时长</dt><dd className="mono">{media.durationMs ? formatVideoTime(Number(media.durationMs)) : '—'}</dd><dt>画幅</dt><dd className="mono">{activePreparation.width ? `${activePreparation.width} × ${activePreparation.height}` : '—'}</dd></dl></section><section className="desk-overall"><h3>整体结论</h3><div><span className="state-pass"><strong>{String(totals.pass).padStart(2, '0')}</strong>通过</span><span className="state-uncertain"><strong>{String(totals.manual).padStart(2, '0')}</strong>需人工</span><span className="state-fail"><strong>{String(totals.fail).padStart(2, '0')}</strong>不通过</span></div><small>AI 逐项结果 · 参考项 {totals.advisory} 条</small></section><div className="desk-review-actions">{reviewActions ? reviewActions(active?.id) : <div className="review-responsibility">AI 判定仅供参考，最终由品牌确认。</div>}</div></aside>}</div>
    {compact && <><Drawer title="提交完整视频进行验收" open={uploadOpen} onClose={() => { if (!uploading && !starting) setUploadOpen(false); }} size={620}>{uploadControls}</Drawer><Modal title="视频验收调用历史" open={historyOpen} onCancel={() => setHistoryOpen(false)} footer={null}><div className="media-history-list">{runs.data?.data.length ? runs.data.data.map(r => <Button key={r.id} block type={r.id === active?.id ? 'primary' : 'default'} onClick={() => { choose(r.id); setHistoryOpen(false); }}>{runLabels[r.status]} · {formatDate(r.createdAt)}</Button>) : <p>暂无调用记录</p>}</div></Modal></>}
  </>;
}
