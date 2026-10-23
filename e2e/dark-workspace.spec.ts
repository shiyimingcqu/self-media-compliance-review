import { test, expect } from '@playwright/test';

test('浅色工作台导航、搜索、真实统计和移动端布局', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: '项目列表' })).toBeVisible();
  await expect(page.locator('.desk-nav a')).toHaveCount(6);
  expect(await page.locator('body').evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgb(248, 249, 251)');
  expect(await page.locator('.desk-topbar').evaluate(node => node.getBoundingClientRect().height)).toBe(64);
  await page.screenshot({ path: 'test-results/light-projects-desktop.png', fullPage: true });
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  for (const [name, heading] of [['规则库','规则库'], ['达人反馈','达人反馈'], ['数据看板','审核数据看板']] as const) {
    await page.locator('.desk-nav').getByRole('link', { name, exact: true }).click();
    await expect(page.getByRole('heading', { name: heading, exact: true }), JSON.stringify(errors)).toBeVisible();
  }
  await expect(page.locator('.dashboard-metric')).toHaveCount(4);
  await page.screenshot({ path: 'test-results/light-dashboard-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(await page.locator('.desk-topbar').evaluate(node => node.getBoundingClientRect().top)).toBe(0);
  await page.screenshot({ path: 'test-results/light-dashboard-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});
