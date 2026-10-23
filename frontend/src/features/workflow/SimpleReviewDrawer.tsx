import { useCallback, useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { Link, useBeforeUnload, useBlocker } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Input, Modal, Select, Skeleton, Space } from 'antd';
import { standardsApi, generationStrictnessOptions, generationStrictnessLabels, generationStrictnessHints, type GenerationStrictness, type StandardDraft } from './standardsApi';
import { decompositionModelApi } from './decompositionModelApi';
import { RuleCategoryButtons, type RuleCategory } from './RuleCategoryButtons';
import { ImageRuleEditor, defaultImageRules, textRulesValid } from './ImageRuleEditor';
import { projectApi } from '../projects/api';
import { projectBriefApi } from './projectBriefApi';
import type { ProjectDetail } from '../../shared/api/types';
import { TextRuleEditor } from './TextRuleEditor';
import { editableVideoRules, savedReviewLevel, type VideoReviewLevel } from './videoReviewSettings';
import { cleanVideoDraft, mergeVideoDraft } from './videoRuleDrafts';
import { RuleWarnings } from './RuleWarnings';
import { AiElapsed, useAiCallTiming } from '../../shared/ui/AiElapsed';
import { VideoLayersEditor, videoLayersError as videoRulesError, videoLayer } from './VideoLayersEditor';

type Props = { pageMode?: boolean; onCategoryChange?: (value: RuleCategory) => void; initialCategory?: RuleCategory; projectRulesRevision?: number; legacyCandidates?: import('./standardsApi').ProjectStandards['legacyCandidates']; project: ProjectDetail; open: boolean; onClose: () => void; taskId?: string; taskRevision?: number; initial?: { imageRules?: StandardDraft[]; scriptRules: StandardDraft[]; videoRules: StandardDraft[]; sourceVersionId?: string | null }; embedded?: boolean; onDirtyChange?: (dirty: boolean) => void };
// 脚本与图文规则没有三层配置：草稿里若残留 layer/组件（例如恢复历史视频候选），保存前必须去掉。
const withoutVideoLayers = (r: StandardDraft): StandardDraft => ({ ...clean(r), layer: undefined, baselineKey: undefined, presetKey: undefined, componentPlan: null, enabled: true });
const clean = (r: StandardDraft): StandardDraft => ({ text: r.text, criterion: r.criterion, category: r.category, importance: r.importance, sourceBlockId: r.sourceBlockId || '', sourceQuote: r.sourceQuote || '', origin: r.origin || 'manual', enabled: r.enabled !== false, params: r.params, ...(r.presetKey ? { presetKey: r.presetKey } : {}), ...(r.layer ? { layer: r.layer, enabled: r.enabled !== false, baselineKey: r.baselineKey } : {}), componentPlan: r.componentPlan ? { operator: r.componentPlan.operator, sameSegment: r.componentPlan.sameSegment, nodes: r.componentPlan.nodes.map(n => ({ id: n.id, kind: n.kind, parameters: n.parameters })) } : null });

export function SimpleReviewDrawer({ project, open, onClose, taskId, projectRulesRevision = 0, legacyCandidates = [], initial, initialCategory = 'script', pageMode = false, onCategoryChange, embedded = false, onDirtyChange, excludedVideoRuleTexts = [] }: Props & { excludedVideoRuleTexts?: string[] }) {
  const { message } = App.useApp(), cache = useQueryClient();
  const defaults = useQuery({ queryKey: ['script-rule-defaults'], queryFn: standardsApi.scriptDefaults, enabled: open });
  const videoDefaults = useQuery({ queryKey: ['video-rule-defaults'], queryFn: standardsApi.videoDefaults, enabled: open });
  const templates = useQuery({ queryKey: ['standard-templates'], queryFn: standardsApi.templates, enabled: open });
  const projectBrief = useQuery({ queryKey: ['project-brief', project.id], queryFn: () => projectBriefApi.get(project.id), enabled: open, refetchInterval: q => ['queued', 'vision', 'facts'].includes(q.state.data?.data.brief?.processing?.status || '') ? 2000 : false });
  const processing = projectBrief.data?.data.brief?.processing;
  const factsReady = !processing || processing.status === 'succeeded';
  const settings = useQuery({ queryKey: ['decomposition-model-settings'], queryFn: decompositionModelApi.settings, enabled: open });
  const history = useQuery({ queryKey: ['standard-recommendations', project.id], queryFn: () => standardsApi.history(project.id), enabled: open, refetchInterval: q => q.state.data?.data.some(r => r.status === 'processing') ? 2000 : false });

  const recoveryKey = useRef<{ id: string; key: string } | null>(null);
  const [scriptRules, setScriptRules] = useState<StandardDraft[]>([]), [videoRules, setVideoRules] = useState<StandardDraft[]>([]), [sourceId, setSourceId] = useState<string>();
  const [category, setCategory] = useState<RuleCategory>(initialCategory);
  const [strictnessByScope, setStrictnessByScope] = useState<Record<RuleCategory, GenerationStrictness>>({ script: 'lenient', video: 'lenient', image:'lenient' });
  const [generatedLevels, setGeneratedLevels] = useState<Partial<Record<RuleCategory, GenerationStrictness>>>({});
  const [generatedInput, setGeneratedInput] = useState<Partial<Record<RuleCategory, { mode: 'facts' | 'blocks'; pending: number }>>>({});
  const activeScope = category;
  const aiTiming = useAiCallTiming(`${project.id}:${activeScope}`);
  const timedRecommendation = history.data?.data.find(r => r.scope === activeScope && r.snapshot.sourceVersionId === projectBrief.data?.data.brief?.sourceVersionId);
  const strictness = strictnessByScope[activeScope];
  const previous = history.data?.data.find(r => r.scope === activeScope && (!r.snapshot.strictness || r.snapshot.strictness === strictness) && r.snapshot.sourceVersionId === projectBrief.data?.data.brief?.sourceVersionId && !!r.result?.modelResponse && ['succeeded', 'failed'].includes(r.status));
  const [videoReviewLevel, setVideoReviewLevel] = useState<VideoReviewLevel>('basic');
  const [scopeCleanupCount, setScopeCleanupCount] = useState(0);
  // AI 生成的视频规则直接追加到列表末尾；同文案不重复添加，量化检查需先补组件再启用。
  function appendVideoRules(generated: StandardDraft[], exclusions: string[] = []) {
    const incoming = generated.map(rule => { const value = clean(rule), layer = videoLayer(value); return { ...value, layer, enabled: layer !== 'precise' }; });
    const merged = mergeVideoDraft(videoRules, incoming, [...excludedVideoRuleTexts, ...exclusions]);
    setVideoRules(merged.rules);
    if (merged.removed) setScopeCleanupCount(count => count + merged.removed);
    return merged.added;
  }
  const [imageRules, setImageRules] = useState<StandardDraft[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>(), [warnings, setWarnings] = useState<string[]>([]);
  const initialized = useRef(false), lock = useRef(false), key = useRef<{ signature: string; id: string } | null>(null), aiKey = useRef<{ signature: string; id: string } | null>(null);

  const generation = useRef(0), baseline = useRef('');
  const draft = JSON.stringify({ script: scriptRules.map(clean), image: imageRules.map(clean), video: videoRules.map(clean), videoReviewLevel, source: sourceId || null });
  const dirty = !!baseline.current && draft !== baseline.current;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => pageMode && dirty && currentLocation.pathname !== nextLocation.pathname);
  useBeforeUnload(useCallback((event: BeforeUnloadEvent) => { if (pageMode && dirty) { event.preventDefault(); event.returnValue = ''; } }, [pageMode, dirty]));
  useEffect(() => { setCategory(initialCategory); }, [initialCategory]);
  useEffect(() => { generation.current++; return () => { generation.current++; }; }, [taskId, project.id]);
  useEffect(() => {
    if (!open) { initialized.current = false; return; }
    if (initialized.current || !defaults.data || !templates.data || !videoDefaults.data) return;
    initialized.current = true;
    setCategory(initialCategory); setImageRules((initial?.imageRules?.length ? initial.imageRules : defaultImageRules).map(clean));
    setScriptRules((initial?.scriptRules.length ? initial.scriptRules : templates.data.data[0].scriptRules).map(clean));
    const videoDraft = editableVideoRules(initial?.videoRules.length ? initial.videoRules : videoDefaults.data.data.rules).map(clean);
    const initialLevel = initial?.videoRules.length ? savedReviewLevel(initial.videoRules) : 'basic';
    const cleanedVideo = cleanVideoDraft(videoDraft, excludedVideoRuleTexts);
    setVideoRules(cleanedVideo.rules); setScopeCleanupCount(cleanedVideo.removed); setVideoReviewLevel(initialLevel);
    baseline.current = JSON.stringify({ script: (initial?.scriptRules.length ? initial.scriptRules : templates.data.data[0].scriptRules).map(clean), image: (initial?.imageRules?.length ? initial.imageRules : defaultImageRules).map(clean), video: videoDraft, videoReviewLevel: initialLevel, source: initial?.sourceVersionId || null });
    setSourceId(initial?.sourceVersionId || undefined); setError(undefined); setWarnings([]); key.current = null;
  }, [open, defaults.data, templates.data, videoDefaults.data, initial, project.platforms, initialCategory, excludedVideoRuleTexts]);
  useEffect(() => {
    if (!open || !initialized.current || !excludedVideoRuleTexts.length) return;
    const cleaned = cleanVideoDraft(videoRules, excludedVideoRuleTexts);
    if (!cleaned.removed) return;
    setVideoRules(cleaned.rules); setScopeCleanupCount(count => count + cleaned.removed);
  }, [open, videoRules, excludedVideoRuleTexts]);
  useEffect(() => { if (initialized.current && onDirtyChange) onDirtyChange(JSON.stringify(scriptRules.map(clean)) !== JSON.stringify((initial?.scriptRules.length ? initial.scriptRules : templates.data?.data[0].scriptRules || []).map(clean))); }, [scriptRules, initial, templates.data, onDirtyChange]);
  async function generate() {
    if (lock.current || !factsReady || !settings.data?.data.hasKey || !projectBrief.data?.data.brief) return;
    const scope = category;
    aiTiming.start();
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    try {
      const source = projectBrief.data!.data.brief!.sourceVersionId;
      if (at !== generation.current) return;
      const body = { sourceVersionId: source!, scope, videoRuleMode: scope === 'video' ? 'layered' as const : 'legacy' as const, strictness: strictnessByScope[scope], configRevision: settings.data!.data.revision, allowRealCall: true, outputFormat: 'structured' as const };
      const signature = JSON.stringify(body);
      if (aiKey.current?.signature !== signature) aiKey.current = { signature, id: crypto.randomUUID() };
      const r = await standardsApi.recommend(project.id, body, aiKey.current!.id);
      aiTiming.finish(r.data.status, r.data);
      aiKey.current = null;
      void cache.invalidateQueries({ queryKey: ['standard-recommendations', project.id] });
      if (at !== generation.current) return;
      const generated = r.data.result?.report?.rules;
      if (r.data.status !== 'succeeded' || !generated || (scope !== 'video' && !generated.length)) throw new Error(r.data.errorMessage || '未生成可用的逐条规则');
      if (scope === 'video' && !generated.length) { appendVideoRules([], r.data.snapshot.excludedExistingVideoRuleTexts); setWarnings(r.data.result?.report?.warnings || []); message.info('本次没有新增适用的视频成片要求，现有规则保留'); return; }
      setGeneratedInput(current => ({ ...current, [scope]: { mode: r.data.snapshot.inputMode || 'blocks', pending: r.data.snapshot.factsPendingCount || 0 } }));
      setSourceId(source); if (scope === 'video') { const added = appendVideoRules(generated, r.data.snapshot.excludedExistingVideoRuleTexts); message.success(`已追加 ${added} 条视频规则，核对后启用并保存`); } else if (scope === 'image') setImageRules(generated.map(withoutVideoLayers)); else setScriptRules(generated.map(clean)); setWarnings(r.data.result?.report?.warnings || []); setGeneratedLevels(current => ({ ...current, [scope]: body.strictness }));
      if (scope !== 'video') message.success('已根据 Brief 生成规则，可直接编辑后保存');
    } catch (e) { aiTiming.finish('failed'); const text = e instanceof Error ? e.message : '生成失败'; if (at === generation.current) { setError(text); message.error(text); } }
    finally { lock.current = false; if (at === generation.current) setBusy(false); }
  }
  async function recover() {
    if (lock.current || !previous) return;
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    if (recoveryKey.current?.id !== previous.id) recoveryKey.current = { id: previous.id, key: crypto.randomUUID() };
    try {
      const r = await standardsApi.revalidate(project.id, previous.id, recoveryKey.current.key);
      recoveryKey.current = null;
      if (at !== generation.current) return;
      const generated = r.data.result?.report?.rules;
      if (!generated || (r.data.scope !== 'video' && !generated.length)) throw new Error('上次没有可恢复的规则');
      if (r.data.scope === 'video' && !generated.length) { appendVideoRules([], r.data.snapshot.excludedExistingVideoRuleTexts); setWarnings(r.data.result?.report?.warnings || []); message.info('本次没有新增适用的视频成片要求，现有规则保留'); return; }
      if (r.data.snapshot.strictness) setGeneratedLevels(current => ({ ...current, [r.data.scope]: r.data.snapshot.strictness }));
      setSourceId(r.data.snapshot.sourceVersionId); if (r.data.scope === 'video') { const added = appendVideoRules(generated, r.data.snapshot.excludedExistingVideoRuleTexts); message.success(`已追加上次生成的 ${added} 条视频规则，核对后启用并保存`); } else if (r.data.scope === 'image') setImageRules(generated.map(withoutVideoLayers)); else setScriptRules(generated.map(clean)); setWarnings(r.data.result?.report?.warnings || []);
      void cache.invalidateQueries({ queryKey: ['standard-recommendations', project.id] });
      if (r.data.scope !== 'video') message.success('已恢复上次返回的规则，未调用AI；请核对后保存');
    } catch (e) { if (at === generation.current) setError(e instanceof Error ? e.message : '恢复失败'); }
    finally { lock.current = false; if (at === generation.current) setBusy(false); }
  }
  async function save() {
    if (lock.current || !textRulesValid(imageRules) || !scriptRules.length || scriptRules.some(r => !r.text.trim() || !r.criterion.trim()) || !scriptRules.some(r => r.importance === 'must') || videoRulesError(videoRules, videoReviewLevel)) return;
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    const common = { videoReviewLevel, scriptRules: scriptRules.map(r => ({ ...withoutVideoLayers(r), category: r.category === '脚本审核规则' ? '脚本检查' : r.category })), videoRules: videoRules.map(rule => clean({ ...rule, layer: videoLayer(rule), enabled: rule.enabled !== false })), imageRules: imageRules.map(withoutVideoLayers), scriptConfirmed: true, videoConfirmed: true, confirmationNote: '品牌保存逐条审核规则，仅用于平台内审核', sourceVersionId: sourceId || null };
    const body = { ...common, revision: projectRulesRevision };
    const signature = JSON.stringify(body);
    if (key.current?.signature !== signature) key.current = { signature, id: crypto.randomUUID() };
    try {
      await standardsApi.confirmProject(project.id, body, key.current!.id);
      key.current = null; baseline.current = draft; message.success('项目规则已保存');
      await Promise.all([cache.invalidateQueries({ queryKey: ['project-standards', project.id] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['workflow'] }), cache.invalidateQueries({ queryKey: ['task'] }), cache.invalidateQueries({ queryKey: ['project-tasks', project.id] }), cache.invalidateQueries({ queryKey: ['project', project.id] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] })]);
      if (at === generation.current) { onClose(); }
    } catch (e) { if (at === generation.current) { setError(e instanceof Error ? e.message : '保存失败'); } }
    finally { lock.current = false; if (at === generation.current) setBusy(false); }
  }
  const controls = <Space><Button aria-label="保存项目审核规则" loading={busy} type="primary" disabled={busy || !textRulesValid(imageRules) || !scriptRules.length || scriptRules.some(r => !r.text.trim() || !r.criterion.trim()) || !scriptRules.some(r => r.importance === 'must') || !!videoRulesError(videoRules, videoReviewLevel) || project.status === 'archived'} onClick={() => void save()}>保存项目审核规则</Button></Space>;
  const content = defaults.isPending || templates.isPending || videoDefaults.isPending ? <Skeleton active /> : videoDefaults.isError ? <Alert type="error" title="基础规则读取失败" /> : <>
    {category !== 'script' && <p className="muted">规则供本项目所有达人共用，修改后需重新验收。</p>}
    {!!legacyCandidates.length && <Select aria-label="历史规则候选" style={{ width: '100%', marginBottom: 14 }} placeholder="可选：从历史达人规则载入草稿，再确认为项目规则" options={legacyCandidates.map(c => ({ value: c.taskId, label: c.creatorName + '的历史规则' }))} onChange={id => { const c = legacyCandidates.find(item => item.taskId === id)!; setScriptRules(c.scriptRules.map(clean)); setVideoRules(c.videoRules.map(clean)); setSourceId(c.sourceVersionId || undefined); }} />}

    <RuleCategoryButtons active={category} counts={{ script: scriptRules.length, image: imageRules.length, video: videoRules.length }} disabled={busy} onSelect={value => { setCategory(value); onCategoryChange?.(value); }} />
        <section className="script-generation-card" aria-label={`AI 生成${category === 'script' ? '脚本' : category === 'image' ? '图文' : '视频'}规则`}>
          <div className="script-generation-heading"><span className="script-generation-icon"><Sparkles size={25} /></span><div><h3>AI 生成规则</h3><p>基于项目 Brief，自动生成贴合品牌要求的审核规则。可在生成后进行编辑和调整。</p></div></div>
          <div className="script-generation-controls"><Space>{category !== 'video' && <Select aria-label="AI生成规则严格程度" value={strictness} disabled={busy || project.status === 'archived'} onChange={value => setStrictnessByScope(current => ({ ...current, [activeScope]: value }))} options={generationStrictnessOptions} />}<Button type="primary" icon={<Sparkles size={16} />} loading={busy} disabled={!factsReady || busy || project.status === 'archived' || !settings.data?.data.hasKey || !projectBrief.data?.data.brief} onClick={() => void generate()}>AI 生成规则</Button></Space><small>{category === 'video' ? '生成适用于视频的候选要求，核对启用状态后保存。' : generationStrictnessHints[strictness]}</small><AiElapsed run={aiTiming.run || timedRecommendation} /></div>
        </section>
    {category === 'script' && <>
        <div className="script-generation-context"><span>{projectBrief.data?.data.brief ? `当前项目 Brief：${projectBrief.data.data.brief.title}` : '项目尚未上传 Brief'}</span><Link to={`/projects/${project.id}`}>上传或更新 Brief</Link>{previous && <Button size="small" type="text" disabled={busy || project.status === 'archived'} onClick={() => void recover()}>恢复上次生成（不调用AI）</Button>}{!settings.data?.data.hasKey && <Link to="/settings/models">配置 Brief 解构模型与 Key</Link>}<details><summary>生成说明</summary><p>生成后核对下方规则并保存。AI 调用可能计费。档位只影响下一次生成。{generatedLevels[activeScope] && `当前草稿：${generationStrictnessLabels[generatedLevels[activeScope]!]}。`}{generatedInput[activeScope]?.pending ? `${generatedInput[activeScope]!.pending} 项待核对事实未纳入。` : ''}</p><details><summary>默认系统提示词</summary><pre style={{whiteSpace:'pre-wrap',maxHeight:240,overflow:'auto'}}>{defaults.data?.data.systemPrompt.content}</pre></details></details></div>
        <RuleWarnings warnings={warnings} />
        <TextRuleEditor rules={scriptRules} onChange={setScriptRules} disabled={busy || project.status === 'archived'} />
        <Space wrap style={{ marginBottom: 16 }}><Button disabled={busy} onClick={() => setScriptRules(templates.data!.data[0].scriptRules.map(clean))}>恢复默认规则</Button></Space>
      </>}
    {category === 'image' && <><div className="script-generation-context"><span>{projectBrief.data?.data.brief ? `当前项目 Brief：${projectBrief.data.data.brief.title}` : '项目尚未上传 Brief'}</span><Link to={`/projects/${project.id}`}>上传或更新 Brief</Link>{previous && <Button size="small" type="text" disabled={busy} onClick={() => void recover()}>恢复上次图文候选（不调用AI）</Button>}{!settings.data?.data.hasKey && <Link to="/settings/models">配置 Brief 解构模型与 Key</Link>}<span>生成后核对并保存，AI 调用可能计费。</span></div><RuleWarnings warnings={warnings} /><ImageRuleEditor rules={imageRules} onChange={setImageRules} disabled={busy || project.status === 'archived'} /></>}
    {category === 'video' && <>
      <div className="script-generation-context"><Link to={`/projects/${project.id}`}>上传或更新 Brief</Link></div>
      {scopeCleanupCount > 0 && <p className="muted" role="status">已从草稿移除 {scopeCleanupCount} 条非视频旧AI规则，保存后生效。</p>}
      {previous && <Button disabled={busy || project.status === 'archived'} onClick={() => void recover()}>恢复上次视频候选（不调用AI）</Button>}
      {!settings.data?.data.hasKey && <Link to="/settings/models">配置 Brief 解构模型与 Key</Link>}
      <RuleWarnings warnings={warnings} />
      <VideoLayersEditor projectId={project.id} rules={videoRules} defaults={videoDefaults.data?.data.rules || []} reviewLevel={videoReviewLevel} onReviewLevelChange={setVideoReviewLevel} onChange={setVideoRules} disabled={busy || project.status === 'archived'} onUploadBusy={value => { lock.current = value; setBusy(value); }} />
    </>}
    {error && <Alert type="error" title={error} style={{ marginTop: 14 }} />}

  </>;
  const footer = <div className="project-rules-save-bar"><span className="muted">{dirty ? '有未保存的修改' : '规则按项目统一维护'} · 必须项需全部通过</span><Space>{pageMode && <Link to={`/projects/${project.id}`}>返回项目</Link>}{controls}</Space></div>;
  const leavePrompt = <Modal title="还有未保存的规则" open={blocker.state === 'blocked'} okText="离开并放弃修改" cancelText="继续编辑" onOk={() => { if (blocker.state === 'blocked') blocker.proceed(); }} onCancel={() => { if (blocker.state === 'blocked') blocker.reset(); }}>离开页面会丢失本次修改，可以先保存项目规则。</Modal>;
  return embedded ? <Card title="项目审核规则" className="project-rules-form">{content}{footer}{leavePrompt}</Card> : <section className="project-rules-form">{content}{footer}{leavePrompt}</section>;
}

export function InlineScriptRules({ taskId, projectId, onDirtyChange }: { taskId: string; projectId: string; onDirtyChange?: (dirty: boolean) => void }) {
  const query = useQuery({ queryKey: ['project-standards', projectId], queryFn: () => standardsApi.projectStandards(projectId) });
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => projectApi.detail(projectId) });
  const v = query.data?.data;
  if (!v || !project.data || v.projectId !== projectId) return <Skeleton active />;
  const initial = v.script && v.video ? { imageRules: v.image?.rules, scriptRules: v.script.rules, videoRules: v.video.rules, sourceVersionId: v.script.source_version_id } : undefined;
  return <SimpleReviewDrawer key={v.revision} project={project.data.data} taskId={taskId} projectRulesRevision={v.revision} initial={initial} open embedded onDirtyChange={onDirtyChange} onClose={() => {}} />;
}
