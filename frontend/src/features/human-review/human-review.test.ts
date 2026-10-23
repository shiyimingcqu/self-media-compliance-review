// D 人工待办前端纯逻辑测试（node 环境，无渲染断言能力 —— 渲染/E2E 覆盖缺口见任务报告）。
// 只证明 API 调用行为与加载状态分类纯函数；组件渲染路径由 typecheck/build 保证（不等于渲染断言）。
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../shared/api/client';
import { humanReviewApi, classifyDetailSuccess, classifyListSuccess, classifyReviewsError } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('humanReviewApi 调用', () => {
  it('list 真实请求 /human-reviews，501 时保留错误码与 requestId（不把失败当成功）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: 'MODULE_NOT_IMPLEMENTED', message: '此模块尚未接入，骨架不生成审核结果' }, requestId: 'trace-501' }),
      { status: 501 })));
    await expect(humanReviewApi.list('')).rejects.toMatchObject(
      { status: 501, code: 'MODULE_NOT_IMPLEMENTED', requestId: 'trace-501' });
  });

  it('detail 请求路径携带 reviewId，404 返回 RESOURCE_NOT_FOUND', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: 'RESOURCE_NOT_FOUND', message: '资源不存在' }, requestId: 'trace-404' }),
      { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(humanReviewApi.detail('rev-9')).rejects.toMatchObject({ status: 404, code: 'RESOURCE_NOT_FOUND' });
    expect(String(fetchMock.mock.calls[0][0])).toContain('/human-reviews/rev-9');
  });

  it('list 200 合法分页：通用字段透传（total 可用，行实体保持 unknown）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ data: [], requestId: 'trace-ok', page: 1, pageSize: 10, total: 0 }), { status: 200 })));
    const body = await humanReviewApi.list('');
    expect(body.total).toBe(0);
    expect(body.requestId).toBe('trace-ok');
  });
});

describe('classifyReviewsError 错误分类', () => {
  it('501 + MODULE_NOT_IMPLEMENTED 判为未接入', () => {
    expect(classifyReviewsError(new ApiError(501, 'MODULE_NOT_IMPLEMENTED', '尚未接入'))).toEqual({ kind: 'module-not-implemented' });
  });
  it('404 判为不存在', () => {
    expect(classifyReviewsError(new ApiError(404, 'RESOURCE_NOT_FOUND', '资源不存在'))).toEqual({ kind: 'not-found' });
  });
  it('网络失败（status 0）与 5xx 判为可重试错误，消息透传', () => {
    expect(classifyReviewsError(new ApiError(0, 'NETWORK_ERROR', '无法连接后端'))).toEqual({ kind: 'error', message: '无法连接后端' });
    expect(classifyReviewsError(new ApiError(500, 'INTERNAL_ERROR', '服务暂时不可用'))).toEqual({ kind: 'error', message: '服务暂时不可用' });
  });
  it('501 但错误码不同（如网关）不判为未接入，归错误', () => {
    expect(classifyReviewsError(new ApiError(501, 'BAD_GATEWAY', '网关异常'))).toEqual({ kind: 'error', message: '网关异常' });
  });
  it('非 ApiError 异常兜底为错误，不抛出', () => {
    expect(classifyReviewsError(new TypeError('boom'))).toEqual({ kind: 'error', message: 'boom' });
    expect(classifyReviewsError('strange')).toEqual({ kind: 'error', message: '加载失败' });
  });
});

describe('classifyListSuccess 200 运行时 shape 检查', () => {
  it('合法分页 total=0 判为空态', () => {
    expect(classifyListSuccess({ data: [], page: 1, pageSize: 10, total: 0, requestId: 'r' })).toEqual({ kind: 'empty' });
  });
  it('合法分页 total>0 判为契约未接入（不渲染记录）', () => {
    expect(classifyListSuccess({ data: [{ id: 'x' }], page: 1, pageSize: 10, total: 3, requestId: 'r' }))
      .toEqual({ kind: 'contract-pending', total: 3 });
  });
  it('畸形结构一律拒绝：非对象 / data 非数组 / total 非整数 / page<1 / pageSize<1', () => {
    expect(classifyListSuccess(null)).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess('ok')).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: {}, page: 1, pageSize: 10, total: 0 })).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [], page: 1, pageSize: 10, total: -1 })).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [], page: 1, pageSize: 10, total: 1.5 })).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [], page: 1, pageSize: 10, total: Number.MAX_SAFE_INTEGER + 1 })).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [], page: 1, pageSize: 10, total: Number.NaN })).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [], page: 0, pageSize: 10, total: 0 })).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [], page: 1, pageSize: 0, total: 0 })).toEqual({ kind: 'error', message: '响应结构异常' });
  });
  it('矛盾响应拒绝：data 超出 pageSize / total=0 但 data 非空', () => {
    expect(classifyListSuccess({ data: new Array(11), page: 1, pageSize: 10, total: 11 }))
      .toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyListSuccess({ data: [{ id: 'x' }], page: 1, pageSize: 10, total: 0 }))
      .toEqual({ kind: 'error', message: '响应结构异常' });
  });
});

describe('classifyDetailSuccess 详情 200 判定', () => {
  it('结构合法（对象含 data）只意味着契约未接入，不编造字段', () => {
    expect(classifyDetailSuccess({ data: { anything: true } })).toEqual({ kind: 'contract-pending', total: 0 });
  });
  it('null / 非对象 / 缺 data 判畸形错误，不判契约未接入', () => {
    expect(classifyDetailSuccess(null)).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyDetailSuccess('ok')).toEqual({ kind: 'error', message: '响应结构异常' });
    expect(classifyDetailSuccess({ requestId: 'r' })).toEqual({ kind: 'error', message: '响应结构异常' });
  });
});
