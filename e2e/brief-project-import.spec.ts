import { test, expect } from '@playwright/test';
const base = 'http://127.0.0.1:8001/api/v1';

test('项目列表上传 Brief 自动创建入口及自动打开项目', async ({ page, request }) => {
  const project = (await (await request.post(`${base}/projects`, { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: 'Brief 自动创建测试', brandName: '麦咖啡', productNames: ['芝士浓奶铁'], platforms: ['douyin'] } })).json()).data;
  let reads = 0, calls = 0;
  const run = () => ({ id: 'mock-import', fileId: 'file', status: reads < 2 ? 'processing' : 'succeeded', projectId: reads < 2 ? null : project.id, snapshot: { filename: '抖音_麦咖啡_芝士浓奶铁_Brief.txt' } });
  await page.route('**/api/v1/brief-project-imports', async r => {
    if (r.request().method() === 'GET') return r.fulfill({ json: { data: [] } });
    calls++; expect(r.request().postDataJSON().allowRealCall).toBe(true);
    return r.fulfill({ status: 202, json: { data: { ...run(), status: 'queued' } } });
  });
  await page.route('**/api/v1/brief-project-imports/mock-import', r => { reads++; return r.fulfill({ json: { data: run() } }); });
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/projects');
  await page.getByRole('button', { name: '上传 Brief 创建项目', exact: true }).click();
  await expect(page.getByText('模型会结合文件名和首页内容识别项目信息')).toBeVisible();
  await page.screenshot({ path: 'test-results/brief-project-import-upload.png', fullPage: true, animations: 'disabled' });
  await page.getByLabel('选择项目 Brief').setInputFiles({ name: '抖音_麦咖啡_芝士浓奶铁_Brief.txt', mimeType: 'text/plain', buffer: Buffer.from('麦咖啡 芝士浓奶铁 抖音') });
  await page.getByRole('button', { name: '上传并自动创建', exact: true }).click();
  await expect(page.getByText('正在识别项目信息', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(`/projects/${project.id}`);
  await expect(page.locator('.project-detail-header').getByText('芝士浓奶铁', { exact: true })).toBeVisible();
  expect(calls).toBe(1); expect(errors).toEqual([]);
});

test('识别缺少平台时补充确认，不再调用识别模型；刷新可找回记录', async ({ page, request }) => {
  const project = (await (await request.post(`${base}/projects`, { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '补充平台测试', brandName: '麦咖啡', productNames: ['芝士浓奶铁'], platforms: ['douyin'] } })).json()).data;
  let completes = 0;
  const run = { id: 'pending-import', fileId: 'file', status: 'needs_input', snapshot: { filename: '麦咖啡_芝士浓奶铁_Brief.pdf' }, result: { metadata: { projectName: '补充平台测试', brandName: '麦咖啡', productNames: ['芝士浓奶铁'], platforms: [], warnings: ['文件名和首页未明确投放平台'], evidence: [{ field: 'brandName', value: '麦咖啡', source: 'filename', quote: '麦咖啡' }] } } };
  await page.route('**/api/v1/brief-project-imports', r => { expect(r.request().method()).toBe('GET'); return r.fulfill({ json: { data: [run] } }); });
  await page.route('**/api/v1/brief-project-imports/pending-import', r => r.fulfill({ json: { data: run } }));
  await page.route('**/api/v1/brief-project-imports/pending-import/complete', r => {
    completes++; const body = r.request().postDataJSON();
    expect(body.project.brandName).toBe('麦咖啡'); expect(body.project.productNames).toEqual(['芝士浓奶铁']); expect(body.project.platforms).toEqual(['douyin']);
    return r.fulfill({ json: { data: { ...run, status: 'succeeded', projectId: project.id } } });
  });
  await page.goto('/projects');
  await page.reload();
  await page.getByRole('button', { name: /导入记录/ }).click();
  await expect(page.getByLabel('主品牌')).toHaveValue('麦咖啡');
  await expect(page.getByRole('checkbox', { name: '抖音', exact: true })).not.toBeChecked();
  await page.screenshot({ path: 'test-results/brief-project-import-confirm.png', fullPage: true, animations: 'disabled' });
  await page.getByRole('checkbox', { name: '抖音', exact: true }).check();
  await page.getByRole('button', { name: '确认信息并创建', exact: true }).click();
  await expect(page).toHaveURL(`/projects/${project.id}`); expect(completes).toBe(1);
});
