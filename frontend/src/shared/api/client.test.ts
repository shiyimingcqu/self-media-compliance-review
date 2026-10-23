import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, uploadFile } from './client';

afterEach(() => vi.unstubAllGlobals());

describe('文件上传幂等键', () => {
  it('设置调用方传入的 Idempotency-Key，重试沿用同 key、新上传换新 key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { id: 'f1' }, requestId: 'trace-9' }), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
    const file = new File([new Uint8Array([1, 2, 3])], 'brief.pdf', { type: 'application/pdf' });
    const key = 'stable-key-0001';
    await uploadFile(file, 'brief', key);
    await uploadFile(file, 'brief', key); // 同一次操作的重试：复用 key
    const [path1, init1] = fetchMock.mock.calls[0];
    const [path2, init2] = fetchMock.mock.calls[1];
    expect(String(path1)).toContain('/files');
    expect(new Headers(init1.headers).get('idempotency-key')).toBe(key);
    expect(new Headers(init2.headers).get('idempotency-key')).toBe(key);
    // FormData 直传：helper 不手写 Content-Type（浏览器/undici 自动带 boundary）
    expect(new Headers(init1.headers).get('content-type')).toBeNull();
    // 载荷保持：文件名与 purpose 均在 FormData
    const form1 = init1.body as FormData;
    expect(form1.get('purpose')).toBe('brief');
    expect((form1.get('file') as File).name).toBe('brief.pdf');
    // 新上传（新 key）与隐式随机无关：由调用方决定
    await uploadFile(file, 'script', 'another-key-0002');
    const init3 = fetchMock.mock.calls[2][1];
    expect(new Headers(init3.headers).get('idempotency-key')).toBe('another-key-0002');
    expect((init3.body as FormData).get('purpose')).toBe('script');
  });
  it('过短/空白 idempotencyKey 本地拒绝，不发请求', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const file = new File([new Uint8Array([1])], 'a.pdf');
    await expect(uploadFile(file, 'brief', 'abc')).rejects.toBeInstanceOf(ApiError);
    await expect(uploadFile(file, 'brief', '        ')).rejects.toMatchObject({ code: 'VALIDATION_ERROR' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
describe('统一接口客户端', () => {
  it('保留未实现接口的错误码和 requestId，不把失败作为成功结果', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'MODULE_NOT_IMPLEMENTED', message: '尚未接入' }, requestId: 'trace-1' }), { status: 501 })));
    await expect(api('/human-reviews')).rejects.toMatchObject({ status: 501, code: 'MODULE_NOT_IMPLEMENTED', requestId: 'trace-1' });
  });
  it('网络中断给出可操作提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed to fetch')));
    await expect(api('/projects')).rejects.toBeInstanceOf(ApiError);
    await expect(api('/projects')).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
  });
});
