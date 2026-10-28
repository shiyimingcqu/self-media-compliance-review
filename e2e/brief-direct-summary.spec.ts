import { test, expect } from '@playwright/test';

test('产品事实与四组规则对照原页，载入草稿不调用AI也不自动保存', async ({ page, request }) => {
  const p = (await (await request.post('/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: 'Brief 直接提炼验收', brandName: '测试品牌', platforms: ['xiaohongshu'] } })).json()).data;
  const item = (text: string, pending = false) => ({ text, pageNo: 20, quote: text, sourceBlockId: pending ? '' : 'native', citationStatus: pending ? 'pending' : 'verified' });
  const directSummary = { productFacts: { brand: [item('测试品牌')], product: [item('芝士浓奶铁')], sellingPoint: [item('芝士奶香') ] }, rules: { common: [item('不可产品镜像')], video: [item('前15秒完整露出产品'), item('视频引文待核对', true)], image: [item('产品露出不少于4张')], copy: [item('发布标题简练吸睛')] } };
  const brief = { sourceVersionId: 'source', revision: 1, title: '直接提炼 Brief', fileId: 'file', pageCount: 27, blocks: [], uncoveredSources: [], directSummary };
  await page.route(`**/api/v1/projects/${p.id}/brief`, r => r.fulfill({ json: { data: { brief, revision: 1 } } }));
  await page.route(`**/api/v1/projects/${p.id}/brief/facts`, r => r.fulfill({ json: { data: { sourceVersionId: 'source', facts: null, lastRun: null } } }));
  let paidCalls = 0, saves = 0;
  page.on('request', req => {
    if (req.method() !== 'POST') return;
    if (/standard-recommendations|brief\/vision/.test(req.url())) paidCalls++;
    if (/standards\/confirm/.test(req.url())) saves++;
  });
  await page.goto(`/projects/${p.id}/brief-review`);
  await expect(page.getByRole('tab', { name: '产品事实板' })).toBeVisible();
  await expect(page.getByText('芝士浓奶铁', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '四组规则' }).click();
  for (const label of ['通用规则', '视频规则', '图片规则', '文案规则']) await expect(page.getByRole('region', { name: label, exact: true })).toBeVisible();
  await expect(page.getByText('第 20 页', {exact:true})).toHaveCount(0);
  await expect(page.getByRole('region', { name: '视频规则', exact: true }).locator('[title="第 20 页"]').first()).toBeVisible();
  await page.getByText('前15秒完整露出产品', {exact:true}).click();
  await expect(page).toHaveURL(/page=20/);
  await page.goto(`/projects/${p.id}/rules?type=video`);
  await page.getByText('进阶版', { exact: true }).click();
  await page.getByRole('button', { name: '载入 Brief 规则（无需AI）' }).click();
  await expect.poll(() => page.locator('input').evaluateAll(nodes => nodes.map(node => node.value))).toContain('前15秒完整露出产品');
  await expect.poll(() => page.locator('input').evaluateAll(nodes => nodes.map(node => node.value))).not.toContain('产品露出不少于4张');
  expect(paidCalls).toBe(0);
  expect(saves).toBe(0);
});

test('导入Brief后重复AI生成保留当前草稿与停用状态，不重复追加（固定响应）', async ({ page, request }) => {
  const p = (await (await request.post('/api/v1/projects', { headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { name: 'Brief导入后增量生成', brandName: '测试品牌', platforms: ['xiaohongshu'] } })).json()).data;
  const text = '视频前15秒需清晰且完整露出产品，Logo正对镜头，对焦清晰，手持不遮挡Logo。';
  const defaults = (await (await request.get('/api/v1/workflow/video-rule-defaults')).json()).data.rules;
  const rule = (title: string, criterion = title) => ({ text: title, criterion, importance: 'must', category: '内容', layer: 'brief', origin: 'ai', enabled: true, sourceBlockId: '', sourceQuote: '' });
  const brief = { sourceVersionId: 'source', revision: 1, title: '增量生成Brief', blocks: [], uncoveredSources: [], directSummary: { productFacts: { brand: [], product: [], sellingPoint: [] }, rules: { common: [], video: [{ text, pageNo: 20, quote: text }], image: [], copy: [] } } };
  await page.route(`**/api/v1/projects/${p.id}/brief`, r => r.fulfill({ json: { data: { brief, revision: 1 } } }));
  await page.route(`**/api/v1/projects/${p.id}/brief/facts`, r => r.fulfill({ json: { data: { sourceVersionId: 'source', facts: null, lastRun: null } } }));
  await page.route('**/api/v1/settings/models/decomposition', r => r.fulfill({ json: { data: { hasKey: true, revision: 1 } } }));
  let calls = 0, saves = 0;
  page.on('request', r => { if (r.method() === 'POST' && /standards\/confirm/.test(r.url())) saves++; });
  await page.route(`**/api/v1/projects/${p.id}/standard-recommendations`, r => {
    if (r.request().method() !== 'POST') return r.fulfill({ json: { data: [] } });
    calls++;
    const draft = r.request().postDataJSON().existingVideoRules;
    expect(draft.find((item: { text: string }) => item.text === text)).toMatchObject({ enabled: false });
    return r.fulfill({ json: { data: { id: `incremental-${calls}`, scope: 'video', status: 'succeeded', snapshot: { sourceVersionId: 'source' }, result: { report: { rules: [...defaults, rule('前15秒完整展示产品与Logo', text), rule('视频须有食用画面')], warnings: [] } } } } });
  });
  await page.goto(`/projects/${p.id}/rules?type=video`);
  await page.getByText('进阶版', { exact: true }).click();
  await page.getByRole('button', { name: '载入 Brief 规则（无需AI）' }).click();
  await expect(page.locator('.video-layer-row')).toHaveCount(7);
  await page.getByRole('switch', { name: '启用视频第7条', exact: true }).click();
  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: 'AI 生成规则', exact: true }).click();
    await expect(page.locator('.video-layer-row')).toHaveCount(8);
    await expect(page.getByRole('button', { name: 'AI 生成规则', exact: true })).toBeEnabled();
    await expect(page.getByLabel('视频第7条要求', { exact: true })).toHaveValue(text);
    await expect(page.getByRole('switch', { name: '启用视频第7条', exact: true })).not.toBeChecked();
  }
  expect(calls).toBe(2);
  expect(saves).toBe(0);
});
