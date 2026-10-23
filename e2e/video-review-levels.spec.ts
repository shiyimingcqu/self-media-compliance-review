import { test, expect } from '@playwright/test';

test('档位只决定叠加到哪一层，规则始终是一个连续列表，切档保留已生成规则', async ({ page, request }) => {
  test.setTimeout(90_000);
  const project = (await (await request.post('/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '审查档位和单行规则', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  await page.goto(`/projects/${project.id}/rules?type=video`);
  await expect(page.getByRole('radio', { name: '基础版', exact: true })).toBeChecked();
  // 不再有分层区块标题，只有一个连续列表
  await expect(page.locator('.video-layer-heading')).toHaveCount(0);
  await expect(page.locator('.video-layer-row')).toHaveCount(3);
  // 判据默认隐藏，点击详情后可以查看和编辑。
  await expect(page.getByLabel('视频第1条判定说明', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '查看视频第1条详情', exact: true }).click();
  await expect(page.getByLabel('视频第1条判定说明', { exact: true })).toBeVisible();
  await page.locator('.ant-drawer').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.getByRole('article', { name: '不使用禁用表达', exact: true })).toHaveCount(0);

  await page.locator('.video-review-level').getByText('进阶版', { exact: true }).click();
  await page.getByRole('button', { name: '添加文字规则', exact: true }).click();
  await page.getByLabel('视频第4条要求', { exact: true }).fill('商业信息自然传达');
  await page.getByLabel('视频第4条判定说明', { exact: true }).fill('核心卖点自然传达即可，合理剪辑允许');
  await page.locator('.ant-drawer').getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('switch', { name: '启用视频第4条', exact: true }).check();

  await page.locator('.video-review-level').getByText('严格版', { exact: true }).click();
  await expect(page.getByRole('article', { name: '不使用禁用表达', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '配置禁用表达', exact: true }).click();
  const terms = page.getByLabel('禁用表达词句');
  await terms.fill('第一条禁词'); await terms.press('End'); await terms.press('Enter'); await terms.pressSequentially('第二条禁词');
  await expect(terms).toHaveValue('第一条禁词\n第二条禁词');
  await page.getByRole('switch', { name: '启用不使用禁用表达', exact: true }).check();

  async function save() {
    const saved = page.waitForResponse(r => r.url().endsWith('/standards/confirm') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '保存项目审核规则', exact: true }).click();
    expect((await saved).status()).toBe(200);
  }
  await save(); await page.reload();
  await expect(page.getByRole('radio', { name: '严格版', exact: true })).toBeChecked();
  await expect(page.getByRole('switch', { name: '启用不使用禁用表达', exact: true })).toBeChecked();
  await page.locator('.video-layers-editor').screenshot({ path: 'test-results/video-review-levels.png' });

  // 切回基础版：更高档位的规则保留在数据里但不在列表出现，审核也不使用
  await page.locator('.video-review-level').getByText('基础版', { exact: true }).click();
  await expect(page.locator('.video-layer-row')).toHaveCount(3);
  await expect(page.getByRole('article', { name: '不使用禁用表达', exact: true })).toHaveCount(0);
  await expect(page.getByText(/另有 \d+ 条更高档位的规则已保留/)).toBeVisible();
  await save(); await page.reload();
  await expect(page.getByRole('radio', { name: '基础版', exact: true })).toBeChecked();
  const standards = (await (await request.get(`/api/v1/projects/${project.id}/standards`)).json()).data;
  expect(standards.video.videoReviewLevel).toBe('basic');
  expect(standards.video.rules.find((r: any) => r.presetKey === 'forbidden_expression').enabled).toBe(true);
  expect(standards.video.rules.filter((r: any) => r.layer === 'baseline')).toHaveLength(3);
  expect(standards.video.rules.filter((r: any) => r.layer === 'brief')).toHaveLength(1);

  // 切回严格版：之前生成的规则和禁用表达配置都还在
  await page.locator('.video-review-level').getByText('严格版', { exact: true }).click();
  await expect(page.getByRole('switch', { name: '启用不使用禁用表达', exact: true })).toBeChecked();
  await expect(page.getByLabel('视频第4条要求', { exact: true })).toHaveValue('商业信息自然传达');
});