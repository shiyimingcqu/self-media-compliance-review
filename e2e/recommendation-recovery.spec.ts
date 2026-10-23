import { test, expect } from '@playwright/test';

test('失败推荐可以从历史恢复到草稿，不触发收费生成或自动保存', async ({ page, request }) => {
  const base = 'http://127.0.0.1:8001/api/v1';
  const project = (await (await request.post(`${base}/projects`, { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '规则本地恢复测试', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  const candidate = { id: 'saved-result', scope: 'script', status: 'failed', snapshot: { sourceVersionId: 'source' }, result: { modelResponse: '{"rules":[]}' } };
  let recovered = 0, generated = 0, saved = 0;
  await page.route(`**/api/v1/projects/${project.id}/brief`, r => r.fulfill({ json: { data: { projectId: project.id, revision: 1, brief: { title: '品牌Brief.pdf', sourceVersionId: 'source', blocks: [], uncoveredSources: [] } } } }));
  await page.route(`**/api/v1/projects/${project.id}/standard-recommendations`, r => {
    if (r.request().method() === 'POST') generated++;
    return r.fulfill({ json: { data: [candidate] } });
  });
  await page.route(`**/api/v1/projects/${project.id}/standards/confirm`, r => { saved++; return r.abort(); });
  await page.route(`**/api/v1/projects/${project.id}/standard-recommendations/saved-result/revalidate`, r => {
    recovered++;
    return r.fulfill({ json: { data: { ...candidate, status: 'succeeded', result: { report: { rules: [{ text: '恢复的产品名称核对规则', criterion: '产品名称须与Brief一致', category: '品牌', importance: 'must', sourceBlockId: '', sourceQuote: '', origin: 'ai' }], warnings: ['引用已还原，请品牌核对。'] } } } } });
  });
  await page.goto(`/projects/${project.id}/rules?type=script`);
  await page.getByRole('button', { name: '恢复上次生成（不调用AI）', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '第1条要求', exact: true })).toHaveValue('恢复的产品名称核对规则');
  await expect(page.getByText('引用已还原，请品牌核对。', { exact: true })).toBeVisible();
  expect(recovered).toBe(1); expect(generated).toBe(0); expect(saved).toBe(0);
});
