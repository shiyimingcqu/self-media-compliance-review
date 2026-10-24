import { test, expect } from '@playwright/test';

for (const outcome of ['approved', 'returned'] as const) {
  test(`品牌三栏脚本复核：${outcome} 后达人收到结果（固定响应）`, async ({ page, request }) => {
    test.skip(process.env.E2E_DISCUSSION_FIXTURE !== '1', '仅使用隔离固定响应');
    const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
    const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: `品牌三栏-${outcome}-${Date.now()}`, brandName: '品牌A', platforms: ['xiaohongshu'] } })).json()).data;
    const rule = { text: '展示品牌A产品', criterion: '产品可辨认即可', importance: 'must', category: '产品' };
    const task = (await (await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: `品牌复核达人-${outcome}`, platform: 'xiaohongshu', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '固定测试' } })).json()).data.id;
    const cfg = (await (await request.get('/api/v1/settings/models')).json()).data;
    expect((await request.put('/api/v1/settings/models', { data: { endpoint: 'https://models.example.com/v1', model: 'e2e-discussion-fixture', apiKey: 'sk-test-fixture', revision: cfg.revision } })).ok()).toBeTruthy();
    await request.post(`/api/v1/tasks/${task}/scripts`, { headers: headers(), data: { inputType: 'text', text: 'E2E_PASS 展示品牌A产品，自然介绍使用感受。' } });
    const view = (await (await request.get(`/api/v1/tasks/${task}/workflow`)).json()).data;
    const review = (await (await request.post(`/api/v1/tasks/${task}/workflow/script-reviews`, { headers: headers(), data: { scriptVersionId: view.currentScript.scriptVersionId, contextKey: view.contextKey, configRevision: cfg.revision + 1, allowRealCall: true } })).json()).data;
    expect((await request.post(`/api/v1/tasks/${task}/review-responses`, { headers: headers(), data: { scope: 'script', reviewId: review.id, action: 'submitted' } })).ok()).toBeTruthy();
    await page.addInitScript(() => { if (!localStorage.getItem('review-demo-role')) localStorage.setItem('review-demo-role', 'brand'); });
    await page.goto(`/projects/${project.id}/tasks/${task}/script`);
    await expect(page.getByText('脚本解析预览', { exact: true })).toBeVisible();
    await expect(page.getByText('审核结果', { exact: true })).toBeVisible();
    const preview = await page.locator('.creator-reader-card').first().boundingBox();
    const results = await page.locator('.creator-reader-card').last().boundingBox();
    const aside = await page.locator('.script-review-aside').boundingBox();
    expect(preview!.x + preview!.width).toBeLessThanOrEqual(results!.x);
    expect(results!.x + results!.width).toBeLessThanOrEqual(aside!.x);
    await expect(page.getByRole('button', { name: '提交品牌', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '审核通过', exact: true })).toBeDisabled();
    if (outcome === 'returned') {
      await page.getByRole('button', { name: '让达人继续修改', exact: true }).click();
      await expect(page.getByRole('button', { name: '确认退回修改', exact: true })).toBeDisabled();
      await page.getByLabel('达人修改要求', { exact: true }).fill('请将产品介绍放到开场，并补充一个产品特写镜头。');
    }
    await page.getByRole('checkbox', { name: '已核对本次材料及审核结果', exact: true }).check();
    await page.getByRole('button', { name: outcome === 'approved' ? '审核通过' : '确认退回修改', exact: true }).click();
    if (outcome === 'approved') await expect(page).toHaveURL(/\/reviews$/);
    else await expect(page.getByText('已让达人继续修改', { exact: true })).toBeVisible();
    await page.screenshot({ path: `test-results/brand-script-${outcome}.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.setViewportSize({ width: 1440, height: 1080 });
    expect((await (await request.get('/api/v1/workflow/review-queue')).json()).data.filter((r: { id: string }) => r.id === task)).toHaveLength(0);
    await page.evaluate(() => localStorage.setItem('review-demo-role', 'creator'));
    await page.goto(`/creator/projects/${project.id}/tasks/${task}`);
    await page.reload();
    if (outcome === 'approved') await expect(page.getByText('品牌已通过脚本', { exact: true })).toBeVisible();
    else await expect(page.getByText('请将产品介绍放到开场，并补充一个产品特写镜头。', { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  });
}
