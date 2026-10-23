import { describe, expect, it } from 'vitest';
import { elapsedLabel } from './AiElapsed';

const start = '2026-10-22T10:00:00Z';
const at = (seconds: number) => Date.parse(start) + seconds * 1000;

describe('AI elapsed time', () => {
  it('keeps counting across backend phases using the original creation time', () => {
    for (const status of ['queued', 'processing', 'vision', 'facts']) {
      expect(elapsedLabel({ status, createdAt: start, updatedAt: new Date(at(8)).toISOString() }, at(12))).toBe('已用时 12 秒');
    }
  });
  it('freezes at completion and distinguishes failure and interruption', () => {
    const run = { createdAt: start, updatedAt: new Date(at(7)).toISOString() };
    expect(elapsedLabel({ ...run, status: 'succeeded' }, at(60))).toBe('7 秒完成');
    expect(elapsedLabel({ ...run, status: 'succeeded' }, at(600))).toBe('7 秒完成');
    expect(elapsedLabel({ ...run, status: 'failed' }, at(600))).toBe('7 秒 · 未完成');
    expect(elapsedLabel({ ...run, status: 'interrupted' }, at(600))).toBe('7 秒 · 已中断');
  });
  it('preserves original call duration after subsequent local report repairs', () => {
    expect(elapsedLabel({ status: 'succeeded', createdAt: start, updatedAt: new Date(at(600)).toISOString(), result: { durationMs: 4500 } }, at(800))).toBe('5 秒完成');
    expect(elapsedLabel({ status: 'succeeded', createdAt: start, updatedAt: new Date(at(600)).toISOString(), result: { elapsedMs: 20187 } }, at(800))).toBe('21 秒完成');
  });
  it('does not invent duration for old records without an end timestamp', () => {
    expect(elapsedLabel({ status: 'succeeded', createdAt: start }, at(60))).toBeNull();
    expect(elapsedLabel({ status: 'succeeded', createdAt: start, updatedAt: 'invalid' }, at(60))).toBeNull();
  });
});
