import { test, expect } from '@playwright/test';

test('视频处理意见在右栏显示金色按钮，异议后可复用原片重新识别（固定响应不计费）', async ({ page, request }) => {
  const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
  const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: `右栏视频意见-${Date.now()}`, brandName: '品牌A', platforms: ['douyin'] } })).json()).data;
  const rule = { text: '产品实际出现', criterion: '产品可辨认出现即可', importance: 'must', category: '产品' };
  const published = await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: '测试达人', platform: 'douyin', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '固定浏览器测试' } });
  expect(published.ok(), await published.text()).toBeTruthy();
  const task = (await published.json()).data.id;
  const base = { id: 'original-review', kind: 'video', taskId: task, fileId: 'original-file', configRevision: 1, status: 'succeeded', createdAt: '2026-10-23T10:00:00Z', updatedAt: '2026-10-23T10:00:00Z', snapshot: { compressionPreset: 'compact', rules: [{ id: 'r1', ruleNumber: 1, ...rule }] }, result: { media: { durationMs: 162000 }, preparation: { original: { width: 1080, height: 1920 } }, report: { summary: '本条存在待修改内容', findings: [{ id: 'issue1', rule_id: 'r1', verdict: 'non_compliant', observation_type: 'absence', description: 'AI 未发现指定产品，请品牌核对完整原片。', evidence_ids: [] }] } } };
  let calls = 0, response: string | null = null, newRun: Record<string, unknown> | undefined;
  const keys: string[] = [];
  await page.route('**/api/v1/settings/models', route => route.fulfill({ json: { data: { hasKey: true, revision: 2 } } }));
  await page.route(`**/api/v1/tasks/${task}/review-discussion?**`, route => route.fulfill({ json: { data: { response: response ? { action: response, revision: 1 } : null, current: !newRun, writable: !newRun, disputes: [] } } }));
  await page.route(`**/api/v1/tasks/${task}/review-responses`, route => {
    expect(route.request().postDataJSON()).toMatchObject({ scope: 'video', reviewId: base.id, action: 'modifying' });
    response = 'modifying'; return route.fulfill({ json: { data: { action: 'modifying' } } });
  });
  await page.route('**/api/v1/evidence-runs**', route => {
    if (route.request().method() === 'POST') {
      calls++;
      expect(route.request().postDataJSON()).toMatchObject({ taskId: task, fileId: base.fileId, configRevision: 2, allowRealCall: true, compressionPreset: 'compact' });
      keys.push(route.request().headers()['idempotency-key']);
      newRun = { ...base, id: 'new-review', status: 'queued', result: {} };
      return route.fulfill({ json: { data: newRun } });
    }
    return route.fulfill({ json: { data: newRun ? [newRun, base] : [base] } });
  });
  await page.route('**/api/v1/files/original-file/content', route => route.fulfill({ contentType: 'video/mp4', body: Buffer.from('fixed-video-placeholder') }));
  const uploads: string[] = [];
  page.on('request', req => { if (req.method() === 'POST' && /\/files(?:\?|$)|\/video-preparations$/.test(req.url())) uploads.push(req.url()); });
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'creator'));
  await page.goto(`/creator/projects/${project.id}/tasks/${task}/video`);
  const sidebar = page.locator('.desk-review-aside');
  await expect(sidebar.getByText('审核意见', { exact: true })).toBeVisible();
  await expect(sidebar.getByRole('button', { name: '收到，我去修改', exact: true })).toHaveClass(/ant-btn-primary/);
  await expect(sidebar.getByRole('button', { name: '我对 AI 有异议', exact: true })).toHaveClass(/ant-btn-primary/);
  await expect(page.locator('.desk-run-result .creator-review-response')).toHaveCount(0);
  await expect(sidebar.getByRole('button', { name: '重新识别', exact: true })).toHaveCount(0);
  await sidebar.getByRole('button', { name: '收到，我去修改', exact: true }).click();
  await expect(sidebar.getByRole('button', { name: '已收到，修改中', exact: true })).toBeVisible();
  await sidebar.getByRole('button', { name: '我对 AI 有异议', exact: true }).click();
  await expect(page.getByRole('button', { name: '对这条提出异议', exact: true })).toBeVisible();
  const recognize = sidebar.getByRole('button', { name: '重新识别', exact: true });
  await expect(recognize).toBeVisible();
  await expect(recognize).toHaveClass(/ant-btn-primary/);
  await page.screenshot({ path: 'test-results/video-response-sidebar.png' });
  await recognize.click();
  await page.getByRole('button', { name: /^取\s*消$/ }).click();
  expect(calls).toBe(0);
  await recognize.click();
  await page.getByRole('button', { name: '确认重新识别', exact: true }).click();
  await expect(page).toHaveURL(/run=new-review/);
  await expect(sidebar.getByRole('button', { name: '重新识别', exact: true })).toHaveCount(0);
  expect(calls).toBe(1);
  expect(keys[0]).toBeTruthy();
  expect(uploads).toEqual([]);
  expect(base.result.report.findings[0].verdict).toBe('non_compliant');
});
