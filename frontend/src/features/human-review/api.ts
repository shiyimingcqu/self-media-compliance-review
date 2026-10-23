// D 人工待办/复核 API 层。
// 后端 GET /human-reviews、/human-reviews/:id 当前为 501 stub（无 response_model），
// 契约中无 200 数据 schema —— 本文件不手写 HumanReview 实体类型（不另造契约）。
// 200 响应一律先当 unknown 做有限运行时 shape 检查（见 classify 函数），
// 类型泛型仅声明框架级通用 envelope 字段，不作为业务字段验证。
import { api, ApiError } from '../../shared/api/client';
import type { Envelope, PageEnvelope } from '../../shared/api/types';

export const humanReviewApi = {
  list: (query: string, signal?: AbortSignal) =>
    api<PageEnvelope<unknown>>(`/human-reviews${query ? `?${query}` : ''}`, { signal }),
  detail: (id: string, signal?: AbortSignal) =>
    api<Envelope<unknown>>(`/human-reviews/${encodeURIComponent(id)}`, { signal }),
};

// ---------- 加载状态判定（纯函数，供页面与测试共用） ----------

export type ReviewsLoadState =
  | { kind: 'module-not-implemented' }          // 501 MODULE_NOT_IMPLEMENTED：后端明确未接入
  | { kind: 'not-found' }                       // 404：资源不存在（详情页）
  | { kind: 'error'; message: string }          // 网络失败 / 5xx / 200 畸形结构
  | { kind: 'empty' }                           // 200 合法分页且 total === 0
  | { kind: 'contract-pending'; total: number }; // 200 合法分页但 total > 0：数据契约未接入，不能展示记录

/** 把查询错误分类为页面状态。只认错误码，不猜测原因。 */
export function classifyReviewsError(error: unknown): ReviewsLoadState {
  if (error instanceof ApiError) {
    if (error.status === 501 && error.code === 'MODULE_NOT_IMPLEMENTED') return { kind: 'module-not-implemented' };
    if (error.status === 404) return { kind: 'not-found' };
    return { kind: 'error', message: error.message };
  }
  return { kind: 'error', message: error instanceof Error ? error.message : '加载失败' };
}

/**
 * 200 响应的有限运行时 shape 检查：仅验证框架级通用分页字段——
 * data 为数组、长度不超过 pageSize、total/page/pageSize 为安全正整数
 * 且相互一致（total=0 时 data 必须为空）。不检查、不断言行实体的
 * 任何业务字段 —— 它们属后端未来契约，未接入前不渲染。
 * 结构畸形或矛盾按错误处理，不把类型声明当验证。
 */
export function classifyListSuccess(body: unknown): ReviewsLoadState {
  if (typeof body !== 'object' || body === null) return { kind: 'error', message: '响应结构异常' };
  const page = body as Record<string, unknown>;
  const wellFormed = Array.isArray(page.data)
    && typeof page.total === 'number' && Number.isSafeInteger(page.total) && page.total >= 0
    && typeof page.page === 'number' && Number.isSafeInteger(page.page) && page.page >= 1
    && typeof page.pageSize === 'number' && Number.isSafeInteger(page.pageSize) && page.pageSize >= 1
    && (page.data as unknown[]).length <= (page.pageSize as number)
    && !(page.total === 0 && (page.data as unknown[]).length > 0);
  if (!wellFormed) return { kind: 'error', message: '响应结构异常' };
  const total = page.total as number;
  return total === 0 ? { kind: 'empty' } : { kind: 'contract-pending', total };
}

/**
 * 详情 200 响应判定：至少验证框架级 envelope 形状（对象且含 data 字段），
 * 非对象 / 缺 data 判畸形错误；结构合法才显示“资源存在、数据契约尚未接入”
 * 提示，不编造业务字段。
 */
export function classifyDetailSuccess(body: unknown): ReviewsLoadState {
  if (typeof body !== 'object' || body === null) return { kind: 'error', message: '响应结构异常' };
  if (!('data' in body)) return { kind: 'error', message: '响应结构异常' };
  return { kind: 'contract-pending', total: 0 }; // total 仅复用类型；detail 页不展示该值
}
