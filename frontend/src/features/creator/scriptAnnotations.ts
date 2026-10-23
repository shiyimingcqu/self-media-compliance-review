import type { ScriptReview, Workflow } from '../workflow/api';

export type Finding = NonNullable<NonNullable<ScriptReview['result']>['report']>['findings'][number];
export type ScriptAnnotation = { id: string; index: number; finding: Finding; anchors: { location: string; start: number; end: number; kind: 'text' | 'image'; imageId?: string }[] };

export type CommentCategory = 'changes' | 'suggestions' | 'passed' | 'not_applicable';

export function commentCategory(finding: Finding, review: ScriptReview | null): CommentCategory {
  if (finding.verdict === 'not_applicable') return 'not_applicable';
  if (finding.verdict === 'compliant' && finding.evidence_status !== 'unverified') return 'passed';
  const rule = review?.snapshot?.rules?.find(r => r.id === finding.rule_id);
  return rule?.importance === 'suggested' ? 'suggestions' : 'changes';
}

export function scriptAnnotations(script: Workflow['currentScript'], review: ScriptReview | null): ScriptAnnotation[] {
  if (!script || !review || review.scriptVersionId !== script.scriptVersionId || (review.status !== 'succeeded' && !review.result?.partialReport && !review.result?.recovery?.requiresManualReview)) return [];
  const blocks = script.sourceMap.length ? script.sourceMap : script.text ? [{ text: script.text, location: '脚本正文' }] : [];
  const text = script.text ?? blocks.map(b => b.text).join('\n');
  let cursor = 0;
  const positioned = blocks.map((b, i) => {
    const start = text.indexOf(b.text, cursor); if (start >= 0) cursor = start + b.text.length;
    return { ...b, location: b.location || `段落 ${i + 1}`, start, end: start + b.text.length };
  });
  return ((review.result?.report || review.result?.partialReport)?.findings || []).map((finding, index) => {
    const anchors: ScriptAnnotation['anchors'] = [];
    let from = 0;
    for (const quote of finding.quote_segments?.length ? finding.quote_segments : finding.quote ? [finding.quote] : []) {
      const start = text.indexOf(quote, from); if (start < 0 || !quote) continue;
      from = start + quote.length;
      for (const block of positioned) {
        if (block.start >= 0 && start < block.end && from > block.start) anchors.push({ location: block.location, start: Math.max(0, start - block.start), end: Math.min(block.text.length, from - block.start), kind: 'text' });
      }
    }
    for (const imageId of finding.image_ids || []) {
      const image = review.snapshot?.multimodal?.images.find(im => im.id === imageId);
      for (const location of image?.locations || []) if (location.includes('!')) anchors.push({ location, start: 0, end: 0, kind: 'image', imageId });
    }
    return { id: `comment-${index + 1}`, index: index + 1, finding, anchors };
  });
}
