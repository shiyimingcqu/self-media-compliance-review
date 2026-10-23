// t-26 浏览器验收（A 02:46 批准第 8 文件）：真实 API 走一条
// TXT 上传→Brief 登记→创建草稿→人工补充（品牌补充来源）→批量确认→启用只读→刷新同版本恢复。
// 隔离栈由 playwright.config webServer 提供：8001=scripts/e2e-backend.py（TemporaryDirectory 专属库），
// 5175=vite（API_PROXY_TARGET=8001），reuseExistingServer=false；不触 demo 库/其他项目服务。
// 断言真实刷新（revision/状态文本变化），不用 sleep 掩盖或放宽断言。
import { expect, test } from '@playwright/test';

const stamp = Date.now();
const TITLE = `E2E Brief ${stamp}`;
const RULE_TEXT = `E2E 禁止功效表达 ${stamp}`;

test('Brief 人工规则闭环：登记→草稿→补充→确认→启用只读→刷新恢复', async ({ page }) => {
  test.setTimeout(120_000);

  // 1) 新建项目（真实 API 走 UI；平台=小红书）
  await page.goto('/projects');
  await page.getByRole('button', { name: '新建项目' }).click();
  const modal = page.getByRole('dialog', { name: '新建项目' });
  await modal.getByLabel('项目名称').fill(`E2E 项目 ${stamp}`);
  await modal.getByLabel('品牌').fill('E2E 品牌');
  await modal.getByRole('checkbox', { name: '小红书' }).check();
  await modal.getByRole('button', { name: '创建' }).click();
  await expect(page).toHaveURL(/\/projects\/[^/]+$/, { timeout: 30_000 });
  const projectId = decodeURIComponent(page.url().split('/').pop()!);

  // 2) 登记 Brief（TXT 上传两步；成功导航真实 briefId）
  await page.goto(`/projects/${projectId}/briefs`);
  const txt = page.locator('#brief-file-input');
  await txt.setInputFiles({ name: `brief-${stamp}.txt`, mimeType: 'text/plain',
    buffer: Buffer.from(`必须口播品牌全称 ${stamp}\n\n禁止出现功效词 ${stamp}`, 'utf-8') });
  await page.getByLabel('Brief 标题').fill(TITLE);
  await page.getByLabel('适用平台（须为项目平台子集）').click();
  await page.getByText('xiaohongshu', { exact: true }).last().click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '上传并登记' }).click();
  await expect(page).toHaveURL(/\/briefs\/[0-9a-f]{32}$/, { timeout: 30_000 });
  const briefId = decodeURIComponent(new URL(page.url()).pathname.split('/').pop()!);
  await expect(page.getByRole('strong').filter({ hasText: TITLE })).toBeVisible();

  // 3) 创建首个规则草稿
  await page.getByRole('button', { name: '创建首个规则草稿' }).click();
  await expect(page.getByText(/审核要求 · v1（draft，revision 1/)).toBeVisible({ timeout: 30_000 });
  // t-30 修订2 回归：新建草稿后**不刷新浏览器**，project 投影同步→该版本 source 可得→preview 立即可用
  await expect(page.getByText('持久投影已验证')).toBeVisible({ timeout: 30_000 });

  // 4) 人工补充要求（品牌补充来源）
  const addForm = page.getByLabel('要求内容（1–500 字）');
  await addForm.fill(RULE_TEXT);
  await page.getByLabel('类别').click();
  await page.getByText('禁用表达', { exact: true }).last().click();
  await page.getByRole('radio', { name: '品牌人工补充' }).check();
  await page.getByLabel('品牌补充说明（不得携带文档坐标）').fill('E2E 人工核对补充');
  await page.getByRole('button', { name: '添加要求' }).click();
  await expect(page.getByText(RULE_TEXT)).toBeVisible({ timeout: 30_000 });
  // 真值刷新：revision 从 1→2（添加要求 bump）
  await expect(page.getByText(/revision 2）/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'AI 提取（未接入）' })).toBeDisabled();

  // 5) 批量确认：选中该行→确认→状态变已确认+revision 再 bump；确认成功 modal 后列表真值刷新
  await page.getByRole('checkbox', { name: /Select row|选择行/ }).or(page.locator('.ant-table-tbody input[type="checkbox"]')).first().check();
  await page.getByRole('button', { name: /批量确认/ }).click();
  await expect(page.getByText(/已确认/).first()).toBeVisible({ timeout: 30_000 });
  const headerAfterConfirm = page.getByText(/审核要求 · v1（pending_confirmation，revision 3）/);
  await expect(headerAfterConfirm).toBeVisible({ timeout: 30_000 });

  // 6) 启用：门禁预检通过→模态填摘要→成功→只读快照
  await page.getByRole('button', { name: '确认并启用' }).click();
  const enableModal = page.locator('.ant-modal-confirm, .ant-modal').last();
  await enableModal.locator('textarea').fill('E2E 首次启用');
  await enableModal.getByRole('button', { name: /启\s*用/ }).click();
  await expect(page.getByText(/审核要求 · v1（enabled，revision 4）/)).toBeVisible({ timeout: 30_000 });
  // 只读：添加要求表单消失，派生按钮出现
  await expect(page.getByRole('button', { name: '添加要求' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '派生新草稿' })).toBeVisible();

  // 7) 刷新恢复：同 URL reload → 投影/版本/enabled 只读仍真值；
  // t-30 后原文段落经持久 preview 恢复（不再依赖登记内存态），且引用对应所选版本真实源。
  await page.reload();
  await expect(page.getByRole('strong').filter({ hasText: TITLE })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/审核要求 · v1（enabled，revision 4）/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: '派生新草稿' })).toBeVisible();
  // 持久投影已验证（t-30）：刷新后原文段落仍可见，来自所选版本 source 的 preview
  await expect(page.getByText('持久投影已验证')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(`必须口播品牌全称 ${stamp}`)).toBeVisible();
  await expect(page.getByText(`禁止出现功效词 ${stamp}`)).toBeVisible();

  // 8) 后端真值交叉核对（在页面上下文经代理请求，不经 UI 组件态）
  const detail = await page.evaluate(async (id) => (await fetch(`/api/v1/projects/${id}`)).json(), projectId);
  const brief = detail.data.briefs.find((b: { id: string }) => b.id === briefId);
  expect(brief).toBeTruthy();
  expect(brief.extractionStatus).toBe('ready');
  expect(brief.versions.some((v: { state: string }) => v.state === 'enabled')).toBe(true);
});
