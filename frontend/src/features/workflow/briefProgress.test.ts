import { describe, expect, it } from 'vitest';
import { briefProgressLabel } from './briefProgress';
import type { VisualRun } from './projectBriefApi';

describe('Brief progress', () => {
  it('shows the active phase and completed batch count', () => {
    const run = { status: 'processing', result: { progress: { stage: 'ocr', completed: 1, total: 2 } } } as VisualRun;
    expect(briefProgressLabel(run)).toBe('识读页面与区域 · 1/2 批');
    run.result!.progress!.stage = 'summary';
    expect(briefProgressLabel(run)).toBe('提炼全文重点 · 1/2 批');
    run.status = 'succeeded';
    expect(briefProgressLabel(run)).toBeUndefined();
  });
});
