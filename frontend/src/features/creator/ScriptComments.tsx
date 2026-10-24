import { ReviewDiscussionProvider, CreatorReviewResponse, RuleDisputeButton } from '../workflow/ReviewDiscussion';
import { useEffect, useRef, useState } from 'react';
import { Button, Empty, Popover, Segmented, Tag } from 'antd';
import { ScriptFeedback } from './ScriptFeedback';
import type { Acceptance } from './api';
import type { ScriptReview } from '../workflow/api';
import { commentCategory, type CommentCategory, type ScriptAnnotation } from './scriptAnnotations';
import { AiElapsed, type AiTimedRun } from '../../shared/ui/AiElapsed';

export function ScriptComments({ taskId, review, timing, acceptance, annotations, active, onSelect, onRefresh, refreshing }: { taskId?: string; review: ScriptReview | null; timing?: AiTimedRun; acceptance: Acceptance; annotations: ScriptAnnotation[]; active?: { id: string; sequence: number }; onSelect: (id: string) => void; onRefresh: () => void; refreshing: boolean }) {
  const [filter, setFilter] = useState<CommentCategory>('changes'); const root = useRef<HTMLDivElement>(null);
  const counts = { changes: 0, suggestions: 0, passed: 0, not_applicable: 0 };
  for (const annotation of annotations) counts[commentCategory(annotation.finding, review)]++;
  useEffect(() => { setFilter('changes'); }, [review?.id]);
  useEffect(() => {
    const annotation = annotations.find(a => a.id === active?.id);
    if (annotation) setFilter(commentCategory(annotation.finding, review));
  }, [active]);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const comment = [...(root.current?.querySelectorAll<HTMLElement>('[data-comment-target]') || [])].find(el => el.dataset.commentTarget === active?.id);
      const body = comment?.closest<HTMLElement>('.ant-card-body');
      if (comment && body) {
        const box = comment.getBoundingClientRect(), clip = body.getBoundingClientRect();
        if (box.top < clip.top || box.bottom > clip.bottom) body.scrollTo({ top: body.scrollTop + box.top - clip.top - 16, behavior: 'smooth' });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [active, filter]);
  return <ReviewDiscussionProvider taskId={taskId} scope="script" reviewId={review?.id}><div ref={root} className="script-review-comments">
    <div className="review-comment-toolbar"><div className="review-comment-status"><Tag title={review?.errorMessage || acceptance.message} color={acceptance.passed ? 'green' : review?.status === 'failed' ? 'red' : 'orange'}>{acceptance.passed ? '必须项通过' : !review ? '待提交' : review.status === 'processing' ? '验收中' : ['failed', 'interrupted'].includes(review.status) ? '验收未完成' : acceptance.status === 'incomplete' ? '资料待补全' : '待修改'}</Tag><Popover trigger="click" title="审核详情" content={<div className="review-information-popover"><ScriptFeedback review={review} acceptance={acceptance} compact hideFindings onRefresh={onRefresh} refreshing={refreshing} /></div>}><Button size="small" type="text">审核详情</Button></Popover><AiElapsed run={timing || review} /></div><Segmented block size="small" aria-label="筛选审核评论" value={filter} onChange={value => setFilter(value as CommentCategory)} options={[{ value: 'changes', label: `需修改 ${counts.changes} 项` }, { value: 'suggestions', label: `建议 ${counts.suggestions} 项` }, { value: 'passed', label: `通过 ${counts.passed} 项` }, ...(counts.not_applicable ? [{ value: 'not_applicable', label: `不适用 ${counts.not_applicable} 项` }] : [])]} /></div>
    <CreatorReviewResponse needsResponse={review?.status === 'succeeded' && annotations.some(a => ['non_compliant', 'needs_confirmation', 'pending'].includes(a.finding.verdict))} />
    {!review && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="提交脚本后显示 AI 评论" />}
    {review?.result?.recovery?.requiresManualReview && <p role="note">格式已修复，以下为待人工核对的候选，不能自动通过。</p>}
    {review?.result?.coverage && <p>可验证条目：{review.result.coverage.validatedRules} / {review.result.coverage.totalRules}；其余为未判定。</p>}
    {review?.status === 'failed' && <p className="review-comment-no-source">{review.errorCode} · {review.errorMessage} <Button type="link" size="small" onClick={onRefresh} loading={refreshing}>刷新审核结果</Button></p>}
    {annotations.filter(a => commentCategory(a.finding, review) === filter).map(annotation => {
      const finding = annotation.finding, category = commentCategory(finding, review), color = category === 'suggestions' ? 'blue' : finding.verdict === 'non_compliant' ? 'red' : finding.verdict === 'needs_confirmation' ? 'orange' : finding.verdict === 'compliant' ? 'green' : 'default';
      const locations = [...new Set(annotation.anchors.map(a => a.location))];
      return <article key={annotation.id} className={`review-comment ${annotation.id === active?.id ? 'active' : ''} ${finding.verdict}`} aria-label={`审核评论 ${annotation.index}`}>
        <div className="review-comment-heading"><button type="button" className="review-comment-number" data-comment-target={annotation.id} aria-label={`定位评论 ${annotation.index}`} onClick={() => onSelect(annotation.id)}>{annotation.index}</button><strong>AI 审核意见</strong><Tag color={color}>{finding.rule_number ? `第 ${finding.rule_number} 条 · ` : ''}{category === 'suggestions' ? '建议' : finding.verdict === 'non_compliant' ? '需修改' : finding.verdict === 'needs_confirmation' ? '待确认' : finding.verdict === 'compliant' && finding.evidence_status !== 'unverified' ? '通过' : finding.verdict === 'pending' ? '未判定' : finding.evidence_status === 'unverified' ? '待确认' : '不适用'}</Tag></div>
        {locations.length ? <div className="review-comment-location"><span>{locations.join('、')}</span><Button size="small" type="link" onClick={() => onSelect(annotation.id)}>定位原文</Button></div> : <span className="review-comment-no-source">{finding.evidence_status === 'unverified' ? '引文待人工核对 · 未建立文字定位' : finding.quote || finding.image_ids?.length ? '引文未定位到当前预览，请核对原文件' : '缺失项 / 整体检查 · 无对应原文位置'}</span>}
        {(finding.quote_segments?.length ? finding.quote_segments : finding.quote ? [finding.quote] : []).map((quote, i) => <blockquote key={i} onClick={() => onSelect(annotation.id)}>{quote}</blockquote>)}
        {finding.evidence_status === 'unverified' && <div className="review-comment-suggestion" role="note"><strong>{finding.evidence_warning}</strong><p>模型返回的未核实引文：{finding.unverified_quote || finding.unverified_quote_segments?.join(' / ') || '无可定位引文'}</p></div>}
        <p>{finding.description}</p>
        {finding.suggestion && <div className="review-comment-suggestion"><strong>修改建议</strong><p>{finding.suggestion}</p></div>}
        {['non_compliant', 'needs_confirmation', 'pending'].includes(finding.verdict) && <RuleDisputeButton ruleId={finding.rule_id} />}
      </article>;
    })}
    {!!annotations.length && !counts[filter] && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={filter === 'changes' ? '暂无需修改项，可查看建议或通过项' : filter === 'suggestions' ? '暂无建议项' : '暂无通过项'} />}
    {review?.status === 'succeeded' && !annotations.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前脚本没有可定位的规则评论" />}
  </div></ReviewDiscussionProvider>;
}
