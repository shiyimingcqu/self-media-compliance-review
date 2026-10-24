import { describe, expect, it } from 'vitest';
import { commentCategory, scriptAnnotations, scriptRuleFindings } from './scriptAnnotations';
import type { Workflow, ScriptReview } from '../workflow/api';

const script: NonNullable<Workflow['currentScript']> = { scriptVersionId: 's1', versionLabel: 's1', extractionStatus: 'ready', text: null, parseMeta: {}, sourceMap: [{ text: '产品名称错误', location: '脚本!D10' }, { text: '第二句台词', location: '脚本!D11' }] };
const review: ScriptReview = { id: 'r1', scriptVersionId: 's1', ruleVersionId: null, contextKey: 'c', status: 'succeeded', createdAt: '', snapshot: { multimodal: { images: [{ id: 'im', locations: ['脚本!E10'], width: 100, height: 100 }], incomplete: false, uncovered: [] } }, result: { report: { summary: '', findings: [
  { rule_id: 'r1', verdict: 'non_compliant', quote: '名称错误\n第二句', description: '', suggestion: '' },
  { rule_id: 'r2', verdict: 'non_compliant', quote: '', image_ids: ['im'], description: '', suggestion: '' },
  { rule_id: 'r3', verdict: 'non_compliant', quote: '', description: '缺少卖点', suggestion: '' },
] } } };

describe('script comment evidence mapping', () => {
  it('separates failures, uncertainty and inapplicable conditional rules', () => {
    const base = review.result!.report!.findings[0];
    expect(commentCategory({ ...base, verdict: 'non_compliant' }, review)).toBe('changes');
    expect(commentCategory({ ...base, verdict: 'needs_confirmation' }, review)).toBe('uncertain');
    expect(commentCategory({ ...base, verdict: 'compliant', evidence_status: 'unverified' }, review)).toBe('uncertain');
    expect(commentCategory({ ...base, verdict: 'not_applicable' }, review)).toBe('not_applicable');
  });
  it('includes mandatory-only checks and uses the same rule conclusion as acceptance', () => {
    const paragraph: ScriptReview = { ...review, snapshot: { ...review.snapshot, paragraphMode: true }, result: { report: {
      summary: '', findings: [{ ...review.result!.report!.findings[0], verdict: 'compliant' }],
      mandatory_checks: [{ rule_id: 'r1', verdict: 'needs_confirmation', requirement: '待确认', reason: '需核对' },
        { rule_id: 'price', verdict: 'not_applicable', requirement: '涉及价格时核对', reason: '全文未出现价格' }],
    } } };
    const findings = scriptRuleFindings(paragraph);
    expect(findings).toHaveLength(2);
    expect(findings[0].verdict).toBe('needs_confirmation');
    expect(findings[1].description).toBe('全文未出现价格');
    expect(scriptAnnotations(script, paragraph)).toHaveLength(2);
  });
  it('splits exact multi-cell quotations and maps real image anchors', () => {
    const result = scriptAnnotations(script, review);
    expect(result[0].anchors).toEqual([{ location: '脚本!D10', start: 2, end: 6, kind: 'text' }, { location: '脚本!D11', start: 0, end: 3, kind: 'text' }]);
    expect(result[1].anchors[0]).toMatchObject({ location: '脚本!E10', imageId: 'im', kind: 'image' });
    expect(result[2].anchors).toEqual([]);
  });
  it('does not attach another version or an invalid report to the current script', () => {
    expect(scriptAnnotations({ ...script, scriptVersionId: 's2' }, review)).toEqual([]);
    expect(scriptAnnotations(script, { ...review, status: 'failed' })).toEqual([]);
  });
});
