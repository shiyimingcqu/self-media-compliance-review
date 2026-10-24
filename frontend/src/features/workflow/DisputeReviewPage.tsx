import { PanZoomImage } from '../../shared/ui/PanZoomImage';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Checkbox, Empty, Input, Select, Skeleton, Space, Tabs, Tag } from 'antd';
import { useDemoRole } from '../../app/DemoRole';
import { ErrorState } from '../../shared/ui';
import { fileContentUrl } from '../../shared/api/client';
import { ScriptPreview } from '../creator/ScriptPreview';
import { scriptAnnotations } from '../creator/scriptAnnotations';
import { artifactUrl } from '../video-model/api';
import { discussionsApi, disputeLabels } from './discussionsApi';
import { DisputeMessages, useDiscussionAction } from './ReviewDiscussion';
import '../creator/creator.css';
import './review-discussion.css';

const verdictLabels: Record<string, string> = { compliant: '通过', non_compliant: '未通过', needs_confirmation: '待确定', pending: '未判定', not_applicable: '不适用' };
const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} 秒`;

export function DisputeReviewPage() {
  const { projectId = '', disputeId = '' } = useParams(), role = useDemoRole();
  const cache = useQueryClient(), action = useDiscussionAction();
  const [panel, setPanel] = useState('dispute');
  const [active, setActive] = useState<{ id: string; sequence: number }>();
  const [outcome, setOutcome] = useState<'accepted' | 'rejected' | 'needs_info'>('needs_info');
  const [note, setNote] = useState(''), [reviewed, setReviewed] = useState(false);
  const video = useRef<HTMLVideoElement>(null), rulesRoot = useRef<HTMLDivElement>(null);
  const query = useQuery({ queryKey: ['review-dispute-detail', projectId, disputeId], enabled: role === 'brand', queryFn: () => discussionsApi.detail(projectId, disputeId), refetchInterval: 15000 });
  const value = query.data?.data;
  const annotations = useMemo(() => scriptAnnotations(value?.scriptMaterial || null, value?.scriptReview || null), [value?.scriptMaterial, value?.scriptReview]);
  useEffect(() => { setPanel('dispute'); setNote(''); setReviewed(false); setOutcome('needs_info'); setActive(undefined); }, [disputeId]);
  useEffect(() => {
    if (panel === 'rules' && active) rulesRoot.current?.querySelector(`[data-annotation="${active.id}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [panel, active]);
  function locate(id: string, showRule = false) {
    setActive({ id, sequence: Date.now() });
    if (showRule) setPanel(annotations.find(a => a.id === id)?.finding.rule_id === value?.rule_id ? 'dispute' : 'rules');
  }
  const disputedAnnotation = annotations.find(a => a.finding.rule_id === value?.rule_id);
  useEffect(() => {
    if (disputedAnnotation) setActive({ id: disputedAnnotation.id, sequence: Date.now() });
  }, [disputedAnnotation?.id, disputeId]);
  function seek(ms: number) { if (video.current) { video.current.currentTime = ms / 1000; void video.current.play().catch(() => {}); } }
  function decide() {
    if (!value) return;
    const body = { outcome, note, revision: value.revision, reviewedMaterial: reviewed };
    void action.run(body, key => discussionsApi.decide(projectId, disputeId, body, key), async () => {
      await query.refetch();
      await Promise.all(['review-disputes', 'creator-progress', 'workflow', 'creator-tasks', 'review-discussion', 'workflow-review-queue', 'shell-review-count', 'review-disputes-count', 'submission-records'].map(key => cache.invalidateQueries({ queryKey: [key] })));
      setNote(''); setReviewed(false);
    });
  }
  if (role !== 'brand') return <Navigate to="/creator" replace />;
  if (query.isPending) return <Skeleton active />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  if (!value) return <Empty description="未找到这条异议" />;
  return <div className="creator-script-workspace brand-dispute-workspace">
    <header className="creator-review-commandbar">
      <div className="creator-review-identity"><Link to={`/feedback?tab=disputes&projectId=${projectId}`}>← 返回异议列表</Link><strong title={value.project_name}>{value.project_name}</strong><span>{value.creator_name}</span><Tag color="gold">{value.scope === 'script' ? '脚本' : '视频'}异议复核</Tag></div>
      <Space><Tag color={value.status === 'accepted' ? 'green' : 'orange'}>{disputeLabels[value.status]}</Tag><Button size="small" loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button></Space>
    </header>
    {!value.current && <Alert className="dispute-history-notice" type="info" showIcon title="历史版本异议：以下为当时提交的材料和规则，裁决不会改变当前版本资格。" />}
    <div className="creator-submit-grid creator-review-grid creator-comment-grid">
      <Card title={value.scope === 'script' ? '脚本解析预览' : '视频原片'} className="creator-reader-card" extra={value.scriptMaterial ? <Tag>{value.scriptMaterial.versionLabel}</Tag> : <Tag>本次提交</Tag>}>
        {value.scope === 'script' ? <ScriptPreview taskId={value.task_id} script={value.scriptMaterial || null} review={value.scriptReview || null} annotations={annotations} active={active} onSelect={id => locate(id, true)} /> : <div className="dispute-video-preview">
          {value.fileId ? <video ref={video} aria-label="异议视频原片" controls preload="metadata" src={fileContentUrl(value.fileId)} /> : <Empty description="本次审核未关联视频原文件" />}
          {!!value.artifacts.length && <div className="dispute-video-locations"><strong>本条规则取证位置</strong>{value.artifacts.map(item => <Button key={item.id} onClick={() => seek(item.startMs)}>{item.kind === 'image' ? `定位截图 ${seconds(item.startMs)}` : `播放 ${seconds(item.startMs)} 至 ${seconds(item.endMs ?? item.startMs)}`}</Button>)}</div>}
          {value.script && <details className="dispute-reference-script"><summary>当时参考脚本</summary><pre>{value.script}</pre></details>}
          {value.fileId && <a href={fileContentUrl(value.fileId)} target="_blank" rel="noreferrer">查看原文件 ↗</a>}
        </div>}
      </Card>
      <Card title="规则异议 · 品牌裁决" className="creator-reader-card creator-feedback-card">
        <Tabs activeKey={panel} onChange={setPanel} items={[
          { key: 'dispute', label: '当前异议', children: <>
            <section className="dispute-rule-context"><div className="dispute-rule-meta"><Tag color="gold">第 {value.rule.ruleNumber || '?'} 条</Tag><span>本次冻结规则</span></div><h2>{value.rule.text}</h2><p>{value.rule.criterion}</p>
              <div className="dispute-ai-reason"><strong>AI 原始意见 · {verdictLabels[value.finding.verdict] || value.finding.verdict}</strong><p>{value.finding.description}</p>{value.finding.quote && <blockquote>{value.finding.quote}</blockquote>}{disputedAnnotation && disputedAnnotation.anchors.length > 0 && <Button size="small" onClick={() => locate(disputedAnnotation.id)}>定位脚本原文</Button>}</div>
              {value.artifacts.length > 0 && <div className="dispute-evidence">{value.artifacts.map(item => <figure key={item.id}>{item.kind === 'image' ? <PanZoomImage alt={`异议截图 ${seconds(item.startMs)}`} src={artifactUrl(value.review_id, item.id)} /> : <video controls preload="metadata" src={artifactUrl(value.review_id, item.id)} />}<figcaption>{item.kind === 'image' ? `截图 ${seconds(item.startMs)}` : `片段 ${seconds(item.startMs)} 至 ${seconds(item.endMs ?? item.startMs)}`}</figcaption></figure>)}</div>}
            </section>
            <section className="dispute-conversation"><h3>达人异议与品牌回复</h3><DisputeMessages dispute={value} /></section>
            {value.writable && ['pending', 'needs_info'].includes(value.status) && <section className="dispute-ruling"><h3>品牌裁决</h3><Select aria-label="异议裁决结果" value={outcome} onChange={setOutcome} options={[{ value: 'needs_info', label: '请达人补充说明' }, { value: 'accepted', label: '采纳异议 · 本条人工通过' }, { value: 'rejected', label: '维持 AI 原判 · 请修改' }]} style={{ width: '100%' }} /><Input.TextArea aria-label="品牌裁决理由" value={note} onChange={e => setNote(e.target.value)} rows={4} maxLength={4000} placeholder="说明依据和后续要求，达人将看到这条回复" /><Checkbox checked={reviewed} onChange={e => setReviewed(e.target.checked)}>已核对本次原始材料、规则及相关证据</Checkbox><Button type="primary" loading={action.busy} disabled={!reviewed || !note.trim()} onClick={decide}>提交品牌裁决</Button><p className="dispute-ruling-note">采纳仅对本条规则生效，原始 AI 意见保留；整份内容仍需品牌确认。</p>{action.error && <Alert type="error" title={action.error} />}</section>}
          </> },
          { key: 'rules', label: `本次审核规则 · ${value.rules.length}`, children: <div ref={rulesRoot} className="dispute-frozen-rules">{value.rules.map(rule => {
            const finding = value.findings.find(f => f.rule_id === rule.id), annotation = annotations.find(a => a.finding.rule_id === rule.id);
            return <article key={rule.id} data-annotation={annotation?.id} className={`review-comment${active && annotation && active.id === annotation.id ? ' active' : ''}`}><Space wrap><Tag>{rule.importance === 'must' ? '必须' : '建议'}</Tag>{rule.id === value.rule_id && <Tag color="gold">当前异议</Tag>}{finding && <Tag>{verdictLabels[finding.verdict] || finding.verdict}</Tag>}</Space><h3>第 {rule.ruleNumber || '?'} 条 · {rule.text}</h3><p>{rule.criterion}</p>{finding && <p>{finding.description}</p>}{annotation && annotation.anchors.length > 0 && <Button size="small" onClick={() => locate(annotation.id)}>定位脚本原文</Button>}</article>;
          })}</div> },
        ]} />
      </Card>
    </div>
  </div>;
}
