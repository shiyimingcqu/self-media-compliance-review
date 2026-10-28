import { test, expect } from '@playwright/test';

test('项目列表永久删除：取消保留，确认删除关联任务并保留其他项目', async ({ page, request }) => {
  const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
  const name = `麦咖啡芝士浓奶铁小红书达人种草-${Date.now()}`;
  const create = async (name: string) => {
    const response = await request.post('/api/v1/projects', { headers: headers(), data: { name, brandName: '删除测试品牌', platforms: ['xiaohongshu'] } });
    expect(response.ok()).toBeTruthy();
    return (await response.json()).data;
  };
  const project = await create(name), other = await create(`保留-${name}`);
  const rule = { text: '展示产品', criterion: '可辨认即可', importance: 'must', category: '产品' };
  const taskResponse = await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: '删除验证达人', platform: 'xiaohongshu', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '验证删除关联任务' } });
  expect(taskResponse.ok()).toBeTruthy();
  const task = (await taskResponse.json()).data;
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'brand'));
  await page.goto(`/projects?q=${encodeURIComponent(name)}`);
  const button = page.getByRole('button', { name: `删除项目 ${name}`, exact: true });
  await expect(button).toBeVisible();
  for (const width of [1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1080 });
    const table = page.locator('.project-list-table .ant-table-content');
    expect(await table.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.locator('.project-panel').screenshot({ path: 'test-results/project-list-compact.png', animations: 'disabled' });
  await button.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('此操作无法撤销');
  await dialog.getByRole('button', { name: /^取\s*消$/ }).click();
  await expect(button).toBeVisible();
  expect((await request.get(`/api/v1/tasks/${task.id}`)).ok()).toBeTruthy();
  await button.click();
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: 'test-results/project-delete-confirmation.png', animations: 'disabled' });
  await dialog.getByRole('button', { name: '永久删除', exact: true }).click();
  await expect(button).toHaveCount(0);
  await expect(page.getByRole('link', { name: other.name, exact: true })).toBeVisible();
  expect((await request.get(`/api/v1/projects/${project.id}`)).status()).toBe(404);
  expect((await request.get(`/api/v1/tasks/${task.id}`)).status()).toBe(404);
  expect((await request.get(`/api/v1/projects/${other.id}`)).ok()).toBeTruthy();
  await page.screenshot({ path: 'test-results/project-deletion.png' });
});
