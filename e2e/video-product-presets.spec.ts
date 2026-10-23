import { test, expect } from '@playwright/test';

test('第三层默认产品次数和时间：行内填写、选择启用、关闭保存和回显', async ({ page, request }) => {
  test.setTimeout(90_000);
  const project = (await (await request.post('/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '默认产品检查', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  await page.goto(`/projects/${project.id}/rules?type=video`);
  await page.locator('.video-review-level').getByText('严格版', { exact: true }).click();
  const count = page.getByRole('article', { name: '产品出现次数检查', exact: true });
  const duration = page.getByRole('article', { name: '产品出现时间检查', exact: true });
  await expect(count).toBeVisible(); await expect(duration).toBeVisible();
  async function configure(name: string) {
    const label = name + '至少' + (name.includes('次数') ? '次' : '秒');
    await page.locator('.video-review-level > strong').click();
    await expect(page.getByLabel(label, { exact: true })).toBeHidden();
    await page.getByRole('button', { name: '配置' + name, exact: true }).click();
    await expect(page.getByLabel(label, { exact: true })).toBeVisible();
  }
  await configure('产品出现次数检查');
  await expect(page.getByLabel('产品出现次数检查至少次', { exact: true })).toHaveValue('');
  await configure('产品出现时间检查');
  await expect(page.getByLabel('产品出现时间检查至少秒', { exact: true })).toHaveValue('');
  await expect(page.getByRole('switch', { name: '启用产品出现次数检查', exact: true })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '启用产品出现时间检查', exact: true })).not.toBeChecked();
  await configure('产品出现次数检查');
  await page.getByLabel('产品出现次数检查至少次', { exact: true }).fill('3');
  await page.getByLabel('默认产品检查上传参考图').setInputFiles('e2e/fixtures/component-reference.png');
  await configure('产品出现次数检查');
  await page.getByLabel('产品出现次数检查产品参考图', { exact: true }).click();
  await page.getByTitle('component-reference.png', { exact: true }).last().click();
  await page.getByRole('switch', { name: '启用产品出现次数检查', exact: true }).check();
  await configure('产品出现时间检查');
  await page.getByLabel('产品出现时间检查至少秒', { exact: true }).fill('2.5');
  await page.getByLabel('产品出现时间检查产品参考图', { exact: true }).click();
  await page.getByTitle('component-reference.png', { exact: true }).last().click();
  await page.getByRole('switch', { name: '启用产品出现时间检查', exact: true }).check();
  async function save() {
    const saved = page.waitForResponse(r => r.url().endsWith('/standards/confirm') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '保存项目审核规则', exact: true }).click();
    expect((await saved).status()).toBe(200);
  }
  await save(); await page.reload();
  await configure('产品出现次数检查');
  await expect(page.getByLabel('产品出现次数检查至少次', { exact: true })).toHaveValue('3');
  await configure('产品出现时间检查');
  await expect(page.getByLabel('产品出现时间检查至少秒', { exact: true })).toHaveValue('2.50');
  await expect(page.getByRole('switch', { name: '启用产品出现次数检查', exact: true })).toBeChecked();
  const frozen = (await (await request.get(`/api/v1/projects/${project.id}/standards`)).json()).data.video.rules;
  const countRule = frozen.find((r: any) => r.presetKey === 'product_count');
  const durationRule = frozen.find((r: any) => r.presetKey === 'product_duration');
  expect(countRule.componentPlan.nodes[0].kind).toBe('exposure_count');
  expect(countRule.componentPlan.nodes[0].parameters.minOccurrences).toBe(3);
  expect(durationRule.componentPlan.nodes[0].parameters.minTotalSeconds).toBe(2.5);
  expect(durationRule.componentPlan.nodes[0].parameters.minContinuousSeconds).toBeNull();
  await page.getByRole('switch', { name: '启用产品出现次数检查', exact: true }).uncheck();
  await save(); await page.reload();
  await expect(page.getByRole('switch', { name: '启用产品出现次数检查', exact: true })).not.toBeChecked();
  await configure('产品出现次数检查');
  await expect(page.getByLabel('产品出现次数检查至少次', { exact: true })).toHaveValue('3');
  await configure('产品出现时间检查');
  await page.getByLabel('产品出现时间检查至少秒', { exact: true }).fill('');
  await expect(page.getByRole('switch', { name: '启用产品出现时间检查', exact: true })).not.toBeChecked();
  await expect(page.getByRole('switch', { name: '启用产品出现时间检查', exact: true })).toBeDisabled();
  await page.screenshot({ path: 'test-results/video-product-presets.png', fullPage: true });
});
