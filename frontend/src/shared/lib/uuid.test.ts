import { expect, it, vi } from 'vitest';
import { installUuidFallback } from './uuid';
it('uses secure random bytes and produces UUID v4 on HTTP', () => {
  const getRandomValues = vi.fn((bytes:Uint8Array) => { bytes.fill(255); return bytes; });
  const target = { getRandomValues } as unknown as Crypto;
  installUuidFallback(target);
  expect(target.randomUUID()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff');
  expect(getRandomValues).toHaveBeenCalledOnce();
});
it('preserves the native implementation', () => {
  const native = vi.fn(); const target = { randomUUID:native } as unknown as Crypto;
  installUuidFallback(target);
  expect(target.randomUUID).toBe(native);
});
