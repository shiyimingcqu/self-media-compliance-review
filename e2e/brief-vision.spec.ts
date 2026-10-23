import { test, expect } from '@playwright/test';

test('Brief图文解析可启动、跟踪并逐页核对，刷新复用结果', async ({ page, request }) => {
  const project = (await (await request.post('http://127.0.0.1:8001/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '图文Brief测试', brandName: '麦咖啡', platforms: ['douyin'] } })).json()).data;
  let started = false, completed = false, calls = 0;
  const run = () => ({ id: 'visionmock', sourceVersionId: 'source', status: completed ? 'succeeded' : 'processing', snapshot: { model: 'omni-test', pages: [{ pageNo: 1, width: 2200, height: 1238 }] }, result: completed ? { report: { pages: [{ pageNo: 1, text: '品牌要求：产品完整露出，禁止镜像。', visualDescription: '左侧为要求清单，右侧为产品参考图。', readability: 'readable', uncertainties: [] }], warnings: [] }, coverageComplete: true } : null });
  await page.route('**/api/v1/settings/models', r => r.fulfill({ json: { data: { hasKey: true, revision: 1, model: 'omni-test' } } }));
  await page.route(`**/api/v1/projects/${project.id}/brief`, r => r.fulfill({ json: { data: { projectId: project.id, revision: 1, brief: { title: '图文Brief.pdf', revision: 1, sourceVersionId: 'source', fileId: 'file', pageCount: 1, blocks: completed ? [{ id: 'vision:1', text: '品牌要求：产品完整露出，禁止镜像。', pageNo: 1, origin: 'ai-visual-extraction' }, {id:'vision:visual',text:'左侧为要求清单，右侧为产品参考图。',pageNo:1,origin:'ai-visual-extraction'}] : [], uncoveredSources: completed ? [] : ['page:1'], visionRunId: completed ? 'visionmock' : null, visualRun: started ? run() : null } } } }));
  await page.route(`**/api/v1/projects/${project.id}/brief/vision`, async r => {
    const body = r.request().postDataJSON();
    expect(body).toEqual({ sourceVersionId: 'source', briefRevision: 1, configRevision: 1, allowRealCall: true, extractFacts: true });
    expect(r.request().headers()['idempotency-key']).toBeTruthy();
    calls++; started = true;
    await r.fulfill({ status: 202, json: { data: run() } });
  });
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.route('**/brief/vision/visionmock/pages/1', r => r.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')}));
  await page.goto(`/projects/${project.id}`);
  await page.getByRole('button', { name: 'AI 图文解析', exact: true }).click();
  await expect(page.getByText('AI 图文解析中 · V1', { exact: true })).toBeVisible();
  completed = true;
  await expect(page.getByText('AI 图文已解析 · V1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '查看解析内容', exact: true }).click();
  await expect(page).toHaveURL(/brief-review/);
  await page.getByRole('tab', { name: '识读原文', exact: true }).click();
  await expect(page.getByText('品牌要求：产品完整露出，禁止镜像。', {exact:true})).toBeVisible();
  await expect(page.getByText('左侧为要求清单，右侧为产品参考图。', {exact:true})).toBeVisible();
  await expect(page.getByAltText('Brief第1页')).toHaveAttribute('src', `/api/v1/projects/${project.id}/brief/vision/visionmock/pages/1`);
  await page.getByRole('link', { name: '返回项目与达人', exact: true }).click();
  await page.reload();
  await expect(page.getByText('AI 图文已解析 · V1', { exact: true })).toBeVisible();
  expect(calls).toBe(1); expect(errors).toEqual([]);
});
