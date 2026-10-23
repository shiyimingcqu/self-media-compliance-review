import type { ScriptAnnotation } from './scriptAnnotations';

export function AnnotationText({ text, location, annotations, activeId, onSelect, sourceText }: { text: string; sourceText?: string; location: string; annotations: ScriptAnnotation[]; activeId?: string; onSelect: (id: string) => void }) {
  const shift = sourceText ? text.indexOf(sourceText) : 0;
  const ranges = annotations.flatMap(a => a.anchors.filter(p => p.location === location && p.kind === 'text' && shift >= 0).map(p => ({ id: a.id, start: p.start + shift, end: p.end + shift, verdict: a.finding.verdict })));
  const boundaries = [...new Set([0, text.length, ...ranges.flatMap(r => [r.start, r.end])])].filter(n => n >= 0 && n <= text.length).sort((a, b) => a - b);
  return <>{boundaries.slice(0, -1).map((start, i) => {
    const end = boundaries[i + 1], hits = ranges.filter(r => r.start < end && r.end > start);
    if (!hits.length) return text.slice(start, end);
    const hit = hits.find(r => r.id === activeId) || hits[0];
    return <mark key={start} data-source-comment={hit.id} className={`review-text-mark ${hit.verdict} ${hit.id === activeId ? 'active' : ''}`} role="button" tabIndex={0} aria-label={`查看原文评论 ${annotations.find(a => a.id === hit.id)?.index}`} onClick={() => onSelect(hit.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(hit.id); } }}>{text.slice(start, end)}</mark>;
  })}</>;
}
