import { AiElapsed } from '../../shared/ui/AiElapsed';
import { useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Image, Space, Tag, Typography } from 'antd';
import type { Acceptance } from './api';
import { workflowApi, isRecoverableOutputError, type ScriptReview } from '../workflow/api';

export function ScriptFeedback({ review, acceptance, onRefresh, refreshing, compact, onLocateQuote, hideFindings }: { review: ScriptReview | null; acceptance: Acceptance; onRefresh?: () => void; refreshing?: boolean; compact?: boolean; onLocateQuote?: (quote: string) => void; hideFindings?: boolean }) {
  const [recovering, setRecovering] = useState(false), [recoveryError, setRecoveryError] = useState<string>();
  const recoveryKey = useRef<{ id: string; key: string } | null>(null);
  async function recover() {
    if (!review || recovering) return;
    if (recoveryKey.current?.id !== review.id) recoveryKey.current = { id: review.id, key: crypto.randomUUID() };
    setRecovering(true); setRecoveryError(undefined);
    try { await workflowApi.revalidate(review.id, recoveryKey.current.key); onRefresh?.(); }
    catch (error) { setRecoveryError(error instanceof Error ? error.message : '本地恢复失败'); }
    finally { setRecovering(false); }
  }
  if (!review) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="提交脚本后，这里显示 AI 验收结果和修改建议" />;
  const report = review.result?.report || review.result?.partialReport;
  const media = review.snapshot?.multimodal;
  const missingImageExplanation = review.status === 'failed' || review.status === 'interrupted' ? '报告尚未通过校验，图片说明未采纳；可在调用诊断中查看原始返回。' : review.status === 'processing' ? '图片判定处理中，请等待报告返回。' : '报告中未提供这张图片的判定说明，请核对审核覆盖。';
  const imageUrl = (id: string) => `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/workflow/script-reviews/${encodeURIComponent(review.id)}/images/${encodeURIComponent(id)}`;
  return <><div className="ai-timing-row"><AiElapsed run={review} /></div>
    {(review.status === 'failed' || review.status === 'interrupted') && onRefresh && <Button size="small" loading={refreshing} onClick={onRefresh} style={{ marginBottom: 8 }}>刷新审核结果</Button>}
    {review.status === 'failed' && isRecoverableOutputError(review.errorCode) && !!review.result?.modelResponse && <Button size="small" loading={recovering} onClick={() => void recover()} style={{ marginBottom: 8 }}>本地恢复报告（不调用模型）</Button>}
    {recoveryError && <Alert type="error" title={recoveryError} />}
    {review.result?.recovery?.requiresManualReview && <Alert type="warning" showIcon title="报告已本地恢复，须人工核对" description="恢复候选不能自动获得通过资格；可在调用诊断中核对原始回复和恢复记录。" />}
    {review.result?.coverage && <p>可验证条目：{review.result.coverage.validatedRules} / {review.result.coverage.totalRules}，其余条目标记为未判定。</p>}
    <Alert showIcon type={acceptance.passed ? 'success' : review.status === 'failed' ? 'error' : 'warning'} title={acceptance.passed ? '必须项验收通过' : review.status === 'processing' ? 'AI 正在验收' : review.status === 'failed' ? 'AI 验收未完成' : acceptance.status === 'incomplete' ? '材料或验收结果待补全' : acceptance.status === 'needs_confirmation' ? '存在待确定项，请品牌核对' : '存在未通过项，请修改或提出异议'} description={review.errorMessage || acceptance.message} />
    {(review.status === 'failed' || review.status === 'interrupted') && <details style={{ marginTop: 10 }}><summary>调用诊断</summary><div className="muted">错误码：{review.errorCode || '未知'}<br />审核记录：{review.id}{review.result?.diagnostics?.errorType && <><br />错误类型：{review.result.diagnostics.errorType}</>}{review.result?.diagnostics?.validationError && <><br />校验原因：{review.result.diagnostics.validationError}</>}{!!review.result?.providerRequestIds?.length && <><br />服务商请求 ID：{review.result.providerRequestIds.join(' / ')}</>}</div>{review.result?.diagnostics && <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(review.result.diagnostics, null, 2)}</pre>}{!!review.result?.recovery?.bindings?.length && <details><summary>规则引用恢复记录</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(review.result.recovery.bindings, null, 2)}</pre></details>}{review.result?.finishReason && <p>结束原因：{review.result.finishReason}</p>}{review.result?.recovery?.repairedOutput && <details><summary>格式修复候选（待人工核对）</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{review.result.recovery.repairedOutput}</pre></details>}{review.result?.modelResponse && <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto', fontSize: 12 }}>{review.result.modelResponse}</pre>}</details>}
    <Typography.Paragraph style={{ marginTop: 16 }} ellipsis={compact ? { rows: 5, expandable: true, symbol: '展开总体意见' } : false}>{report?.summary}</Typography.Paragraph>
    {compact ? !!acceptance.checks.length && <details className="acceptance-overview"><summary>必须项验收明细 · {acceptance.checks.length} 条</summary>{acceptance.checks.map((check, i) => <div className="acceptance-check" key={`${check.rule_id}-${i}`}><Tag color={check.verdict === 'compliant' ? 'green' : 'orange'}>{check.rule_number ? `第 ${check.rule_number} 条 · ` : ''}{check.verdict === 'compliant' ? '通过' : check.verdict === 'not_applicable' ? '不适用' : check.verdict === 'non_compliant' ? '未通过' : '待确定'}</Tag><strong>{check.requirement}</strong><p>{check.reason}</p></div>)}</details> : acceptance.checks.map((check, i) => <div className="acceptance-check" key={`${check.rule_id}-${i}`}><Tag color={check.verdict === 'compliant' ? 'green' : 'orange'}>{check.rule_number ? `第 ${check.rule_number} 条 · ` : ''}{check.verdict === 'compliant' ? '通过' : check.verdict === 'not_applicable' ? '不适用' : check.verdict === 'non_compliant' ? '未通过' : '待确定'}</Tag><strong>{check.requirement}</strong><p>{check.reason}</p></div>)}
    {!hideFindings && report?.findings.map((finding, i) => <Card size="small" className="feedback-issue" key={`${finding.rule_id}-${i}`} title={finding.rule_number ? `${finding.verdict === 'non_compliant' ? '违反第' : '第'} ${finding.rule_number} 条${finding.verdict === 'compliant' ? ' · 符合' : finding.verdict === 'pending' ? ' · 未判定' : finding.verdict === 'needs_confirmation' ? ' · 待确认' : finding.verdict === 'not_applicable' ? ' · 不适用' : ''}` : finding.verdict === 'compliant' ? '检查结果' : '反馈与修改建议'}>
      {finding.evidence_status === 'unverified' && <Alert type="warning" showIcon title="引文待人工核对" description={<>{finding.evidence_warning}<p>模型返回的未核实引文：{finding.unverified_quote || finding.unverified_quote_segments?.join(' / ')}</p></>} />}
      {finding.quote_segments?.length ? finding.quote_segments.map((quote, n) => <blockquote key={n}>{quote}{onLocateQuote && <Button size="small" type="link" onClick={() => onLocateQuote(quote)}>定位原文</Button>}</blockquote>) : finding.quote && <blockquote>{finding.quote}{onLocateQuote && <Button size="small" type="link" onClick={() => onLocateQuote(finding.quote)}>定位原文</Button>}</blockquote>}<p>{finding.description}</p>{finding.suggestion && <p><strong>建议：</strong>{finding.suggestion}</p>}
      <Space wrap>{finding.image_ids?.map(id => <div key={id}><Image src={imageUrl(id)} width={120} height={90} style={{ objectFit: 'contain' }} alt={`图片证据 ${media?.images.find(im => im.id === id)?.locations.join('、') || ''}`} /><div className="muted">{media?.images.find(im => im.id === id)?.locations.join('、')}</div></div>)}</Space>
    </Card>)}
    {!!media?.images.length && !compact && <Card size="small" title={`已送审 Excel 图片（${media.images.length} 张）`} style={{ marginTop: 16 }}><Image.PreviewGroup><Space wrap>{media.images.map(im => <div key={im.id}><Image src={imageUrl(im.id)} width={120} height={90} style={{ objectFit: 'contain' }} alt={`脚本图片 ${im.locations.join('、')}`} /><div className="muted">{im.locations.join('、')}</div><p style={{ maxWidth: 240 }}>{report?.image_checks?.find(check => check.image_id === im.id)?.description}</p></div>)}</Space></Image.PreviewGroup></Card>}
    {!!media?.images.length && compact && <details className="acceptance-overview"><summary>图片审核说明 · {media.images.length} 张（原图见左侧「图片」）</summary>{media.images.map(im => <p key={im.id}><strong>{im.locations.join('、')}</strong><br />{report?.image_checks?.find(check => check.image_id === im.id)?.description || missingImageExplanation}</p>)}</details>}
    {!!media?.uncovered.length && <Alert type="warning" style={{ marginTop: 12 }} title="图片未完整覆盖，暂不能进入品牌复核" description={media.uncovered.join('；')} />}
  </>;
}
