import { test, expect } from '@playwright/test';

test('格式失败保留诊断，本地恢复候选明显标记且不自动通过', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'creator'));
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  let recovered = false, recoveries = 0;
  const acceptance = { passed: false, status: 'incomplete', checks: [], blockingCount: 0, message: '须人工核对，不能自动通过' };
  const finding = { rule_id: 'r1', rule_number: 1, verdict: 'compliant', quote: '品牌A产品展示', description: '已核对产品名称', suggestion: '' };
  const original = '{"description":"或"22g大粉量萃取"等关键参数"}';
  const review = () => ({ id: 'review1', scriptVersionId: 's1', status: 'failed', errorCode: recovered ? 'NEEDS_MANUAL_REVIEW' : 'OUTPUT_INVALID_JSON', errorMessage: '原始输出已保留，未自动重试', result: {
    modelResponse: original,
    diagnostics: { errorType: 'OUTPUT_INVALID_JSON', validationError: '模型返回不是合法 JSON', offset: 20, context: original },
    ...(recovered ? { recovery: { repaired: true, requiresManualReview: true, repairedOutput: JSON.stringify({ description: '或"22g大粉量萃取"等关键参数' }) }, report: { summary: '本地格式修复候选', findings: [finding], image_checks: [] } } : {}),
  } });
  await page.route('**/api/v1/creator/tasks', r => r.fulfill({ json: { data: [{ id: 'task', project_id: 'project', creator_name: '样例达人', project_name: '可靠性验收', brand_name: '品牌A', briefTitle: '测试Brief', rulesReady: true, scriptAcceptance: acceptance }] } }));
  await page.route('**/api/v1/settings/models', r => r.fulfill({ json: { data: { hasKey: true, revision: 1 } } }));
  await page.route('**/api/v1/tasks/task/workflow', r => r.fulfill({ json: { data: {
    taskId: 'task', writable: true, rulesReady: true, brandReviewAllowed: false, videoAllowed: false,
    currentScript: { scriptVersionId: 's1', versionLabel: 's1', extractionStatus: 'ready', inputType: 'text', text: '品牌A产品展示', sourceMap: [{ text: '品牌A产品展示', paragraphId: 'p1' }], parseMeta: {} },
    scriptReview: review(), scriptReviews: [review()], scriptAcceptance: acceptance, scriptApproval: null,
  } } }));
  await page.route('**/api/v1/workflow/script-reviews/review1/revalidate', r => {
    expect(r.request().method()).toBe('POST');
    expect(r.request().headers()['idempotency-key']).toBeTruthy();
    recoveries++; recovered = true;
    return r.fulfill({ json: { data: review() } });
  });
  await page.goto('/creator/projects/project/tasks/task');
  await page.getByRole('button', { name: '审核详情', exact: true }).click();
  await page.getByText('调用诊断', { exact: true }).click();
  await expect(page.getByText('错误类型：OUTPUT_INVALID_JSON', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '本地恢复报告（不调用模型）', exact: true }).click();
  await expect(page.getByText('格式已修复，须人工核对', { exact: true })).toBeVisible();
  await expect(page.getByText('必须项验收通过', { exact: true })).toHaveCount(0);
  await expect(page.locator('.review-comment')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/script-output-recovery.png', fullPage: true });
  expect(recoveries).toBe(1);
  expect(errors).toEqual([]);
});
