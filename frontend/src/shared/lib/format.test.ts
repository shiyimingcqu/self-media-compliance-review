import { describe, expect, it } from 'vitest';
import { formatDate, formatVideoTime } from './format';

describe('时间显示契约', () => {
  it('按视频毫秒显示短视频及超过一小时的区间', () => {
    expect(formatVideoTime(36_133)).toBe('00:36');
    expect(formatVideoTime(3_661_000)).toBe('01:01:01');
    expect(formatVideoTime(-1)).toBe('00:00');
  });
  it('把 UTC 时间按用户时区显示', () => {
    const display = formatDate('2026-10-19T00:00:00Z');
    expect(display).toContain('08:00');
    expect(display).toContain('2026');
  });
});
