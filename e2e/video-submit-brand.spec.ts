import { test, expect } from '@playwright/test';

test('视频无未通过项时可提交品牌，待确定项保留（固定响应不计费）', async ({ page, request }) => {
  const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
  const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: `视频提交品牌-${Date.now()}`, brandName: '品牌A', platforms: ['douyin'] } })).json()).data;
  const rule = { text: '产品实际出现', criterion: '产品可辨认即可', importance: 'must', category: '产品' };
  const task = (await (await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: '视频提交达人', platform: 'douyin', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '固定测试' } })).json()).data.id;
  const run = { id: 'pending-video', taskId: task, kind: 'video', fileId: 'fixed-video', status: 'succeeded', configRevision: 1, createdAt: '2026-10-24T10:00:00Z', updatedAt: '2026-10-24T10:00:00Z', snapshot: { rules: [{ id: 'r1', ruleNumber: 1, ...rule }] }, result: { media: { durationMs: 10000 }, report: { summary: '需人工核对产品细节', findings: [{ id: 'i1', rule_id: 'r1', verdict: 'needs_confirmation', observation_type: 'uncertain', description: '需品牌核对，未发现明确不符合', evidence_ids: [] }] } } };
  let submitted = false, calls = 0, failNext = true, configured = false;
  await page.route('**/api/v1/evidence-runs**', r => {
    expect(r.request().method()).toBe('GET');
    return r.fulfill({ json: { data: [{ ...run, result: { ...run.result, ruleCoverage: { unconfiguredRules: configured ? [] : [{ ruleId: 'missing', ruleNumber: 2, text: '不出现指定竞品', importance: 'must', reason: '待品牌确认指定竞品名单' }] } } }] } });
  });
  await page.route(`**/api/v1/tasks/${task}/review-discussion?**`, r => r.fulfill({ json: { data: { current: true, writable: true, submissionReady: configured, disputes: [], response: submitted ? { action: 'submitted', revision: 1 } : null } } }));
  await page.route(`**/api/v1/tasks/${task}/review-responses`, r => {
    expect(r.request().postDataJSON()).toMatchObject({ scope: 'video', reviewId: run.id, action: 'submitted', revision: 0 });
    if (failNext) { failNext = false; return r.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '提交失败，请重试' } } }); }
    calls++; submitted = true; return r.fulfill({ json: { data: { action: 'submitted' } } });
  });
  await page.route('**/api/v1/files/fixed-video/content', r => r.fulfill({ contentType: 'video/mp4', body: Buffer.from('fixed-placeholder') }));
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'creator'));
  await page.goto(`/creator/projects/${project.id}/tasks/${task}/video`);
  const aside = page.locator('.desk-review-aside');
  await expect(aside.getByRole('button', { name: '提交品牌', exact: true })).toBeDisabled();
  await expect(aside).toContainText('待品牌确认指定竞品名单');
  expect(calls).toBe(0);
  configured = true;
  await page.reload();
  await expect(aside.getByRole('button', { name: '收到，我去修改', exact: true })).toHaveCount(0);
  await aside.getByRole('button', { name: '提交品牌', exact: true }).click();
  await expect(aside.getByText('提交失败，请重试', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/tasks/${task}/video$`));
  await aside.getByRole('button', { name: '提交品牌', exact: true }).click();
  await expect(page).toHaveURL(/\/creator$/);
  await expect(page.getByRole('heading', { name: '我的合作', exact: true })).toBeVisible();
  await page.reload();
  await page.goto(`/creator/projects/${project.id}/tasks/${task}/video`);
  await expect(aside.getByRole('button', { name: '已提交品牌', exact: true })).toBeDisabled();
  expect(calls).toBe(1);
});
