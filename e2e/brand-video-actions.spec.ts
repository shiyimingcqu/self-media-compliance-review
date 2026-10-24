import { test, expect } from '@playwright/test';

test('品牌视频页右下角使用同款审核与退回按钮（固定接口响应）', async ({ page, request }) => {
  const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
  const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: `品牌视频按钮-${Date.now()}`, brandName: '品牌A', platforms: ['douyin'] } })).json()).data;
  const rule = { text: '产品实际出现', criterion: '产品可辨认出现即可', importance: 'must', category: '产品' };
  const task = (await (await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: '视频复核达人', platform: 'douyin', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '固定浏览器测试' } })).json()).data.id;
  const realView = (await (await request.get(`/api/v1/tasks/${task}/workflow`)).json()).data;
  const run = { id: 'video-fixed-run', kind: 'video', taskId: task, fileId: 'fixed-file', configRevision: 1, status: 'succeeded', createdAt: '2026-10-24T10:00:00Z', updatedAt: '2026-10-24T10:00:00Z', snapshot: { rules: [{ id: 'r1', ruleNumber: 1, ...rule }] }, result: { media: { durationMs: 10000 }, report: { summary: '固定测试', findings: [{ id: 'i1', rule_id: 'r1', verdict: 'compliant', observation_type: 'observed', description: '固定测试产品出现', evidence_ids: [] }] } } };
  let decision: Record<string, string> | null = null, calls = 0;
  await page.route(`**/api/v1/tasks/${task}/workflow`, r => r.fulfill({ json: { data: { ...realView, videoAllowed: true, videoReviewReady: true, videoSubmitted: true, latestVideoRunId: run.id, videoComponentAcceptance: { passed: true, blockingRules: [], message: '固定通过' }, finalDecision: decision } } }));
  await page.route('**/api/v1/evidence-runs**', r => r.fulfill({ json: { data: [run] } }));
  await page.route('**/api/v1/files/fixed-file/content', r => r.fulfill({ contentType: 'video/mp4', body: Buffer.from('fixed-video-placeholder') }));
  await page.route(`**/api/v1/tasks/${task}/workflow/decisions`, r => {
    const body = r.request().postDataJSON();
    expect(body).toMatchObject({ scope: 'video', targetId: run.id, decision: 'returned', note: '请补充结尾的产品展示。', reviewedMaterial: true });
    calls++; decision = { decision: 'returned', note: body.note };
    return r.fulfill({ json: { data: { id: 'fixed-decision', stage: 'video' } } });
  });
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'brand'));
  await page.goto(`/projects/${project.id}/tasks/${task}/video`);
  const actions = page.locator('.desk-review-actions');
  await expect(actions.getByRole('button', { name: '审核通过', exact: true })).toBeVisible();
  await actions.getByRole('button', { name: '让达人继续修改', exact: true }).click();
  await actions.getByLabel('达人修改要求', { exact: true }).fill('请补充结尾的产品展示。');
  await actions.getByRole('checkbox', { name: '已核对本次材料及审核结果', exact: true }).check();
  await actions.getByRole('button', { name: '确认退回修改', exact: true }).click();
  await expect(actions.getByText('已让达人继续修改', { exact: true })).toBeVisible();
  expect(calls).toBe(1);
});
