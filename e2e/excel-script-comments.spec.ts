import { test, expect } from '@playwright/test';

test('Excel保留原布局，原文标记与评论双向定位，连线随滚动缩放更新', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'creator'));
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const acceptance = { passed: false, status: 'needs_changes', blockingCount: 2, message: '请修改产品名称与补充卖点', checks: [] };
  const currentScript = { scriptVersionId: 's1', versionLabel: 's1', extractionStatus: 'ready', inputType: 'file', fileId: 'xlsx-file', text: null, parseMeta: { warnings: [], uncoveredMedia: [], coverage: 'full' }, sourceMap: [{ text: '脚本标题', location: '视频脚本!A1' }, { text: '女儿：麦咖啡冰奶铁好喝。', location: '视频脚本!C4' }] };
  const review = { id: 'review1', createdAt: '2026-10-22T10:00:00Z', updatedAt: '2026-10-22T10:00:05Z', scriptVersionId: 's1', status: 'succeeded', snapshot: { rules: [{ id: 'rule5', importance: 'suggested' }], multimodal: { images: [{ id: 'image1', locations: ['视频脚本!D4'], width: 100, height: 60 }], uncovered: [], incomplete: false } }, result: { report: { summary: '请核对产品名称，补充核心卖点。', findings: [
    { rule_id: 'rule1', rule_number: 1, verdict: 'non_compliant', quote: '麦咖啡冰奶铁', description: '产品名不完整。', suggestion: '改为芝士浓奶铁。' },
    { rule_id: 'rule2', rule_number: 2, verdict: 'non_compliant', quote: '', description: '缺少核心卖点。', suggestion: '补充卖点。' },
    { rule_id: 'rule3', rule_number: 3, verdict: 'needs_confirmation', quote: '', image_ids: ['image1'], description: '参考图需要核对。', suggestion: '核对图片。' },
    { rule_id: 'rule4', rule_number: 4, verdict: 'needs_confirmation', quote: '', evidence_status: 'unverified', evidence_warning: '引文待人工核对：模型引文未逐字匹配脚本，本项不计为通过。', unverified_quote: '模型改写的台词', description: '需要人工核实证据。', suggestion: '' },
    { rule_id: 'rule5', rule_number: 5, verdict: 'non_compliant', quote: '', description: '开场可以更自然。', suggestion: '调整开场节奏。' },
    { rule_id: 'rule6', rule_number: 6, verdict: 'compliant', quote: '', description: '没有出现指定竞品。', suggestion: '' },
  ], image_checks: [{ image_id: 'image1', description: '产品参考图', rule_ids: ['rule3'] }] } } };
  const layout = { supported: true, sheets: [{ name: '视频脚本', rowCount: 5, colCount: 4, columns: [100, 120, 360, 180], rows: [40, 40, 40, 220, 320], merges: [{ r1: 1, c1: 1, r2: 1, c2: 4 }], hidden: false, hiddenRows: [], hiddenColumns: [], cells: [{ address: 'A1', row: 1, col: 1, text: '脚本标题', uncached: false, style: { fontSize: 18, fontWeight: 700, backgroundColor: '#fff100', whiteSpace: 'pre-wrap' } }, { address: 'C4', row: 4, col: 3, text: '女儿：麦咖啡冰奶铁好喝。', uncached: false, style: { fontSize: 18, whiteSpace: 'pre-wrap', verticalAlign: 'top' } }], images: [{ id: 'image1', cell: 'D4', x: 584, y: 124, width: 100, height: 60 }] }], warnings: ['浏览布局可能与打印效果不同'], images: review.snapshot.multimodal.images };
  await page.route('**/api/v1/creator/tasks', r => r.fulfill({ json: { data: [{ id: 'task', project_id: 'project', creator_name: '样例达人', project_name: '麦咖啡项目', brand_name: '麦咖啡', briefTitle: '品牌Brief', rulesReady: true, scriptAcceptance: acceptance }] } }));
  await page.route('**/api/v1/tasks/task/workflow', r => r.fulfill({ json: { data: { writable: true, rulesReady: true, currentScript, scriptReview: review, scriptReviews: [review], scriptAcceptance: acceptance } } }));
  await page.route('**/api/v1/tasks/task/scripts/s1/layout', r => r.fulfill({ json: { data: layout } }));
  await page.route('**/api/v1/settings/models', r => r.fulfill({ json: { data: { hasKey: true, revision: 1 } } }));
  await page.route('**/layout/images/image1', r => r.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100" height="60" fill="#e5f6ee"/></svg>' }));
  await page.goto('/creator/projects/project/tasks/task');
  await expect(page.getByRole('tab', { name: 'Excel 原布局', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('radio', { name: '需修改 4 项', exact: true })).toBeChecked();
  await expect(page.locator('.review-comment')).toHaveCount(4);
  await expect(page.locator('.review-comment-status .ai-elapsed')).toHaveText('5 秒完成');
  await page.getByText('建议 1 项', { exact: true }).click();
  await expect(page.locator('.review-comment')).toHaveCount(1);
  await expect(page.locator('.review-comment')).toContainText('开场可以更自然');
  await page.getByText('通过 1 项', { exact: true }).click();
  await expect(page.locator('.review-comment')).toHaveCount(1);
  await expect(page.locator('.review-comment')).toContainText('没有出现指定竞品');
  await page.getByText('需修改 4 项', { exact: true }).click();
  const area = await page.locator('.creator-comment-grid').boundingBox();
  expect(area!.y).toBeLessThan(150);
  expect(area!.height).toBeGreaterThan(960 * .8);
  expect(await page.locator('.sheet-preview-scroll').evaluate(el => el.clientHeight)).toBeGreaterThan(960 * .6);
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(960);
  const title = page.locator('[data-cell-location="视频脚本!A1"]');
  await expect(title).toHaveCSS('width', '760px');
  await expect(title).toHaveCSS('background-color', 'rgb(255, 241, 0)');
  await expect(page.getByAltText('表格内嵌图片 视频脚本!D4')).toBeVisible();
  await page.getByRole('button', { name: '定位评论 1', exact: true }).click();
  await expect(page.locator('.sheet-cell .review-text-mark.active')).toHaveText('麦咖啡冰奶铁');
  await expect(page.locator('.comment-connector')).toHaveCount(1);
  const oldPath = await page.locator('.comment-connector').getAttribute('d');
  await page.getByRole('article', { name: '审核评论 2', exact: true }).getByRole('button', { name: '定位评论 2', exact: true }).click();
  await expect(page.getByRole('article', { name: '审核评论 2', exact: true })).toContainText('无对应原文位置');
  await expect(page.locator('.comment-connector')).toHaveCount(0);
  await page.getByRole('button', { name: '查看评论 1，视频脚本!C4', exact: true }).click();
  await expect(page.locator('.review-comment.active')).toHaveAttribute('aria-label', '审核评论 1');
  await expect(page.locator('.comment-connector')).toHaveCount(1);
  await page.getByLabel('表格预览缩放').click();
  await page.getByText('125%', { exact: true }).click();
  await expect(page.locator('.sheet-canvas')).toHaveCSS('transform', 'matrix(1.25, 0, 0, 1.25, 0, 0)');
  await expect.poll(() => page.locator('.comment-connector').getAttribute('d')).not.toBe(oldPath);
  await expect(page.locator('.creator-reader-card').first().locator('> .ant-card-body')).toHaveJSProperty('scrollTop', 0);
  await page.screenshot({ path: 'test-results/excel-script-comments.png', fullPage: true });
  await page.getByRole('button', { name: '定位评论 3', exact: true }).click();
  await expect(page.locator('.sheet-drawing.active-comment-image')).toHaveCount(1);
  await expect(page.locator('.comment-connector')).toHaveCount(1);
  await page.getByRole('button', { name: '查看图片评论 3，视频脚本!D4', exact: true }).click();
  await expect(page.locator('.review-comment.active')).toHaveAttribute('aria-label', '审核评论 3');
  const unverified = page.getByRole('article', { name: '审核评论 4', exact: true });
  await expect(unverified).toContainText('本项不计为通过');
  await expect(unverified).toContainText('模型返回的未核实引文：模型改写的台词');
  await expect(unverified.locator('blockquote')).toHaveCount(0);
  await unverified.getByRole('button', { name: '定位评论 4', exact: true }).click();
  await expect(page.locator('.comment-connector')).toHaveCount(0);
  await expect(page.locator('.sheet-cell .review-text-mark.active')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.locator('.review-comment-connections')).toHaveCSS('display', 'none');
  expect(errors).toEqual([]);
});
