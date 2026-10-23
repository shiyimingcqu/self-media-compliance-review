import { test, expect } from '@playwright/test';

test('品牌在规则页查看历史未通过记录，区分调用异常并对照旧规则，不覆盖编辑草稿', async ({ page, request }) => {
  const project = (await (await request.post('/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '品牌失败记录复盘', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  const issue = { ruleId: 'old-rule', ruleNumber: 1, ruleText: '产品每个镜头都露出', criterion: '历史判据：每个镜头均须完整展示产品', importance: 'must', verdict: 'non_compliant', description: '中段剧情没有产品镜头', suggestion: '补拍产品镜头', quote: '中段剧情安排', blocking: true };
  const items = [{ id: 'old-script', taskId: 'task', creatorName: '达人甲', scope: 'script', status: 'succeeded', outcome: 'needs_changes', createdAt: '2026-10-21T10:00:00Z', versionLabel: '脚本 V1', rulesCurrent: false, reportVerified: true, summary: '旧规则下未通过', issues: [issue] },
    { id: 'bad-video', taskId: 'task', creatorName: '达人乙', scope: 'video', status: 'failed', outcome: 'call_failed', createdAt: '2026-10-21T12:00:00Z', versionLabel: '视频记录 bad-video', rulesCurrent: false, reportVerified: false, summary: '', errorCode: 'FINAL_REPORT_EVIDENCE_MISMATCH', errorMessage: '证据引用校验失败', issues: [] }];
  await page.route(`**/api/v1/projects/${project.id}/submission-feedback?*`, route => {
    const params = new URL(route.request().url()).searchParams;
    const filtered = items.filter(item => (!params.get('scope') || params.get('scope') === item.scope) && (params.get('outcome') === 'all' || params.get('outcome') === item.outcome));
    return route.fulfill({ json: { data: { items: filtered, total: filtered.length, counts: { needs_changes: 1, needs_confirmation: 0, call_failed: 1, returned: 0 } } } });
  });
  await page.route(`**/api/v1/projects/${project.id}/submission-feedback/script/old-script`, route => route.fulfill({ json: { data: { ...items[0], scriptText: '达人当时提交的脚本：中段剧情安排。', rules: [{ id: 'old-rule', text: issue.ruleText, criterion: issue.criterion }], artifacts: [] } } }));
  await page.goto(`/projects/${project.id}/rules?type=script`);
  await page.getByLabel('第1条要求', { exact: true }).fill('当前未保存的宽松规则');
  await page.getByRole('button', { name: '查看未通过记录', exact: true }).click();
  await expect(page.locator('.submission-feedback-record')).toHaveCount(1);
  await page.getByLabel('未通过记录类型').click();
  await page.getByText('脚本和视频', { exact: true }).last().click();
  await expect(page.locator('.submission-feedback-record')).toHaveCount(2);
  await page.getByLabel('未通过记录原因').click();
  await page.getByTitle('调用异常', { exact: true }).last().click();
  await expect(page.locator('.submission-feedback-record')).toHaveCount(1);
  await expect(page.locator('.submission-feedback-record')).toContainText('FINAL_REPORT_EVIDENCE_MISMATCH');
  await page.getByLabel('未通过记录原因').click();
  await page.getByTitle('全部原因', { exact: true }).last().click();
  await page.locator('.submission-feedback-record').filter({ hasText: '达人甲' }).getByRole('button', { name: '查看当时规则、材料与反馈', exact: true }).click();
  await expect(page.getByRole('dialog', { name: /达人甲.*历史审核/ })).toBeVisible();
  await expect(page.locator('.submission-feedback-criterion')).toContainText('历史判据：每个镜头均须完整展示产品');
  await expect(page.locator('.submission-feedback-material')).toContainText('达人当时提交的脚本');
  await page.screenshot({ path: 'test-results/brand-submission-feedback.png', fullPage: true });
  await page.getByRole('button', { name: '返回记录并调整规则', exact: true }).click();
  await expect(page.getByRole('dialog', { name: /达人甲.*历史审核/ })).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '项目脚本 / 视频未通过记录', exact: true })).toBeHidden();
  await expect(page.getByLabel('第1条要求', { exact: true })).toHaveValue('当前未保存的宽松规则');
});


test('视频未通过记录可按冻结层级筛选', async ({ page, request }) => {
  const project = (await (await request.post('/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: '按层复盘视频', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  await page.route(`**/api/v1/projects/${project.id}/submission-feedback?*`, route => {
    const selected = new URL(route.request().url()).searchParams.get('layer');
    const items = ['baseline', 'brief', 'precise'].filter(layer => !selected || layer === selected).map(layer => ({
      id: layer, taskId: 'task', creatorName: '达人甲', scope: 'video', status: 'succeeded', outcome: 'needs_changes', createdAt: '2026-10-21T10:00:00Z', versionLabel: layer, rulesCurrent: false, reportVerified: true,
      summary: '按冻结规则显示', issues: [{ layer, ruleId: layer, ruleNumber: 1, ruleText: `${layer}历史要求`, criterion: '当时的判据', importance: 'must', verdict: 'non_compliant', description: '具体反馈', blocking: true }],
    }));
    return route.fulfill({ json: { data: { items, total: items.length, counts: { needs_changes: items.length, needs_confirmation: 0, call_failed: 0, returned: 0 } } } });
  });
  await page.goto(`/projects/${project.id}/rules?type=video`);
  await page.getByRole('button', { name: '查看未通过记录', exact: true }).click();
  await expect(page.locator('.submission-feedback-record')).toHaveCount(3);
  await page.getByLabel('按视频规则层级筛选').click();
  await page.getByTitle('第二层 · Brief', { exact: true }).last().click();
  await expect(page.locator('.submission-feedback-record')).toHaveCount(1);
  await expect(page.locator('.submission-feedback-record')).toContainText('brief历史要求');
});
