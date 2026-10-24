import type { ScriptReview, Workflow } from '../workflow/api';

export type Finding = NonNullable<NonNullable<ScriptReview['result']>['report']>['findings'][number];
export type ScriptAnnotation = { id: string; index: number; finding: Finding; anchors: { location: string; start: number; end: number; kind: 'text' | 'image'; imageId?: string }[] };

export type CommentCategory = 'changes' | 'uncertain' | 'suggestions' | 'passed' | 'not_applicable';

export function commentCategory(finding: Finding, review: ScriptReview | null): CommentCategory {
  if (finding.verdict === 'not_applicable') return 'not_applicable';
  if (finding.verdict === 'compliant' && finding.evidence_status !== 'unverified') return 'passed';
  const rule = review?.snapshot?.rules?.find(r => r.id === finding.rule_id);
  if (rule?.importance === 'suggested') return 'suggestions';
  return finding.verdict === 'non_compliant' ? 'changes' : 'uncertain';
}

export function scriptRuleFindings(review: ScriptReview | null): Finding[] {
  const report = review?.result?.report || review?.result?.partialReport;
  if (!report) return [];
  const findings = [...report.findings];
  for (const check of report.mandatory_checks || []) {
    const matches = findings.filter(f => f.rule_id === check.rule_id);
    if (review?.snapshot?.paragraphMode || !matches.length) {
      const merged: Finding = { ...matches[0], ...check, quote: check.quote || matches[0]?.quote || '',
        description: check.reason, suggestion: matches[0]?.suggestion || '' };
      const first = findings.findIndex(f => f.rule_id === check.rule_id);
      if (first >= 0) {
        for (let i = findings.length - 1; i >= 0; i--) if (findings[i].rule_id === check.rule_id) findings.splice(i, 1);
        findings.splice(first, 0, merged);
      } else findings.push(merged);
    }
  }
  return findings;
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
  return scriptRuleFindings(review).map((finding, index) => {
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
      for (const location of image?.locations || []) if (location.includes('!') || /^(paragraph:\d+|table:\d+:r\d+c\d+)$/.test(location)) anchors.push({ location, start: 0, end: 0, kind: 'image', imageId });
    }
    return { id: `comment-${index + 1}`, index: index + 1, finding, anchors };
  });
}
