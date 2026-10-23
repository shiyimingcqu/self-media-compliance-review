import { test, expect } from '@playwright/test';
const base = 'http://127.0.0.1:8001/api/v1';
const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });

test('项目详情在笔记本一屏展示，五行分页跨接口页不漏达人', async ({ page, request }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const created = await request.post(`${base}/projects`, { headers: headers(), data: { name: '麦咖啡芝士浓奶铁抖音项目一屏验收', brandName: '麦咖啡', platforms: ['douyin'] } });
  expect(created.status()).toBe(201);
  const project = (await created.json()).data;
  const template = (await (await request.get(`${base}/workflow/standard-templates`)).json()).data[0];
  expect((await request.post(`${base}/projects/${project.id}/standards/confirm`, { headers: headers(), data: { scriptRules: template.scriptRules, videoRules: template.videoRules, scriptConfirmed: true, videoConfirmed: true, confirmationNote: '测试项目规则', revision: 0 } })).status()).toBe(200);
  const ids: string[] = [];
  for (let i = 0; i < 12; i++) {
    const response = await request.post(`${base}/projects/${project.id}/creators`, { headers: headers(), data: { creatorName: `达人${i}`, platform: 'douyin' } });
    expect(response.status()).toBe(201); ids.push((await response.json()).data.id);
  }
  // Include the partial-parse warning so the layout covers a taller Brief card.
  await page.route(`**/api/v1/projects/${project.id}/brief`, route => route.fulfill({ json: { data: { projectId: project.id, revision: 1, brief: { title: '麦咖啡芝士浓奶铁抖音Brief.pdf', revision: 1, fileId: 'fixture', sourceVersionId: 'fixture', blocks: [{ id: 'b', text: '品牌原文', ordinal: 0 }], pageCount: 12, uncoveredSources: Array.from({ length: 12 }, (_, i) => `page:${i + 1}`) } } } }));
  await page.goto(`/projects/${project.id}`);
  await expect(page.locator('.ant-table-row')).toHaveCount(5);
  await expect(page.getByText('有局部提示 · V1', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '查看待核对', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/project-one-screen.png', fullPage: true });
  const dimensions = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, viewport: innerHeight }));
  expect(dimensions.height).toBeLessThanOrEqual(dimensions.viewport);
  for (const title of ['脚本规则', '图文规则', '视频规则', '添加达人']) await expect(page.getByRole('button', { name: title, exact: true })).toBeInViewport();
  const seen = new Set(await page.locator('.ant-table-row').evaluateAll(rows => rows.map(r => r.getAttribute('data-row-key'))));
  for (const n of [2, 3]) {
    await page.locator(`.ant-pagination-item-${n}`).click();
    await expect(page.locator('.ant-table-row')).toHaveCount(n === 2 ? 5 : 2);
    if (n === 2) await expect(page.locator('.ant-table-row').first()).not.toHaveAttribute('data-row-key', [...seen][0]!);
    for (const id of await page.locator('.ant-table-row').evaluateAll(rows => rows.map(r => r.getAttribute('data-row-key')))) seen.add(id);
  }
  expect([...seen].sort()).toEqual(ids.sort());
});
