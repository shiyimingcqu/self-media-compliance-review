import { test, expect } from '@playwright/test';
const base = 'http://127.0.0.1:8001/api/v1';
const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });

test('规则独立页面：紧凑逐条核对、搜索、切换、保存与离开保护', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const project = (await (await request.post(`${base}/projects`, { headers: headers(), data: { name: '三类规则验收', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  await page.goto(`/projects/${project.id}`);
  for (const title of ['脚本规则', '图文规则', '视频规则']) {
    const button = page.getByRole('button', { name: title, exact: true });
    await expect(button).toBeVisible();
    const box = await button.boundingBox(); expect(box!.height).toBeGreaterThan(100);
  }
  await page.getByRole('button', { name: '图文规则', exact: true }).click();
  const drawer = page.locator('.project-rules-form');
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/rules\\?type=image$`));
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: '图文规则', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const row = drawer.getByRole('article', { name: '图文第1条规则', exact: true });
  expect((await row.boundingBox())!.height).toBeLessThan(140);
  await drawer.getByRole('textbox', { name: '图文第1条要求', exact: true }).fill('至少四张产品图片');
  await drawer.getByRole('button', { name: '脚本规则', exact: true }).click();
  const originalScript = await drawer.getByRole('textbox', { name: '第1条要求', exact: true }).inputValue();
  await drawer.getByRole('button', { name: '视频规则', exact: true }).click();
  await expect(drawer.getByRole('textbox', { name: '视频第1条要求', exact: true })).not.toBeEmpty();
  await drawer.getByRole('button', { name: '图文规则', exact: true }).click();
  await expect(drawer.getByRole('textbox', { name: '图文第1条要求', exact: true })).toHaveValue('至少四张产品图片');
  await drawer.getByRole('textbox', { name: '图文搜索规则' }).fill('至少四张');
  await expect(drawer.getByRole('article')).toHaveCount(1);
  await drawer.getByRole('textbox', { name: '图文搜索规则' }).clear();
  await page.getByRole('link', { name: '返回项目', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '还有未保存的规则' })).toBeVisible();
  await page.getByRole('button', { name: '继续编辑', exact: true }).click();
  await expect(drawer.getByRole('textbox', { name: '图文第1条要求', exact: true })).toHaveValue('至少四张产品图片');
  await page.screenshot({ path: 'test-results/three-rule-buttons.png', fullPage: true });
  const saved = page.waitForResponse(r => r.url().endsWith('/standards/confirm') && r.request().method() === 'POST');
  await drawer.getByRole('button', { name: '保存项目审核规则' }).click();
  expect((await saved).status()).toBe(200);
  await expect(page.getByText('已确认 V1', { exact: true })).toBeVisible();
  await page.reload();
  await expect(drawer.getByRole('textbox', { name: '图文第1条要求', exact: true })).toHaveValue('至少四张产品图片');
  await drawer.getByRole('button', { name: '脚本规则', exact: true }).click();
  await expect(drawer.getByRole('textbox', { name: '第1条要求', exact: true })).toHaveValue(originalScript);
  expect(errors).toEqual([]);
});
