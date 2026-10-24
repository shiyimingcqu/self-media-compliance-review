import { test, expect } from '@playwright/test';

test('两阶段视频进度、精判诊断和历史报告兼容（仅固定响应）', async ({ page }) => {
  let finished = false;
  const base = { kind: 'video', configRevision: 1, createdAt: '2026-10-23T02:00:00Z', updatedAt: '2026-10-23T02:00:12Z', affectsWorkflow: false };
  const legacy = { ...base, id: 'legacy-run', status: 'succeeded', snapshot: { rules: [{ id: 'legacy-rule', text: '旧版产品检查', importance: 'must' }] }, result: { report: { summary: '旧版审核摘要', findings: [{ id: 'legacy-issue', rule_id: 'legacy-rule', verdict: 'compliant', description: '旧版审核结果保留', evidence_ids: [] }] } } };
  const current = () => ({ ...base, id: 'localized-run', status: finished ? 'succeeded' : 'processing', snapshot: { pipelineVersion: 'localized_refinement_v1', rules: [{ id: 'r1', text: '产品名称须准确', importance: 'must', ruleNumber: 8 }] }, result: {
    durationMs: finished ? 12340 : undefined,
    pipeline: { stage: finished ? 'completed' : 'refining', completedBatches: finished ? 2 : 1, totalBatches: 2,
      decisions: finished ? [{ ruleId: 'r1', initialVerdict: 'non_compliant', finalVerdict: 'compliant', refined: true }] : [],
      batches: [{ id: 'batch-1', startMs: 38000, endMs: 44000, status: 'succeeded' }, { id: 'batch-2', startMs: 60000, endMs: 68000, status: finished ? 'succeeded' : 'processing' }] },
    report: finished ? { summary: '精判后排除产品名称问题', findings: [{ id: 'issue-r1', rule_id: 'r1', rule_number: 8, verdict: 'compliant', description: '原片40–42秒产品名称清晰正确', evidence_ids: ['pass-clip'] }] } : undefined,
    artifacts: finished ? [{ id: 'pass-clip', kind: 'clip', startMs: 38000, endMs: 44000, hasAudio: true, issueId: 'issue-r1' }] : [],
  } });
  await page.route('**/api/v1/settings/models', route => route.fulfill({ json: { data: { endpoint: 'https://models.example.com/v1', model: 'qwen3.8-omni-flash', hasKey: false, revision: 1, maxModelRequests: null } } }));
  await page.route('**/api/v1/evidence-runs/localized-run/artifacts/pass-clip', route => route.fulfill({ contentType: 'video/mp4', body: Buffer.from('local-video-fixture') }));
  await page.route('**/api/v1/evidence-runs*', route => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill({ json: { data: [current(), legacy] } });
  });
  await page.goto('/settings/models?run=localized-run');
  const progress = page.getByLabel('两阶段审核进度');
  await expect(progress).toContainText('1/2');
  await expect(page.getByText('正在整片定位与按片段精判')).toBeVisible();
  await expect(page.getByText('原片40–42秒产品名称清晰正确')).toHaveCount(0);
  finished = true;
  await page.getByRole('button', { name: '刷新记录', exact: true }).click();
  await expect(progress).toContainText('2/2');
  await expect(page.getByRole('tab', { name: /已通过/ })).toHaveAttribute('data-state', 'active');
  await expect(page.getByText('原片40–42秒产品名称清晰正确')).toBeVisible();
  await expect(page.getByRole('button', { name: '▷ 播放 38 秒至 44 秒' })).toBeVisible();
  await expect(page.locator('.finding-evidence video')).toHaveCount(0);
  await expect(page.locator('.desk-run-result').getByRole('timer', { name: 'AI 处理用时：13 秒完成', exact: true })).toBeVisible();
  await progress.getByText('查看定位与精判诊断').click();
  await expect(progress).toContainText('不符合 → 通过');
  await page.screenshot({ path: 'test-results/video-localized-refinement.png', fullPage: true });
  await page.goto('/settings/models?run=legacy-run');
  await page.getByRole('tab', { name: /已通过/ }).click();
  await expect(page.getByText('旧版审核结果保留')).toBeVisible();
  await expect(page.getByLabel('两阶段审核进度')).toHaveCount(0);
});
