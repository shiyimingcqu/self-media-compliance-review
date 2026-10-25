import { AiElapsed, useAiCallTiming } from '../../shared/ui/AiElapsed';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Checkbox, Drawer, Input, Select, Skeleton, Space, Tabs, Tag } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { standardsApi, generationStrictnessOptions, type GenerationStrictness, type StandardDraft, type Recommendation } from './standardsApi';
import { decompositionModelApi } from './decompositionModelApi';
import { projectApi } from '../projects/api';
import type { Platform, ProjectDetail } from '../../shared/api/types';
import { platformNames } from '../../shared/lib/format';
import { ApiError } from '../../shared/api/client';
import { ProjectRulesButton } from './ProjectRulesPanel';
import { SimpleReviewDrawer } from './SimpleReviewDrawer';
import { WarningList } from './RuleWarnings';

const emptyRule = (): StandardDraft => ({ text: '', criterion: '', category: '内容', importance: 'must', sourceBlockId: '', sourceQuote: '', enabled: true, origin: 'manual' });

type Props = { project: ProjectDetail; open: boolean; onClose: () => void; taskId?: string; taskRevision?: number; initial?: { scriptRules: StandardDraft[]; videoRules: StandardDraft[]; sourceVersionId?: string | null } };

export function DetailedStandardsDrawer({ project, open, onClose, taskId, taskRevision, initial }: Props) {
  const aiTiming = useAiCallTiming(project.id);
  const { modal, message } = App.useApp(), cache = useQueryClient();
  const templates = useQuery({ queryKey: ['standard-templates'], queryFn: standardsApi.templates, enabled: open });
  const settings = useQuery({ queryKey: ['decomposition-model-settings'], queryFn: decompositionModelApi.settings, enabled: open });
  const history = useQuery({ queryKey: ['standard-recommendations', project.id], queryFn: () => standardsApi.history(project.id), enabled: open });
  const [strictness, setStrictness] = useState<GenerationStrictness>('lenient');
  const [creatorName, setCreatorName] = useState(''), [platform, setPlatform] = useState<Platform>(project.platforms[0]), [phase, setPhase] = useState<'general' | 'seeding' | 'warmup'>('general');
  const [scriptRules, setScriptRules] = useState<StandardDraft[]>([]), [videoRules, setVideoRules] = useState<StandardDraft[]>([]);
  const [sourceId, setSourceId] = useState<string>(), [templateId, setTemplateId] = useState('general'), [tab, setTab] = useState('script');
  const [scriptConfirmed, setScriptConfirmed] = useState(false), [videoConfirmed, setVideoConfirmed] = useState(false), [note, setNote] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>(), [recommendation, setRecommendation] = useState<Recommendation>();
  const lock = useRef(false), initialized = useRef(false), operation = useRef<{ signature: string; key: string } | null>(null);
  const aiOperation = useRef<{ signature: string; key: string } | null>(null);
  const generation = useRef(0);
  useEffect(() => { generation.current++; return () => { generation.current++; }; }, [project.id, taskId]);
  useEffect(() => {
    if (!open) { initialized.current = false; return; }
    if (initialized.current || !templates.data) return;
    initialized.current = true;
    const first = templates.data.data[0];
    setCreatorName(''); setPlatform(project.platforms[0]); setPhase('general'); setTemplateId(first?.id || 'general');
    setScriptRules(initial?.scriptRules || first?.scriptRules || []); setVideoRules(initial?.videoRules || first?.videoRules || []);
    setSourceId(initial?.sourceVersionId || undefined); setScriptConfirmed(false); setVideoConfirmed(false); setNote(''); setError(undefined); setRecommendation(undefined); operation.current = null;
  }, [open, templates.data, initial, project.platforms]);
  useEffect(() => { setScriptConfirmed(false); setVideoConfirmed(false); }, [platform, phase]);
  function changeRules(scope: string, next: StandardDraft[]) {
    if (scope === 'script') { setScriptRules(next); setScriptConfirmed(false); } else { setVideoRules(next); setVideoConfirmed(false); }
  }
  function loadTemplate() {
    const item = templates.data?.data.find(t => t.id === templateId);
    if (!item) return;
    modal.confirm({ title: '用推荐模板替换两套编辑内容？', content: '模板是通用起点，请补充品牌具体阈值并分别确认。', onOk: () => { setScriptRules(item.scriptRules); setVideoRules(item.videoRules); setSourceId(undefined); setScriptConfirmed(false); setVideoConfirmed(false); } });
  }
  function recommend(scope: 'script' | 'video') {
    if (!sourceId || !settings.data?.data.hasKey || lock.current) return;
    const body = { sourceVersionId: sourceId, scope, videoRuleMode: 'legacy' as const, strictness, configRevision: settings.data.data.revision, allowRealCall: true, outputFormat: 'structured' as const };
    lock.current = true;
    modal.confirm({ title: `确认用 AI 推荐${scope === 'script' ? '脚本' : '视频'}标准？`, content: '一次真实文本模型请求，将所选 Brief 的可读文本发送至已保存服务，可能计费；推荐不会自动启用，也不会改动另一套标准。', okText: '确认生成推荐', onCancel: () => { lock.current = false; }, onOk: async () => {
      setBusy(true); setError(undefined); const at = generation.current;
      const signature = JSON.stringify(body);
      if (aiOperation.current?.signature !== signature) aiOperation.current = { signature, key: crypto.randomUUID() };
      try { aiTiming.start(); const r = await standardsApi.recommend(project.id, body, aiOperation.current!.key); aiTiming.finish(r.data.status, r.data); aiOperation.current = null; if (at !== generation.current) return; setRecommendation(r.data); setTab(scope); await cache.invalidateQueries({ queryKey: ['standard-recommendations', project.id] }); if (r.data.status !== 'succeeded') setError(r.data.errorMessage || '推荐未完成'); }
      catch (e) { aiTiming.finish('failed'); if (at === generation.current) setError(e instanceof Error ? e.message : '推荐失败'); }
      finally { lock.current = false; if (at === generation.current) setBusy(false); }
    } });
  }
  async function publish(acknowledgeDuplicate = false) {
    if (lock.current || !scriptConfirmed || !videoConfirmed || !note.trim()) return;
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    const clean = (rules: StandardDraft[]) => rules.map(r => ({ text: r.text, criterion: r.criterion, category: r.category, importance: r.importance, sourceBlockId: r.sourceBlockId || '', sourceQuote: r.sourceQuote || '', origin: r.origin || 'manual', enabled: r.enabled !== false }));
    const common = { scriptRules: clean(scriptRules), videoRules: clean(videoRules), sourceVersionId: sourceId || null, scriptConfirmed, videoConfirmed, confirmationNote: note.trim() };
    const body = taskId ? { ...common, taskRevision: taskRevision! } : { ...common, creatorName: creatorName.trim(), platform, campaignPhase: phase, acknowledgeDuplicate };
    const signature = JSON.stringify(body);
    if (operation.current?.signature !== signature) operation.current = { signature, key: crypto.randomUUID() };
    let duplicate = false;
    try {
      if (taskId) await standardsApi.confirm(taskId, body as Parameters<typeof standardsApi.confirm>[1], operation.current!.key);
      else await standardsApi.publish(project.id, body as Parameters<typeof standardsApi.publish>[1], operation.current!.key);
      operation.current = null;
      await Promise.all([cache.invalidateQueries({ queryKey: ['project-tasks', project.id] }), cache.invalidateQueries({ queryKey: ['project', project.id] }), cache.invalidateQueries({ queryKey: ['workflow'] }), cache.invalidateQueries({ queryKey: ['task'] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] })]);
      if (at === generation.current) { message.success(taskId ? '两套标准已重新确认，需重新审核脚本' : '任务已发布，两套审核标准已冻结'); onClose(); }
    } catch (e) { if (at === generation.current) { if (e instanceof ApiError && e.code === 'DUPLICATE_SUSPECTED') duplicate = true; else setError(e instanceof Error ? e.message : '发布失败'); } }
    finally { lock.current = false; if (at === generation.current) setBusy(false); }
    if (duplicate) modal.confirm({ title: '同名达人任务已存在，继续发布独立任务？', onOk: () => publish(true) });
  }
  const valid = (rules: StandardDraft[]) => rules.length > 0 && rules.some(r => r.importance === 'must' && r.enabled !== false) && rules.every(r => r.text.trim() && r.criterion.trim());
  const renderRules = (scope: 'script' | 'video', rules: StandardDraft[]) => <div>
    <Alert type="info" showIcon title={scope === 'script' ? '脚本标准：检查台词、故事结构与镜头安排' : '视频标准：检查实际画面、口播、字幕、音轨与脚本执行'} description="两套标准独立编辑与确认。要求和判定口径都需要填写；修改任何条目后须重新勾选本套确认。" />
    <div className="standards-rule-list">{rules.map((rule, i) => <Card size="small" key={i} style={{ marginTop: 12 }} title={`标准 ${i + 1}`} extra={<Button aria-label={`删除${scope}标准${i + 1}`} type="text" danger icon={<DeleteOutlined />} disabled={busy || rules.length <= 1} onClick={() => changeRules(scope, rules.filter((_, n) => n !== i))} />}>
      <Space style={{ marginBottom: 10 }}><Input disabled={busy} aria-label={`${scope}标准${i + 1}类型`} value={rule.category} maxLength={50} onChange={e => changeRules(scope, rules.map((r, n) => n === i ? { ...r, category: e.target.value } : r))} /><Select disabled={busy} aria-label={`${scope}标准${i + 1}重要性`} value={rule.importance} onChange={value => changeRules(scope, rules.map((r, n) => n === i ? { ...r, importance: value } : r))} options={[{ value: 'must', label: '必须' }, { value: 'suggested', label: '建议' }]} /><Tag>{rule.origin === 'ai' ? 'AI 推荐' : rule.origin === 'template' ? '模板' : '人工'}</Tag></Space>
      <Input.TextArea aria-label={`${scope}标准${i + 1}要求`} placeholder="审核要求" value={rule.text} maxLength={2000} rows={2} disabled={busy} onChange={e => changeRules(scope, rules.map((r, n) => n === i ? { ...r, text: e.target.value } : r))} />
      <Input.TextArea style={{ marginTop: 8 }} aria-label={`${scope}标准${i + 1}判定口径`} placeholder="判定口径：检查什么、如何判定、需要什么证据、何时待确认" value={rule.criterion} maxLength={3000} rows={2} disabled={busy} onChange={e => changeRules(scope, rules.map((r, n) => n === i ? { ...r, criterion: e.target.value } : r))} />
      {rule.sourceQuote && <blockquote style={{ margin: '10px 0 0', color: 'var(--text-secondary)' }}>Brief 原文：{rule.sourceQuote}</blockquote>}
    </Card>)}</div>
    <Button style={{ marginTop: 12 }} icon={<PlusOutlined />} disabled={busy || rules.length >= 60} onClick={() => changeRules(scope, [...rules, emptyRule()])}>添加{scope === 'script' ? '脚本' : '视频'}标准</Button>
    <Checkbox style={{ display: 'flex', alignItems: 'flex-start', marginTop: 20 }} checked={scope === 'script' ? scriptConfirmed : videoConfirmed} disabled={busy || !valid(rules)} onChange={e => scope === 'script' ? setScriptConfirmed(e.target.checked) : setVideoConfirmed(e.target.checked)}>品牌已逐条审核并确认这套{scope === 'script' ? '脚本检查' : '视频审查'}标准</Checkbox>
  </div>;
  return <Drawer title={taskId ? '品牌审核两套标准 · 重新确认' : '发布达人任务 · 品牌审核标准'} open={open} width={1000} onClose={() => { if (!busy && !lock.current) onClose(); }} destroyOnHidden={false} footer={<Space style={{ width: '100%', justifyContent: 'flex-end' }}><Tag color={scriptConfirmed ? 'green' : 'default'}>脚本{scriptConfirmed ? '已确认' : '待确认'}</Tag><Tag color={videoConfirmed ? 'green' : 'default'}>视频{videoConfirmed ? '已确认' : '待确认'}</Tag><Button type="primary" loading={busy} disabled={project.status === 'archived' || (!taskId && !creatorName.trim()) || !scriptConfirmed || !videoConfirmed || !valid(scriptRules) || !valid(videoRules) || !note.trim()} onClick={() => void publish()}>{taskId ? '确认两套标准并重新审核' : '确认标准并发布任务'}</Button></Space>}>
    {templates.isPending ? <Skeleton active /> : <>
      {taskId && <Alert type="warning" title="重新确认后，当前脚本和视频的旧通过资格失效，历史保留可查。" style={{ marginBottom: 16 }} />}
      {!taskId && <Space wrap style={{ marginBottom: 18 }}><Input aria-label="达人名称" placeholder="达人名称" value={creatorName} maxLength={50} onChange={e => setCreatorName(e.target.value)} disabled={busy} /><Select aria-label="投放平台" value={platform} style={{ width: 140 }} onChange={setPlatform} options={project.platforms.map(p => ({ value: p, label: platformNames[p] }))} disabled={busy} /><Select aria-label="活动阶段" value={phase} onChange={setPhase} options={[{ value: 'general', label: '通用' }, { value: 'warmup', label: '预热期' }, { value: 'seeding', label: '种草期' }]} disabled={busy} /></Space>}
      <Card size="small" title="标准来源与推荐模板" style={{ marginBottom: 16 }}><Space wrap><Select aria-label="推荐标准模板" style={{ width: 180 }} value={templateId} onChange={setTemplateId} options={templates.data?.data.map(t => ({ value: t.id, label: t.name }))} /><Button onClick={loadTemplate} disabled={busy}>应用模板到两套标准</Button><Link to={`/projects/${project.id}/briefs`}>上传 / 解析 Brief →</Link></Space>
        <Select aria-label="用于AI推荐的Brief" style={{ width: '100%', marginTop: 14 }} placeholder="可选：选择已解析 Brief，生成 AI 推荐审核标准" value={sourceId} allowClear onChange={value => { setSourceId(value); setScriptConfirmed(false); setVideoConfirmed(false); }} disabled={busy} options={project.briefs.filter(b => b.latestSourceVersionId && b.extractionStatus === 'ready').map(b => ({ value: b.latestSourceVersionId!, label: b.title }))} />
        <div className="ai-timing-row"><Space wrap style={{ marginTop: 12 }}><Select aria-label="AI生成规则严格程度" value={strictness} disabled={busy} options={generationStrictnessOptions} style={{ minWidth: 210 }} /><Button disabled={busy || !sourceId || !settings.data?.data.hasKey} onClick={() => recommend('script')}>AI 推荐脚本标准</Button><Button disabled={busy || !sourceId || !settings.data?.data.hasKey} onClick={() => recommend('video')}>AI 推荐视频标准</Button><Link to="/settings/models">模型与提示词</Link></Space><AiElapsed run={aiTiming.run || recommendation} /></div>
        
        {recommendation?.result?.report && <Alert type="info" title={`${recommendation.scope === 'script' ? '脚本' : '视频'}标准推荐已生成，尚未应用`} description={<><WarningList warnings={recommendation.result.report.warnings} empty="请核对引用与判定口径。"/>{recommendation.result.report.rules.map((r, i) => <p key={i}>{i + 1}. {r.text}</p>)}<Button disabled={busy || recommendation.snapshot.sourceVersionId !== sourceId} onClick={() => { changeRules(recommendation.scope, recommendation.result!.report!.rules); setTab(recommendation.scope); }}>应用到{recommendation.scope === 'script' ? '脚本' : '视频'}标准</Button></>} />}
        {recommendation && <details style={{ marginTop: 12 }}><summary>本次推荐提示词版本与调用信息</summary><p>提示词 {recommendation.snapshot.prompt.key} · {recommendation.snapshot.prompt.version} · 输入 {recommendation.snapshot.inputMode === 'facts' ? '仅事实卡' : '事实卡 + Brief 正文'}</p><p>请求 ID：{recommendation.result?.providerRequestIds?.join(' / ') || '未返回'} · 用量：{JSON.stringify(recommendation.result?.usage || [])}</p>{recommendation.result?.modelResponse && <pre style={{ whiteSpace: 'pre-wrap' }}>{recommendation.result.modelResponse}</pre>}</details>}
        {!!history.data?.data.length && <details style={{ marginTop: 12 }}><summary>查看 AI 推荐记录</summary>{history.data.data.map(r => <p key={r.id}><Tag>{r.scope === 'script' ? '脚本' : '视频'}</Tag>{r.status} · 提示词 {r.snapshot.prompt.version}<Button type="link" disabled={busy} onClick={() => { aiTiming.reset(); setRecommendation(r); setError(r.errorMessage); }}>查看</Button></p>)}</details>}
      </Card>
      <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'script', label: `脚本检查标准（${scriptRules.length}）${scriptConfirmed ? ' ✓' : ''}`, children: renderRules('script', scriptRules) }, { key: 'video', label: `视频审查标准（${videoRules.length}）${videoConfirmed ? ' ✓' : ''}`, children: renderRules('video', videoRules) }]} />
      <Input.TextArea style={{ marginTop: 18 }} aria-label="标准确认意见" placeholder="品牌发布确认意见（必填）" value={note} onChange={e => setNote(e.target.value)} maxLength={2000} rows={2} disabled={busy} />
      {error && <Alert type="error" title={error} style={{ marginTop: 16 }} />}
      {templates.isError && <Alert type="error" title="模板读取失败，请刷新" />}
    </>}
  </Drawer>;
}

export function TaskStandardsEditor({ projectId, taskId, taskRevision, initial }: { projectId: string; taskId: string; taskRevision: number; initial?: Props['initial'] }) {
  return <ProjectRulesButton projectId={projectId} />;
}


export function PublishTaskDrawer(props: Props) {
  return <SimpleReviewDrawer {...props} />;
}
