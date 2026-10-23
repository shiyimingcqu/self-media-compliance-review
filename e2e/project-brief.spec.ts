import { test, expect } from '@playwright/test';
const base = 'http://127.0.0.1:8001/api/v1';
test('项目Brief替换保留历史，未配置Key时自动处理明确暂停', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const project = (await (await request.post(`${base}/projects`, { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '麦咖啡芝士浓奶铁抖音项目', brandName: '麦咖啡', platforms: ['douyin'] } })).json()).data;
  await page.goto(`/projects/${project.id}`);
  await expect(page.getByText('品牌内容审核工作流', { exact: true })).toHaveCount(0);
  await expect(page.getByText('还没有项目 Brief，请先上传品牌要求。')).toBeVisible();
  for (const version of [1, 2]) {
    await page.getByLabel('上传项目Brief').setInputFiles({ name: `brief-v${version}.txt`, mimeType: 'text/plain', buffer: Buffer.from(`产品名称为芝士浓奶铁。\n第${version}版品牌要求：禁止提及提神醒脑功效。`) });
    await page.getByRole('button', { name: '上传并解析', exact: true }).click();
    await expect(page.getByText(`解析完成 · V${version}`, { exact: true })).toBeVisible();
  }
  await expect(page.locator('.project-brief-summary strong')).toHaveText('brief-v2.txt');
  await expect(page.locator('.project-brief-summary')).toHaveCount(1);
  await page.getByRole('button', { name: '查看解析内容', exact: true }).click();
  await expect(page.locator('.brief-text-source').getByText('第2版品牌要求：禁止提及提神醒脑功效。')).toBeVisible();
  await page.getByRole('link', { name: '返回项目与达人', exact: true }).click();
  await page.screenshot({ path: 'test-results/project-brief-panel.png', fullPage: true });
  await page.reload();
  await expect(page.getByText('解析完成 · V2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '脚本规则', exact: true }).click();
  await expect(page.getByText(/^当前项目 Brief：brief-v2.txt/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'AI 生成规则', exact: true })).toBeDisabled();
  const current = (await (await request.get(`${base}/projects/${project.id}/brief`)).json()).data;
  expect(current.revision).toBe(2); expect(current.brief.title).toBe('brief-v2.txt');
  expect(current.brief.processing.status).toBe('blocked');
  expect(errors).toEqual([]);
});
