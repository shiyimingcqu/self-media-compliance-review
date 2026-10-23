export const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const rows = (value: unknown) => Array.isArray(value) ? value.map(record) : [];
export function reasonSummary(description: string): string {
  const clean = description.replace(/\s+/g, ' ').trim();
  if (clean.length <= 110) return clean;
  // Preserve the source wording; this is a preview, never a second AI judgment.
  const conclusion = clean.split(/(?<=[。！？])/u).reverse().find(sentence => /^(因此|综上|总体|综合来看|据此|结论[：:])/u.test(sentence.trim()));
  if (conclusion && conclusion.trim().length <= 130) return conclusion.trim();
  const sentence = clean.match(/^.{12,110}?[。！？]/u)?.[0];
  return sentence || `${clean.slice(0, 108)}…`;
}
export function secondsLabel(ms: number): string { return `${Number((ms / 1000).toFixed(2))} 秒`; }
export type ReviewAnchor = { id: string; key: string; label: string; ms: number; endMs?: number; status: string; artifactId?: string };
export function reviewAnchors(resultValue: unknown, snapshotValue: unknown): ReviewAnchor[] {
  const result = record(resultValue), rules = rows(record(snapshotValue).rules), pipeline = record(result.pipeline);
  const artifacts = rows(result.artifacts), initial = record(record(pipeline.localization).findings);
  const duration = Number(record(result.media).durationMs || record(record(result.preparation).original).durationMs);
  return rows(record(result.report).findings).flatMap((finding, index) => {
    const rule = rules.find(r => r.id === finding.rule_id), id = String(finding.id || finding.rule_id);
    const status = rule?.importance === 'suggested' || finding.verdict === 'not_applicable' ? 'advisory' : finding.verdict === 'non_compliant' ? 'fail' : finding.verdict === 'compliant' ? 'pass' : 'uncertain';
    const label = `第 ${finding.rule_number || rule?.ruleNumber || index + 1} 条 · ${{ fail:'未通过', uncertain:'待确定', pass:'通过', advisory:'建议' }[status]} · ${rule?.text || '审核定位'}`;
    const points = artifacts.filter(a => Array.isArray(finding.evidence_ids) && finding.evidence_ids.includes(a.id)).map(a => ({ ms:Number(a.startMs), endMs:a.kind === 'clip' ? Number(a.endMs) : undefined, artifactId:a.kind === 'image' ? String(a.id) : undefined }));
    const refined = rows(pipeline.batches).flatMap(batch => rows(record(record(batch.sourceFindings)[String(finding.rule_id)]).windows));
    const windows = [...rows(finding.windows), ...refined, ...rows(record(initial[String(finding.rule_id)]).windows)];
    for (const w of windows) {
      if (typeof w.start !== 'number' || typeof w.end !== 'number' || w.end <= w.start) continue;
      points.push({ ms:w.start * 1000, endMs:w.end * 1000, artifactId:undefined });
    }
    const seen = new Set<number>();
    return points.filter(p => {
      if (!Number.isFinite(p.ms) || p.ms < 0 || (duration > 0 && (p.ms > duration || (Number.isFinite(p.endMs) && p.endMs! > duration))) || seen.has(p.ms)) return false;
      seen.add(p.ms); return true;
    }).map((p, n) => ({ ...p, id, key:`${id}-${n}`, label, status }));
  });
}

export const anchorStatusByFilter: Record<string, string | undefined> = {
  failed: 'fail', uncertain: 'uncertain', passed: 'pass', advisory: 'advisory', all: undefined,
};
/** 右侧选择哪个分类，左侧时间轴就只留同类标记（全部时不筛）。 */
export function anchorsForFilter<T extends { status: string }>(anchors: T[], filter: string): T[] {
  const status = anchorStatusByFilter[filter];
  return status ? anchors.filter(anchor => anchor.status === status) : anchors;
}
