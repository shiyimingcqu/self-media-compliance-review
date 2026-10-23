import { describe, expect, it } from 'vitest';
import { scriptAnnotations } from './scriptAnnotations';
import type { Workflow, ScriptReview } from '../workflow/api';

const script: NonNullable<Workflow['currentScript']> = { scriptVersionId: 's1', versionLabel: 's1', extractionStatus: 'ready', text: null, parseMeta: {}, sourceMap: [{ text: '产品名称错误', location: '脚本!D10' }, { text: '第二句台词', location: '脚本!D11' }] };
const review: ScriptReview = { id: 'r1', scriptVersionId: 's1', ruleVersionId: null, contextKey: 'c', status: 'succeeded', createdAt: '', snapshot: { multimodal: { images: [{ id: 'im', locations: ['脚本!E10'], width: 100, height: 100 }], incomplete: false, uncovered: [] } }, result: { report: { summary: '', findings: [
  { rule_id: 'r1', verdict: 'non_compliant', quote: '名称错误\n第二句', description: '', suggestion: '' },
  { rule_id: 'r2', verdict: 'non_compliant', quote: '', image_ids: ['im'], description: '', suggestion: '' },
  { rule_id: 'r3', verdict: 'non_compliant', quote: '', description: '缺少卖点', suggestion: '' },
] } } };

describe('script comment evidence mapping', () => {
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
