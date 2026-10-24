import { AiElapsed, AiWaves, useAiCallTiming } from '../../shared/ui/AiElapsed';
import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Checkbox, Drawer, Empty, Image, Input, Radio, Select, Skeleton, Space, Tag } from 'antd';
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useDemoRole } from '../../app/DemoRole';
import { ErrorState } from '../../shared/ui';
import { formatDate } from '../../shared/lib/format';
import { ContentReviewNav } from '../workflow/ContentReviewNav';
import { evidenceApi } from '../video-model/api';
import { imagePostsApi, postAssetUrl, type PostAsset, type PostDraft } from './api';
import '../creator/creator.css';
import './image-posts.css';

const verdictLabels: Record<string, string> = { compliant: '符合', non_compliant: '需修改', needs_confirmation: '待确认', not_applicable: '不适用', pending: '未判定' };

export function ImagePostPage() {
  const { projectId = '', taskId = '' } = useParams();
  const aiTiming = useAiCallTiming(taskId);
  const role = useDemoRole(), creator = role === 'creator', cache = useQueryClient(), { modal, message } = App.useApp();
  const [modelCalling, setModelCalling] = useState(false);
  const [decisionOpen, setDecisionOpen] = useState(false);
  const [busy, setBusy] = useState(false), [uploading, setUploading] = useState(false), [error, setError] = useState<string>();
  const query = useQuery({ queryKey: ['image-posts', taskId], queryFn: () => imagePostsApi.state(taskId), refetchInterval: q => busy || q.state.data?.data.review?.status === 'processing' ? 2000 : false });
  const settings = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings });
  const view = query.data?.data;
  const [selectedVersion, setSelectedVersion] = useState<string>(), [selectedReview, setSelectedReview] = useState<string>(), [focused, setFocused] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false), [title, setTitle] = useState(''), [body, setBody] = useState(''), [images, setImages] = useState<PostAsset[]>([]);
  const [note, setNote] = useState(''), [reviewed, setReviewed] = useState(false), [decision, setDecision] = useState<'approved' | 'returned'>('approved');
  const lock = useRef(false), saveRequest = useRef<{ signature: string; key: string; versionId?: string; contextKey?: string } | null>(null), analyzeRequest = useRef<{ signature: string; key: string } | null>(null), decisionRequest = useRef<{ signature: string; key: string } | null>(null);
  const editBase = useRef<string | null>(null), generation = useRef(0);
  useEffect(() => { generation.current++; lock.current = false; setBusy(false); setModelCalling(false); setUploading(false); setSelectedVersion(undefined); setSelectedReview(undefined); setEditorOpen(false); setError(undefined); saveRequest.current = null; analyzeRequest.current = null; return () => { generation.current++; }; }, [taskId, projectId]);
  useEffect(() => { setNote(''); setReviewed(false); setDecision('approved'); decisionRequest.current = null; }, [view?.contextKey, view?.review?.id]);
  const version = view?.versions.find(v => v.id === selectedVersion) || view?.currentVersion;
  const historical = !!version && version.id !== view?.currentVersion?.id;
  const matchingReviews = view?.reviews.filter(r => r.versionId === version?.id) || [];
  const review = matchingReviews.find(r => r.id === selectedReview) || (historical ? matchingReviews[0] : view?.review);
  const report = review?.result?.report || review?.result?.partialReport;
  const running = busy || view?.review?.status === 'processing';
  const canAnalyze = !!view?.writable && view.rulesReady && !!settings.data?.data.hasKey && !running;
  const refresh = async () => { await Promise.all([cache.invalidateQueries({ queryKey: ['image-posts', taskId] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['workflow-review-queue'] }), cache.invalidateQueries({ queryKey: ['project-tasks', projectId] })]); };

  function edit() {
    if (!view?.writable || running) return;
    setTitle(version?.title || ''); setBody(version?.body || ''); setImages(version?.images.map(im => ({ ...im })) || []);
    editBase.current = view.currentVersion?.id || null;
    saveRequest.current = null; setError(undefined); setEditorOpen(true);
  }
  async function upload(files: FileList | null) {
    if (!files?.length || uploading || lock.current) return;
    const selected = Array.from(files);
    if (images.length + selected.length > 18) { setError('每版图文最多18张图片'); return; }
    if (selected.some(file => file.size > 4 * 1024 * 1024 || !/\.(png|jpe?g|webp)$/i.test(file.name))) { setError('请选择4MiB以内的 PNG、JPEG 或 WebP 图片'); return; }
    lock.current = true; setUploading(true); setError(undefined); const at = generation.current;
    try {
      for (const file of selected) {
        if (generation.current !== at) return;
        const r = await imagePostsApi.upload(taskId, file, crypto.randomUUID());
        if (generation.current === at) setImages(current => [...current, { ...r.data, caption: '' }]);
      }
    } catch (e) { if (at === generation.current) setError(e instanceof Error ? e.message : '图片上传失败，已上传图片会保留'); }
    finally { if (at === generation.current) { lock.current = false; setUploading(false); } }
  }
  function move(index: number, step: number) { setImages(current => { const next = [...current]; [next[index], next[index + step]] = [next[index + step], next[index]]; return next; }); }
  async function analyze(versionId: string, contextKey: string) {
    const at = generation.current; setModelCalling(true);
    const payload = { versionId, contextKey, configRevision: settings.data!.data.revision, allowRealCall: true as const }, signature = JSON.stringify(payload);
    if (analyzeRequest.current?.signature !== signature) analyzeRequest.current = { signature, key: crypto.randomUUID() };
    aiTiming.start();
    let r;
    try { r = await imagePostsApi.analyze(taskId, payload, analyzeRequest.current.key); aiTiming.finish(r.data.status, r.data); }
    catch (error) { aiTiming.finish('failed'); throw error; }
    if (at !== generation.current) return;
    analyzeRequest.current = null; setSelectedReview(undefined);
    if (r.data.status !== 'succeeded') setError(r.data.errorMessage || '验收未完成，请查看调用诊断');
    else message.success('图文 AI 验收完成，结果仍需品牌复核');
  }
  async function save(withReview: boolean) {
    if (lock.current || !view || !title.trim() || !body.trim() || !images.length || uploading) return;
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    try {
      const payload: PostDraft = { title: title.trim(), body: body.trim(), images: images.map(im => ({ assetId: im.id, caption: im.caption || '' })), baseVersionId: editBase.current };
      const signature = JSON.stringify(payload);
      if (saveRequest.current?.signature !== signature) saveRequest.current = { signature, key: crypto.randomUUID() };
      const entry = saveRequest.current;
      if (!entry.versionId) { const r = await imagePostsApi.save(taskId, payload, entry.key); entry.versionId = r.data.version.id; entry.contextKey = r.data.contextKey; }
      if (at !== generation.current) return;
      setSelectedVersion(undefined); setSelectedReview(undefined); setFocused(0); setEditorOpen(false);
      if (withReview) await analyze(entry.versionId, entry.contextKey!);
      else message.success('图文版本已保存，尚未调用模型');
      saveRequest.current = null;
    } catch (e) { if (at === generation.current) setError(e instanceof Error ? e.message : '提交失败'); }
    finally { if (at === generation.current) { await refresh(); if (at === generation.current) { setModelCalling(false); lock.current = false; setBusy(false); } } }
  }
  function confirmSave() { modal.confirm({ title: '保存图文并进行 AI 验收？', content: `将标题、正文、配文与全部 ${images.length} 张图片发送至 ${settings.data?.data.model || '已配置模型'}，一次请求，可能计费。AI 通过后仍需品牌复核。`, okText: '确认保存并验收', onOk: () => save(true) }); }
  function recheck() {
    if (!version || !view || !canAnalyze || historical) return;
    modal.confirm({ title: '按当前图文规则重新验收？', content: '使用当前版本全部文案与图片调用模型，可能计费，不创建重复内容版本。', okText: '确认调用模型', onOk: async () => {
      if (lock.current) return;
      lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
      try { await analyze(version.id, view.contextKey); }
      catch (e) { if (at === generation.current) setError(e instanceof Error ? e.message : '重新验收失败'); }
      finally { if (at === generation.current) { await refresh(); if (at === generation.current) { setModelCalling(false); lock.current = false; setBusy(false); } } }
    } });
  }
  async function decide() {
    if (!view?.review || lock.current || !reviewed || !note.trim()) return;
    const payload = { reviewId: view.review.id, contextKey: view.contextKey, decision, note: note.trim(), reviewedMaterial: reviewed }, signature = JSON.stringify(payload);
    if (decisionRequest.current?.signature !== signature) decisionRequest.current = { signature, key: crypto.randomUUID() };
    lock.current = true; setBusy(true); setError(undefined); const at = generation.current;
    try { await imagePostsApi.decide(taskId, payload, decisionRequest.current.key); if (at !== generation.current) return; decisionRequest.current = null; message.success('品牌决定已保存'); setDecisionOpen(false); await refresh(); }
    catch (e) { if (at === generation.current) setError(e instanceof Error ? e.message : '品牌决定保存失败'); }
    finally { if (at === generation.current) { lock.current = false; setBusy(false); } }
  }

  if (query.isPending) return <Skeleton active />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  if (!view || view.projectId !== projectId) return <Alert type="error" title="此图文任务不属于当前项目" />;
  const focusedImage = version?.images[Math.min(focused, Math.max(0, version.images.length - 1))];
  const displayedDecision = (historical || selectedReview) ? view.decisions?.find(d => d.review_id === review?.id) : view.decision;
  const displayedRules = review?.snapshot?.rules || view.rules;
  const previewAcceptance = review?.acceptance || view.acceptance;
  const canDecide = !historical && !selectedReview && !!view.review && view.writable && !running;
  return <div className="image-post-workspace media-review-workspace">
    <div className="creator-review-commandbar"><div className="creator-review-identity"><Link to={creator ? '/creator' : `/projects/${projectId}`}>← {creator ? '我的合作' : '项目详情'}</Link><strong>{view.creatorName} · 图文验收</strong><ContentReviewNav projectId={projectId} taskId={taskId} active="image" compact /></div><Space size={6} wrap>{!creator && <Button size="small" onClick={() => setDecisionOpen(true)}>品牌复核</Button>}<Button onClick={() => void query.refetch()}>刷新结果</Button>{creator && <><Button disabled={!version || historical || !canAnalyze} loading={busy} onClick={recheck}>按当前规则重新验收</Button><Button type="primary" disabled={!view.writable || running} onClick={edit}>{version ? '提交修改版图文' : '提交图文'}</Button></>}</Space></div>
    {error && <Alert showIcon type="error" title={error} style={{ marginBottom: 12 }} />}
    {!view.rulesReady && <Alert showIcon type="warning" title="品牌尚未配置可用的图文规则" description={creator ? '可先保存图文版本；请联系品牌确认图文规则后再进行 AI 验收。' : <Link to={`/projects/${projectId}/rules?type=image`}>配置并保存项目图文规则 →</Link>} />}
    {settings.isError && <ErrorState error={settings.error} retry={() => settings.refetch()} />}
    {!view.writable && <Alert type="info" title="项目或合作任务已归档，仅可查看历史" />}
    {historical && <Alert type="info" showIcon title="正在查看历史版本；品牌决定和重新验收仅针对最新版本" />}
    <div className="image-post-context"><span>审核依据 · {view.briefTitle}</span><Space wrap><Tag>{view.rules.length} 条图文规则</Tag>{view.currentVersion && <Tag>{view.currentVersion.versionLabel}</Tag>}{view.brandApproved && <Tag color="green">品牌已通过图文</Tag>}</Space></div>
    <div className="image-post-grid media-review-grid">
      <Card title="待发布图文" className="image-post-preview" extra={view.versions.length > 0 && <Select aria-label="图文版本" value={version?.id} style={{ minWidth: 140 }} onChange={id => { setSelectedVersion(id); setSelectedReview(undefined); setFocused(0); }} options={view.versions.map(v => ({ value: v.id, label: `${v.versionLabel}${v.id === view.currentVersion?.id ? ' · 最新' : ' · 历史'}` }))} />}>
        {!version ? <div className="image-post-empty"><Empty description="尚未提交图文" />{creator && <Button type="primary" onClick={edit} disabled={!view.writable}>提交标题、正文与图片</Button>}<p>第 1 张为封面，按发布顺序核对全部配图。</p></div> : <>
          {focusedImage && <div className="image-post-stage"><div className="image-post-stage-label"><Tag color={focusedImage.ordinal === 1 ? 'cyan' : 'default'}>{focusedImage.ordinal === 1 ? '封面' : `配图 ${focusedImage.ordinal}`}</Tag><span>{Math.min(focused, version.images.length - 1) + 1} / {version.images.length}</span></div><Image src={postAssetUrl(taskId, focusedImage.id)} alt={focusedImage.caption || `${version.title} · 第${focusedImage.ordinal}张`} /><p>{focusedImage.caption || focusedImage.name}</p><a href={postAssetUrl(taskId, focusedImage.id, true)} target="_blank" rel="noreferrer">下载原图 ↗</a></div>}
          <div className="image-post-thumbnails" aria-label="按发布顺序查看图片">{version.images.map((im, index) => <button key={im.id} type="button" aria-label={`查看第${index + 1}张${index === 0 ? '封面' : '配图'}`} aria-pressed={focused === index} onClick={() => setFocused(index)}><img src={postAssetUrl(taskId, im.id)} alt={im.name} /><span>{index === 0 ? '封面' : String(index + 1)}</span></button>)}</div>
          <article className="image-post-copy"><h1>{version.title}</h1><p>{version.body}</p><small>{version.versionLabel} · {formatDate(version.createdAt)} · {version.images.length} 张图片</small></article>
        </>}
      </Card>
      <Card title={<div className="ai-timing-row"><span>AI 验收与品牌反馈</span><AiElapsed run={selectedReview || historical ? review : aiTiming.run || review} /></div>} className="image-post-feedback" extra={matchingReviews.length > 0 && <Select aria-label="图文验收历史" placeholder="当前验收" value={review?.id} style={{ width: 180 }} onChange={id => setSelectedReview(id === view.review?.id ? undefined : id)} options={matchingReviews.map(r => ({ value: r.id, label: `${r.status === 'succeeded' ? '完成' : r.status === 'processing' ? '验收中' : '未完成'} · ${formatDate(r.createdAt)}` }))} />}>
        {selectedReview && selectedReview !== view.review?.id && <Alert type="info" title="历史验收记录，仅供对照" />}
        {modelCalling && <Alert type="info" showIcon title={<span className="ai-elapsed is-running"><AiWaves />AI 正在验收图文</span>} description="正在提交本次全部文字和图片，历史验收结果不作为本次结论，请勿重复提交。" />}
        {!modelCalling && review ? <>
          {review.result?.recovery?.requiresManualReview && <Alert type="warning" title="格式修复候选，须人工核对，不能自动通过" />}
          <Alert showIcon type={previewAcceptance.passed ? 'success' : review.status === 'failed' ? 'error' : 'warning'} title={review.status === 'processing' ? 'AI 正在验收图文' : previewAcceptance.passed ? displayedDecision?.decision === 'approved' ? 'AI 必须项通过，品牌已确认' : displayedDecision?.decision === 'returned' ? 'AI 必须项通过，品牌要求修改' : 'AI 必须项通过，仍需品牌复核' : '图文验收未通过或待补全'} description={review.errorMessage || (displayedDecision?.decision === 'approved' ? '品牌已确认此版本，后续修改需重新验收。' : displayedDecision?.decision === 'returned' ? '请按下方品牌意见修改并提交新版本。' : previewAcceptance.message)} />
          {report && <details className="image-post-details image-post-overview"><summary>总体意见与覆盖说明</summary><p className="image-post-summary">{report.summary}</p>{review.result?.coverage && <p>可验证条目：{review.result.coverage.validatedRules} / {review.result.coverage.totalRules}</p>}</details>}
          {report?.findings.map((finding, index) => <div className="image-post-finding" key={`${finding.rule_id}-${index}`}><Space wrap><Tag color={finding.verdict === 'compliant' ? 'green' : finding.verdict === 'non_compliant' ? 'red' : 'orange'}>{verdictLabels[finding.verdict] || finding.verdict}</Tag><strong>第 {finding.rule_number} 条 · {displayedRules.find(r => r.id === finding.rule_id)?.text || '历史图文规则'}</strong></Space>{finding.quote && <blockquote>{finding.quote}</blockquote>}<p>{finding.description}</p>{finding.evidence_status === 'unverified' && <Alert type="warning" title="引文待人工核对" description={finding.unverified_quote} />}{finding.suggestion && <p className="image-post-suggestion"><strong>修改建议：</strong>{finding.suggestion}</p>}<Space wrap>{finding.image_ids?.map(id => { const imageIndex = version?.images.findIndex(im => im.id === id) ?? -1; return imageIndex >= 0 ? <Button key={id} size="small" onClick={() => setFocused(imageIndex)}>查看第{imageIndex + 1}张{imageIndex === 0 ? '封面' : '配图'}</Button> : null; })}</Space></div>)}
          {!!report?.image_checks?.length && <details className="image-post-details"><summary>逐图审核说明 · {report.image_checks.length} 张</summary>{report.image_checks.map(check => <p key={check.image_id}><strong>第 {(version?.images.findIndex(im => im.id === check.image_id) ?? -1) + 1} 张：</strong>{check.description}</p>)}</details>}
          <details className="image-post-details"><summary>调用诊断与原始返回</summary><p>审核记录：{review.id}<br />错误码：{review.errorCode || '无'}<br />服务商请求 ID：{review.result?.providerRequestIds?.join(' / ') || '未提供'}</p>{review.result?.diagnostics && <pre>{JSON.stringify(review.result.diagnostics, null, 2)}</pre>}{review.result?.recovery?.repairedOutput && <><strong>格式修复候选</strong><pre>{review.result.recovery.repairedOutput}</pre></>}<pre>{review.result?.modelResponse || '尚无原始返回'}</pre></details>
        </> : !busy && <Empty description={version ? '此版本尚无适用于当前图文规则的验收，请重新验收' : '提交图文后显示逐条反馈和逐图说明'} />}
        {displayedDecision && <Alert showIcon type={displayedDecision.decision === 'approved' ? 'success' : 'warning'} title={(historical || selectedReview ? '历史意见 · ' : '') + (displayedDecision.decision === 'approved' ? '品牌已通过图文' : '品牌退回修改')} description={displayedDecision.note} style={{ marginTop: 16 }} />}
        <details className="image-post-details"><summary>本次图文检查标准 · {displayedRules.length} 条</summary>{displayedRules.map(rule => <p key={rule.id}><strong>第 {rule.ruleNumber} 条 · {rule.text}</strong><br />{rule.criterion}<br /><Tag>{rule.importance === 'must' ? '必须项' : '建议项'}</Tag></p>)}</details>

      </Card>
    </div>
    <Drawer title="品牌复核当前图文" open={decisionOpen} onClose={() => { if (!busy) setDecisionOpen(false); }} size={560}>{!creator && <div className="image-post-brand-decision"><h3>品牌复核当前图文</h3><p>请核对标题、完整正文、每张图片与 AI 意见后作决定。</p><Radio.Group aria-label="图文品牌决定" value={decision} onChange={e => setDecision(e.target.value)} disabled={!canDecide}><Radio value="approved" disabled={!view.brandReviewAllowed}>通过图文</Radio><Radio value="returned">退回修改</Radio></Radio.Group><Input.TextArea aria-label="图文品牌复核意见" value={note} onChange={e => setNote(e.target.value)} rows={3} maxLength={2000} placeholder="填写核对结论或需要修改的内容" disabled={!canDecide} /><Checkbox checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={!canDecide}>已核对当前版本全部文字与图片</Checkbox><Button type="primary" onClick={() => void decide()} loading={busy} disabled={!canDecide || !reviewed || !note.trim() || (decision === 'approved' && !view.brandReviewAllowed)}>保存品牌决定</Button></div>}</Drawer>
    <Drawer title={view.currentVersion ? '提交修改版图文' : '提交图文'} open={editorOpen} onClose={() => { if (!busy && !uploading) setEditorOpen(false); }} size={760} footer={<Space wrap><Button disabled={busy || uploading} onClick={() => setEditorOpen(false)}>取消</Button><Button loading={busy} disabled={!title.trim() || !body.trim() || !images.length || uploading || !view.writable} onClick={() => void save(false)}>保存版本（不调用 AI）</Button><Button type="primary" disabled={!canAnalyze || uploading || !title.trim() || !body.trim() || !images.length} onClick={confirmSave}>保存并进行 AI 验收</Button></Space>}>
      <div className="image-post-editor"><label htmlFor="post-title">图文标题</label><Input id="post-title" value={title} onChange={e => setTitle(e.target.value)} maxLength={120} showCount disabled={busy} placeholder="填写待发布的图文标题" /><label htmlFor="post-body">正文与话题</label><Input.TextArea id="post-body" value={body} onChange={e => setBody(e.target.value)} rows={8} maxLength={20000} showCount disabled={busy} placeholder="粘贴完整正文，包含话题、品牌名和发布文案" /><div className="image-post-editor-heading"><strong>发布图片 · {images.length} / 18</strong><span>第一张为封面，可调整顺序与配文</span></div><label className="image-post-upload" htmlFor="post-images"><PlusOutlined /> {uploading ? '正在上传图片…' : '添加图片'}<small>PNG / JPEG / WebP · 每张4MiB以内 · 合计编码后20MiB</small></label><input id="post-images" aria-label="添加图文图片" type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={uploading || busy || images.length >= 18} onChange={e => { void upload(e.target.files); e.target.value = ''; }} />
        <div className="image-post-editor-images">{images.map((im, index) => <div className="image-post-editor-image" key={im.id}><Image width={100} height={100} style={{ objectFit: 'contain' }} src={postAssetUrl(taskId, im.id)} alt={im.name} /><div><Space><Tag color={index === 0 ? 'cyan' : 'default'}>{index === 0 ? '封面' : `配图 ${index + 1}`}</Tag><span>{im.name}</span></Space><Input aria-label={`第${index + 1}张图片配文`} value={im.caption || ''} maxLength={500} onChange={e => setImages(current => current.map(item => item.id === im.id ? { ...item, caption: e.target.value } : item))} disabled={busy || uploading} placeholder="可选：这张图片的发布配文" /><Space><Button size="small" aria-label={`上移第${index + 1}张图片`} icon={<ArrowUpOutlined />} disabled={index === 0 || busy || uploading} onClick={() => move(index, -1)}>上移</Button><Button size="small" aria-label={`下移第${index + 1}张图片`} icon={<ArrowDownOutlined />} disabled={index === images.length - 1 || busy || uploading} onClick={() => move(index, 1)}>下移</Button><Button size="small" danger icon={<DeleteOutlined />} disabled={busy || uploading} onClick={() => setImages(current => current.filter(item => item.id !== im.id))}>移除</Button></Space></div></div>)}</div>
        {!view.rulesReady && <Alert type="warning" title="尚无图文规则，可保存版本，暂不能进行 AI 验收" />}{settings.data && !settings.data.data.hasKey && <Alert type="warning" title="品牌尚未配置模型 Key，可先保存图文" />}{error && <Alert type="error" title={error} />}
      </div>
    </Drawer>
  </div>;
}
