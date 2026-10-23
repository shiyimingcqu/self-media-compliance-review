import { test, expect } from '@playwright/test';

test('规则更新后的图片可预览，并按当前规则重审原脚本，不重复上传或建版本', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'creator'));
  let calls = 0, uploads = 0, versions = 0, reviewed = false;
  const image = { id: 'im1', locations: ['脚本!A1'], width: 100, height: 60 };
  const acceptance = { passed: false, status: 'pending', message: '需要重新验收', checks: [] };
  const currentScript = { scriptVersionId: 's6', versionLabel: 's6', extractionStatus: 'ready', inputType: 'file', fileId: 'original', text: null, sourceMap: [{ text: '产品介绍', location: '脚本!A1' }], parseMeta: { uncoveredMedia: ['media:image1'], warnings: [], partial: true } };
  const review = { id: 'new-review', scriptVersionId: 's6', status: 'succeeded', snapshot: { multimodal: { images: [image], uncovered: [], incomplete: false } }, result: { report: { summary: '已按当前规则核对文字和图片', findings: [{ rule_id: 'r1', rule_number: 1, verdict: 'compliant', quote: '产品介绍', description: '已核对', suggestion: '' }], image_checks: [{ image_id: image.id, description: '已检查参考图片', rule_ids: ['r1'] }] } } };
  await page.route('**/api/v1/creator/tasks', r => r.fulfill({ json: { data: [{ id: 'task', project_id: 'project', project_name: '麦咖啡项目', creator_name: '达人', brand_name: '品牌', briefTitle: '品牌Brief' }] } }));
  await page.route('**/api/v1/settings/models', r => r.fulfill({ json: { data: { hasKey: true, revision: 1 } } }));
  await page.route('**/api/v1/tasks/task/workflow', r => r.fulfill({ json: { data: { taskId: 'task', writable: true, rulesReady: true, contextKey: 'current-context', currentScript, scriptReview: reviewed ? review : null, scriptReviews: [{ ...review, id: 'old-review' }], scriptAcceptance: acceptance } } }));
  await page.route('**/api/v1/tasks/task/scripts/s6/layout', r => r.fulfill({ json: { data: { supported: true, sheets: [{ name: '脚本', rowCount: 1, colCount: 1, columns: [300], rows: [140], merges: [], cells: [{ address: 'A1', row: 1, col: 1, text: '产品介绍', style: {} }], images: [], hiddenRows: [], hiddenColumns: [] }], warnings: [], images: [image] } } }));
  await page.route('**/layout/images/im1', r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100" height="60" fill="red"/></svg>' }));
  await page.route('**/api/v1/workflow/script-reviews/new-review/images/im1', r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"/>' }));
  await page.route('**/api/v1/files', r => { uploads++; return r.abort(); });
  await page.route('**/api/v1/tasks/task/scripts', r => { versions++; return r.abort(); });
  await page.route('**/api/v1/tasks/task/workflow/script-reviews', async r => {
    calls++;
    expect(r.request().postDataJSON()).toEqual({ scriptVersionId: 's6', contextKey: 'current-context', configRevision: 1, allowRealCall: true });
    expect(r.request().headers()['idempotency-key']).toBeTruthy();
    reviewed = true; await r.fulfill({ json: { data: review } });
  });
  await page.goto('/creator/projects/project/tasks/task');
  await expect(page.getByText('旧审核已失效，图片需按当前规则重新验收', { exact: true })).toBeVisible();
  await expect(page.getByText(/已从原脚本提取 1 张图片/)).toBeVisible();
  expect(calls).toBe(0);
  await page.getByRole('tab', { name: '图片 · 1', exact: true }).click();
  await expect(page.getByAltText('脚本原图 脚本!A1')).toHaveAttribute('src', '/api/v1/tasks/task/scripts/s6/layout/images/im1');
  await page.getByRole('button', { name: '按当前规则重新验收', exact: true }).click();
  await expect(page.getByText('旧审核已失效，图片需按当前规则重新验收', { exact: true })).toHaveCount(0);
  await expect(page.getByAltText('脚本原图 脚本!A1')).toHaveAttribute('src', '/api/v1/workflow/script-reviews/new-review/images/im1');
  expect(calls).toBe(1); expect(uploads).toBe(0); expect(versions).toBe(0);
});
