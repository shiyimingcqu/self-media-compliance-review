export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fieldErrors?: Record<string, string>,
    public requestId?: string,
  ) { super(message); }
}

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch(`${base}${path}`, { ...init, headers }); }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接后端，请确认 API 服务已启动');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, body?.error?.code || 'HTTP_ERROR', body?.error?.message || '请求失败', body?.error?.fieldErrors, body?.requestId);
  return body as T;
}

/** 受控文件上传：multipart，purpose=brief|script|video；返回服务端 FileAsset 形状。
 * idempotencyKey 由调用方显式传入并在同一次操作的重试中复用（新上传才用新 key）；
 * 不在本 helper 内隐式随机换 key。FormData 直传，不手写 Content-Type。 */
export async function uploadFile(
  file: File,
  purpose: 'brief' | 'script' | 'video',
  idempotencyKey: string,
): Promise<import('./types').Envelope<import('./types').FileAsset>> {
  if (!idempotencyKey || idempotencyKey.trim().length < 8 || idempotencyKey.length > 128) {
    throw new ApiError(0, 'VALIDATION_ERROR', 'Idempotency-Key 必须为 8–128 个非空白字符');
  }
  const form = new FormData();
  form.append('file', file);
  form.append('purpose', purpose);
  return api('/files', { method: 'POST', body: form, headers: { 'Idempotency-Key': idempotencyKey } });
}

/** 受控原件下载地址（GET / Range 由浏览器与后端处理；不接受任意本地路径）。 */
export function fileContentUrl(fileId: string): string {
  return `${base}/files/${encodeURIComponent(fileId)}/content`;
}
