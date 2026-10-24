import { describe, expect, it } from 'vitest';
import { anchorsForFilter, highlightRanges, reasonSummary, reviewAnchors, secondsLabel } from './reviewPresentation';
describe('video review presentation', () => {
  it('keeps a short original reason and does not invent a judgment', () => {
    const first = '杯身品牌被遮挡，无法确认指定产品。';
    expect(reasonSummary(first + '这是详细的检查说明。'.repeat(20))).toBe(first);
    expect(reasonSummary('资料不足')).toBe('资料不足');
    expect(reasonSummary('这是详细的检查说明。'.repeat(20) + '因此未能确认指定产品出现。')).toBe('因此未能确认指定产品出现。');
    expect(reasonSummary('很长的原始观察'.repeat(50)).length).toBeLessThanOrEqual(110);
    expect(secondsLabel(97500)).toBe('97.5 秒');
  });
  it('marks uncertain localization without artifacts, all evidence points, and source-time refinement windows', () => {
    const result = { media:{ durationMs:20000 }, report:{ findings:[{ id:'i',rule_id:'r',verdict:'needs_confirmation',evidence_ids:['a','b'] }] }, artifacts:[{ id:'a',kind:'image',startMs:1000 },{ id:'b',kind:'clip',startMs:5000,endMs:8000 }], pipeline:{ localization:{ findings:{ r:{ windows:[{ start:1,end:2 },{ start:12,end:13 },{ start:-1,end:3 },{ start:19,end:22 }] } } }, batches:[{ sourceFindings:{ r:{ windows:[{ start:9,end:10 }] } } }] } };
    const anchors = reviewAnchors(result, { rules:[{ id:'r',text:'产品辨认',importance:'must' }] });
    expect(anchors.map(a => a.ms)).toEqual([1000,5000,9000,12000]);
    expect(anchors.every(a => a.status === 'uncertain' && a.id === 'i')).toBe(true);
    expect(new Set(anchors.map(a => a.key)).size).toBe(4);
    expect(anchors[0].artifactId).toBe('a');
  });
  it('does not invent a position for global absence or use clip-local timestamps', () => {
    expect(reviewAnchors({ report:{ findings:[{ id:'i',rule_id:'r',verdict:'non_compliant',observation_type:'absence' }] }, pipeline:{ batches:[{ findings:{ r:{ windows:[{ start:2,end:3 }] } } }] } }, {})).toEqual([]);
  });

  it('filters timeline anchors by the selected category', () => {
    const anchors = [
      { id: 'a', status: 'fail' }, { id: 'b', status: 'uncertain' },
      { id: 'c', status: 'pass' }, { id: 'd', status: 'advisory' },
    ];
    expect(anchorsForFilter(anchors, 'failed').map(a => a.id)).toEqual(['a']);
    expect(anchorsForFilter(anchors, 'uncertain').map(a => a.id)).toEqual(['b']);
    expect(anchorsForFilter(anchors, 'passed').map(a => a.id)).toEqual(['c']);
    expect(anchorsForFilter(anchors, 'advisory').map(a => a.id)).toEqual(['d']);
    expect(anchorsForFilter(anchors, 'all')).toHaveLength(4);
  });

  it('merges one rule into highlight bands and skips bare points', () => {
    expect(highlightRanges([{ ms: 1000, endMs: 3000 }])).toEqual([[1000, 3000]]);
    expect(highlightRanges([{ ms: 1000, endMs: 3000 }, { ms: 2800, endMs: 5000 }])).toEqual([[1000, 5000]]);
    expect(highlightRanges([{ ms: 1000, endMs: 2000 }, { ms: 6000, endMs: 7000 }])).toEqual([[1000, 2000], [6000, 7000]]);
    expect(highlightRanges([{ ms: 1000 }])).toEqual([]);
  });
});
