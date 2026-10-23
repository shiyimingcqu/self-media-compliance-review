import { test, expect } from '@playwright/test';
const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });

test('第三层组合组件、词句输入及旧项目明确切换（固定数据，无模型调用）', async ({ page, request }) => {
  test.setTimeout(90_000);
  const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: '组合组件与旧规则兼容', brandName: '样例品牌', platforms: ['douyin'] } })).json()).data;
  await page.goto(`/projects/${project.id}/rules?type=video`);
  await page.getByRole('radio', { name: '严格版', exact: true }).click();
  await page.getByRole('button', { name: '添加量化检查', exact: true }).click();
  await page.getByLabel('视频第4条要求', { exact: true }).fill('前15秒产品和商标同时可辨');
  await page.getByLabel('视频第4条判定说明', { exact: true }).fill('确认产品与商标在同一画面出现，不限制额外时长');
  const row = page.getByRole('article', { name: '视频第4条规则', exact: true });
  async function component(kind: string) {
    await row.getByRole('button', { name: '配置检查组件', exact: true }).click();
    await page.getByLabel('检查组件类型', { exact: true }).click();
    await page.getByTitle(kind, { exact: true }).last().click();
  }
  await component('产品露出检查');
  await page.getByLabel('上传品牌参考图').setInputFiles('e2e/fixtures/component-reference.png');
  await page.getByLabel('组件参考图').click();
  await page.getByTitle('component-reference.png', { exact: true }).last().click();
  await page.getByLabel('检查结束秒数').fill('15');
  await page.getByRole('button', { name: '确认组件参数', exact: true }).click();
  await component('商标露出检查');
  await page.getByLabel('组件参考图').click();
  await page.getByTitle('component-reference.png', { exact: true }).last().click();
  await page.getByLabel('检查结束秒数').fill('15');
  await page.getByRole('button', { name: '确认组件参数', exact: true }).click();
  await row.getByRole('checkbox', { name: '同一画面同时满足' }).check();
  await page.getByRole('switch', { name: '启用视频第4条', exact: true }).check();
  await page.getByRole('button', { name: '添加量化检查', exact: true }).click();
  await page.getByLabel('视频第5条要求', { exact: true }).fill('禁用表达逐句核对');
  await page.getByLabel('视频第5条判定说明', { exact: true }).fill('按品牌明确词句核对，不凭单字命中');
  await page.getByRole('article', { name: '视频第5条规则', exact: true }).getByRole('button', { name: '配置检查组件', exact: true }).click();
  await page.getByLabel('检查组件类型', { exact: true }).click();
  await page.getByTitle('禁用表达检查', { exact: true }).last().click();
  const terms = page.getByLabel('组件检查词句');
  await terms.fill('提神'); await terms.press('End'); await terms.press('Enter'); await terms.pressSequentially('醒脑');
  await expect(terms).toHaveValue('提神\n醒脑');
  await page.getByRole('button', { name: '确认组件参数', exact: true }).click();
  await page.getByRole('switch', { name: '启用视频第5条', exact: true }).check();
  const save = page.waitForResponse(r => r.url().endsWith('/standards/confirm') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '保存项目审核规则', exact: true }).click();
  expect((await save).status()).toBe(200);
  const standards = (await (await request.get(`/api/v1/projects/${project.id}/standards`)).json()).data;
  const plan = standards.video.rules[3].componentPlan;
  expect(plan.sameSegment).toBe(true); expect(plan.operator).toBe('all');
  expect(plan.nodes.map((node: any) => node.kind)).toEqual(['product_presence', 'logo_presence']);
  expect(standards.video.rules[4].componentPlan.nodes[0].parameters.terms).toEqual(['提神', '醒脑']);
  await page.reload();
  await expect(page.getByRole('checkbox', { name: '同一画面同时满足' })).toBeChecked();

  // Recreate a pre-layer client save: no implicit baseline or persisted migration on GET.
  function draft(rule: any) { const { text, criterion, category, importance, sourceBlockId, sourceQuote, origin, params } = rule; return { text, criterion, category, importance, sourceBlockId, sourceQuote, origin, params, componentPlan: rule.componentPlan ? { ...rule.componentPlan, nodes: rule.componentPlan.nodes.map(({ id, kind, parameters }: any) => ({ id, kind, parameters })) } : null }; }
  const legacyRules = standards.video.rules.slice(3).map(draft);
  const response = await request.post(`/api/v1/projects/${project.id}/standards/confirm`, { headers: headers(), data: {
    revision: standards.revision, scriptRules: standards.script.rules.map(draft), imageRules: standards.image.rules.map(draft),
    videoRules: legacyRules, scriptConfirmed: true, videoConfirmed: true, confirmationNote: '旧客户端保存兼容测试',
  } });
  expect(response.status(), await response.text()).toBe(200);
  await page.reload();
  await expect(page.getByText('旧规则已按现有内容继续展示，原有条件与重要性保留；保存新版本后切换为新格式', { exact: true })).toBeVisible();
  await expect(page.locator('.video-layer-row')).toHaveCount(2);
  const before = (await (await request.get(`/api/v1/projects/${project.id}/standards`)).json()).data;
  expect(before.video.rules[0].layer).toBeUndefined();
  await page.getByRole('button', { name: '采用默认基础规则', exact: true }).click();
  await expect(page.locator('.video-layer-row')).toHaveCount(5);
  expect((await (await request.get(`/api/v1/projects/${project.id}/standards`)).json()).data.video.rules).toEqual(before.video.rules);
  const migrated = page.waitForResponse(r => r.url().endsWith('/standards/confirm') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '保存项目审核规则', exact: true }).click();
  expect((await migrated).status()).toBe(200);
  await page.reload();
  const after = (await (await request.get(`/api/v1/projects/${project.id}/standards`)).json()).data;
  expect(after.video.rules.map((rule: any) => rule.layer)).toEqual(['baseline', 'baseline', 'baseline', 'precise', 'precise']);
  expect(after.video.rules[3].componentPlan).toEqual(before.video.rules[0].componentPlan);
  await expect(page.locator('.video-layer-row')).toHaveCount(5);
  await expect(page.getByRole('checkbox', { name: '同一画面同时满足' })).toBeChecked();
  await page.screenshot({ path: 'test-results/video-layer-components.png', fullPage: true });
});
