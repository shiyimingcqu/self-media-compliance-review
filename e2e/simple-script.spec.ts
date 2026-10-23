import { test, expect } from '@playwright/test';
const base = 'http://127.0.0.1:8001/api/v1';
const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });

test('品牌端：上传Brief生成逐条规则，保存后等待达人提交（模拟AI）', async ({ page, request }) => {
  test.setTimeout(60000);
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const p = (await (await request.post(`${base}/projects`, { headers: headers(), data: { name: '简洁审核验收', brandName: '品牌A', platforms: ['douyin'] } })).json()).data;
  let generated = 0, automatic = 0, review: Record<string, unknown> | null = null;
  const rulesText = '检查脚本品牌名是否准确，并指出“保证有效”等没有依据的效果承诺，引用原文给出修改建议。';
  await page.route('**/api/v1/settings/models', route => route.fulfill({ json: { data: { endpoint: 'https://models.example.com/v1', model: 'mock-model', hasKey: true, revision: 1, maxInlineBytes: 25000000 } } }));
  await page.route('**/api/v1/settings/models/decomposition', route => route.fulfill({ json: { data: { endpoint: 'https://models.example.com/v1', model: 'mock-model', hasKey: true, revision: 1, maxInlineBytes: 25000000 } } }));
  // Simulate a completed facts pipeline; the isolated backend has no provider credentials.
  await page.route(`**/api/v1/projects/${p.id}/brief`, async route => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch(); const body = await response.json();
    if (body.data.brief?.processing) body.data.brief.processing.status = 'succeeded';
    await route.fulfill({ response, json: body });
  });
  await page.route(`**/api/v1/projects/${p.id}/standard-recommendations`, async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { data: [] } });
    const body = route.request().postDataJSON(); expect(body.outputFormat).toBe('structured'); expect(body.allowRealCall).toBe(true); generated++;
    await route.fulfill({ json: { data: { id: 'mock-rec', scope: 'script', status: 'succeeded', snapshot: { sourceVersionId: body.sourceVersionId }, result: { report: { rules: [{ text: '核对品牌与表达', criterion: rulesText, category: '脚本检查', importance: 'must', origin: 'ai', sourceBlockId: '', sourceQuote: '' }], warnings: [] } } } } });
  });
  await page.route('**/api/v1/tasks/*/workflow', async route => {
    const response = await route.fetch(); const body = await response.json();
    if (review && body.data.currentScript?.scriptVersionId === review.scriptVersionId) { body.data.scriptReview = review; body.data.scriptReviews = [review]; }
    await route.fulfill({ response, json: body });
  });
  await page.route('**/api/v1/tasks/*/scripts/submit-and-review', async route => {
    const body = route.request().postDataJSON(); expect(body.allowRealCall).toBe(true); automatic++;
    // The isolated backend has no saved Key, so this saves the script without a supplier call.
    const response = await route.fetch(); const saved = (await response.json()).data.script;
    const taskId = saved.taskId;
    const view = (await (await request.get(`${base}/tasks/${taskId}/workflow`)).json()).data;
    const ruleId = view.standardSets.script.rules[0].id;
    review = { id: 'mock-auto-review', scriptVersionId: saved.scriptVersionId, ruleVersionId: null, contextKey: view.contextKey, status: 'succeeded', createdAt: saved.createdAt, result: { report: { summary: '有两处需要修改', findings: [
      { rule_id: ruleId, verdict: 'needs_confirmation', quote: '品牌A', description: '品牌名称需要确认', suggestion: '核对品牌全称' },
      { rule_id: ruleId, verdict: 'non_compliant', quote: '保证有效', description: '效果承诺缺少依据', suggestion: '删除保证有效' },
    ] } } };
    await route.fulfill({ status: 201, json: { data: { script: saved, autoReview: review, autoReviewError: null } } });
  });
  await page.goto(`/projects/${p.id}`);
  await page.getByLabel('上传项目Brief').setInputFiles({ name: 'brief.txt', mimeType: 'text/plain', buffer: Buffer.from('品牌名为品牌A，禁止保证效果的口播承诺。') });
  await page.getByRole('button', { name: '上传并解析', exact: true }).click();
  await expect(page.getByText('解析完成 · V1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '脚本规则', exact: true }).click();
  const drawer = page.locator('.project-rules-form');
  await expect(drawer.getByRole('textbox', { name: '达人名称' })).toHaveCount(0);
  await expect(drawer.getByRole('textbox', { name: '第1条判定说明' })).not.toBeEmpty();
  await expect(drawer.getByRole('checkbox')).toHaveCount(0);
  await drawer.getByRole('button', { name: 'AI 生成规则', exact: true }).click();
  await expect(drawer.getByRole('textbox', { name: '第1条判定说明' })).toHaveValue(rulesText);
  expect(generated).toBe(1);
  await drawer.getByRole('textbox', { name: '第1条判定说明' }).fill(rulesText + '请优先反馈需要修改的地方。');
  await drawer.getByRole('button', { name: '添加一条规则' }).click();
  await drawer.getByRole('textbox', { name: '第2条要求' }).fill('图片不得出现竞品');
  await drawer.getByRole('textbox', { name: '第2条判定说明' }).fill('对照参考图检查品牌标识');
  await page.screenshot({ path: 'test-results/numbered-script-rules.png', fullPage: true });
  const publish = page.waitForResponse(r => r.url().endsWith('/standards/confirm'));
  await drawer.getByRole('button', { name: '保存项目审核规则' }).click();
  const created = await publish; expect(created.status()).toBe(200);
  await expect(page.getByText('已确认 V1', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '返回项目', exact: true }).click();
  await page.getByRole('button', { name: '添加达人', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox', { name: '达人名称' }).fill('样例达人');
  const added = page.waitForResponse(r => r.url().endsWith('/creators') && r.request().method() === 'POST');
  await page.getByRole('dialog').getByRole('button', { name: '添加达人', exact: true }).click();
  const task = (await (await added).json()).data;
  const current = (await (await request.get(`${base}/tasks/${task.id}/workflow`)).json()).data;
  expect(current.standardSets.script.rules[0].criterion).toBe(rulesText + '请优先反馈需要修改的地方。');
  expect(current.standardSets.script.rules.map((r: { ruleNumber: number }) => r.ruleNumber)).toEqual([1, 2]);
  expect(current.standardSets.script.rules[1].criterion).toBe('对照参考图检查品牌标识');
  await page.screenshot({ path: 'test-results/numbered-rules-project.png' });
  await page.goto(`/projects/${p.id}/tasks/${task.id}/script`);
  await expect(page.getByText('尚未进入品牌待审', { exact: true })).toBeVisible();
  await expect(page.getByLabel('达人脚本正文')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '编辑项目规则', exact: true })).toBeVisible();
  expect(automatic).toBe(0);
  expect(errors).toEqual([]);
});
