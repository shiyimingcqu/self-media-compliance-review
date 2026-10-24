import { test, expect } from '@playwright/test';

test('我的合作按脚本和成片两项计进度，视频图文任选一种', async ({ page }) => {
  const empty = { status: 'not_submitted', submitted: false, approved: false };
  const submitted = { status: 'pending_brand', submitted: true, approved: false, submittedAt: '2026-10-24T06:00:00Z' };
  const base = { creator_name: '合作达人', brand_name: '麦咖啡', platform: 'douyin', briefTitle: '0923芝士奶铁 抖音Social Brief.pdf', briefFileId: null, rulesReady: true, currentVersion: 's1', scriptAcceptance: { passed: true, status: 'passed', checks: [], blockingCount: 0, message: '' }, brandApproved: false, brandDecision: null };
  const tasks = [
    { ...base, id: 'video-task', project_id: 'p1', project_name: '0923芝士奶铁 抖音Social Brief', deliveries: { script: submitted, video: submitted, image: empty } },
    { ...base, id: 'image-task', project_id: 'p2', project_name: '图文合作', deliveries: { script: submitted, video: empty, image: submitted } },
    { ...base, id: 'empty-task', project_id: 'p3', project_name: '待开始的合作', deliveries: { script: empty, video: empty, image: empty } },
    { ...base, id: 'uploaded-task', project_id: 'p4', project_name: '已上传未审核', deliveries: { script: { ...empty, status: 'pending_review', hasMaterial: true, hasReview: false, uploadedAt: '2026-10-24T06:00:00Z' }, video: empty, image: empty } },
  ];
  await page.route('**/api/v1/creator/tasks', r => r.fulfill({ json: { data: tasks } }));
  await page.route('**/creator-reminders', r => r.fulfill({ json: { data: [] } }));
  await page.addInitScript(() => localStorage.setItem('review-demo-role', 'creator'));
  await page.goto('/creator');
  const videoCard = page.getByRole('region', { name: '合作任务 0923芝士奶铁 抖音Social Brief', exact: true });
  await expect(videoCard.getByLabel('合作进度 2/2 已提交', { exact: true })).toBeVisible();
  await expect(videoCard.getByRole('article', { name: '图文交付', exact: true })).toContainText('无需提交');
  const imageCard = page.getByRole('region', { name: '合作任务 图文合作', exact: true });
  await expect(imageCard.getByLabel('合作进度 2/2 已提交', { exact: true })).toBeVisible();
  await expect(imageCard.getByRole('article', { name: '视频交付', exact: true })).toContainText('无需提交');
  const emptyCard = page.getByRole('region', { name: '合作任务 待开始的合作', exact: true });
  await expect(emptyCard.getByLabel('合作进度 0/2 已提交', { exact: true })).toBeVisible();
  await expect(emptyCard.getByRole('article', { name: '视频交付', exact: true }).getByRole('link', { name: '提交材料', exact: true })).toHaveAttribute('href', '/creator/projects/p3/tasks/empty-task/video?action=submit');
  await expect(emptyCard.getByRole('article', { name: '图文交付', exact: true }).getByRole('link', { name: '提交材料', exact: true })).toHaveAttribute('href', '/creator/projects/p3/tasks/empty-task/image?action=submit');
  const uploadedCard = page.getByRole('region', { name: '合作任务 已上传未审核', exact: true });
  const uploadedScript = uploadedCard.getByRole('article', {name:'脚本交付',exact:true});
  await expect(uploadedScript.getByRole('link', {name:'提交验收',exact:true})).toBeVisible();
  await expect(uploadedScript.getByRole('link', {name:'查看反馈',exact:true})).toHaveCount(0);
  await expect(uploadedScript).not.toContainText('等待品牌复核');
  await videoCard.screenshot({ path: 'test-results/creator-cooperation-card.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('未提交材料的入口直接打开对应上传窗口，不调用 AI', async ({ page, request }) => {
  const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
  const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: `空材料入口-${Date.now()}`, brandName: '品牌A', platforms: ['douyin'] } })).json()).data;
  const rule = { text: '展示产品', criterion: '出现产品即可', importance: 'must', category: '产品' };
  const task = (await (await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: '空材料达人', platform: 'douyin', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '固定测试' } })).json()).data.id;
  const paidCalls: string[] = [];
  page.on('request', r => { if (r.method() === 'POST' && /evidence-runs|script-reviews|submit-and-review|image-posts\/reviews/.test(r.url())) paidCalls.push(r.url()); });
  await page.addInitScript(() => { localStorage.setItem('review-demo-role', 'creator'); localStorage.setItem('review-creator-name', '空材料达人'); });
  for (const [kind, title] of [['脚本', '提交脚本'], ['视频', '提交完整视频进行验收'], ['图文', '提交图文']]) {
    await page.goto('/creator');
    const card = page.getByRole('region', { name: `合作任务 ${project.name}`, exact: true });
    await card.getByRole('article', { name: `${kind}交付`, exact: true }).getByRole('link', { name: '提交材料', exact: true }).click();
    await expect(page.getByRole('dialog', { name: title, exact: true })).toBeVisible();
    expect(page.url()).toContain(`/tasks/${task}`);
  }
  expect(paidCalls).toEqual([]);
});
