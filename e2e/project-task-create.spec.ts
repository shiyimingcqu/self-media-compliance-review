// D t-29 浏览器验收（A 03:00/10:28 批准）：任务创建 Drawer 真实交互。
// 隔离栈：playwright.config webServer 8001=scripts/e2e-backend.py（TemporaryDirectory
// 专属库）、5175=vite（API_PROXY_TARGET=8001）、reuseExistingServer=false——不触 demo 库。
// seed 全走真实 API（每步断言 HTTP 状态/error envelope，不吞失败）；
// 所有 page.evaluate 参数可序列化传入（Node FormData/闭包变量不跨界）；
// UI 操作走真实 Drawer；502 注入单列（非真实服务证据）。
// 契约依据（brief_script/router.py 实读）：register→{briefId,sourceVersionId}；
// createVersion→VersionView{id,revision}；requirement body.source={manualNote}；
// confirm/enable 携 If-Match=当前 revision（每次 bump 后更新）。
import { expect, test } from '@playwright/test';

/** 每次准备的独立 fixture 标识（opaque UUID）：两用例互不共享 seed 命名空间。
 * 项目名/文件名/标题/文本/所有写操作幂等键均携带本 id（长度合规 8..128）；
 * 同一 fixture 内逻辑重试仍复用相同 key（HTTP 失败不换 key）。 */
function makeFixture(): string {
  return crypto.randomUUID().replace(/-/g, '').slice(0, 16);
}

/** 浏览器内 fetch：参数可序列化传入；断言状态码与 error envelope。 */
async function apiCall(page: import('@playwright/test').Page, path: string,
                       init: { method?: string; headers?: Record<string, string>; body?: unknown } = {}) {
  return page.evaluate(async ({ p, method, headers, body }) => {
    const r = await fetch(`/api/v1${p}`, { method, headers, body: body ?? undefined });
    const json = await r.json().catch(() => null);
    return { status: r.status, ok: r.ok, data: json?.data ?? null, error: json?.error ?? null };
  }, { p: path, method: init.method ?? 'GET', headers: init.headers, body: init.body });
}

async function mustOk(res: { status: number; ok: boolean; error: unknown },
                      step: string, expectStatus = 200) {
  expect(res.status, `${step} 状态`).toBe(expectStatus);
  expect(res.error, `${step} 无错误`).toBeNull();
  expect(res.data, `${step} 有数据`).toBeTruthy();
  return res.data as Record<string, unknown>;
}

/** 读版本最新 revision（写步骤 If-Match 一律从真实 GET 实取，不手算不缓存）。
 * 契约（generated VersionDetail）：GET /rule-versions/:id → data.version.revision
 * ——结构以显式断言约束，不靠猜字段。 */
async function currentRevision(page: import('@playwright/test').Page, versionId: string): Promise<number> {
  const detail = await mustOk(await apiCall(page, `/rule-versions/${versionId}`), '读版本 detail');
  const version = detail.version;
  expect(version, 'VersionDetail.version 存在').toBeTruthy();
  expect(typeof version.revision, 'version.revision 为数字').toBe('number');
  expect(typeof version.id, 'version.id 为字符串').toBe('string');
  return version.revision as number;
}

/** 真实 API 准备：项目→TXT 上传→Brief 登记→草稿→人工 must 规则→确认→启用。
 * 所有资源命名与幂等键均携带本 fixture 的独立 id——两用例/重跑互不撞键。 */
async function seedEnabledRule(page: import('@playwright/test').Page) {
  const fx = makeFixture();  // 本 fixture 独立命名空间
  await page.goto('/projects');  // 同源先行（about:blank 相对 fetch 不可解析）

  const project = await mustOk(await apiCall(page, '/projects', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `seed-${fx}-proj` },
    body: JSON.stringify({ name: `E2E任务项目${fx}`, brandName: 'E2E品牌', platforms: ['xiaohongshu'] }) }),
    '创建项目', 201);

  // TXT 上传：FormData/File 在浏览器函数内构造（Node File 不可跨 evaluate 序列化）
  const file = await mustOk(await page.evaluate(async ({ fx, key }) => {
    const form = new FormData();
    form.append('file', new File([`必须口播品牌全称 ${fx}\n`], `brief-${fx}.txt`,
      { type: 'text/plain' }));
    form.append('purpose', 'brief');
    const r = await fetch('/api/v1/files', { method: 'POST', headers: { 'Idempotency-Key': key }, body: form });
    const json = await r.json().catch(() => null);
    return { status: r.status, ok: r.ok, data: json?.data ?? null, error: json?.error ?? null };
  }, { fx, key: `seed-${fx}-file` }), '上传 TXT', 201);

  const brief = await mustOk(await apiCall(page, `/projects/${project.id}/briefs`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `seed-${fx}-brief` },
    body: JSON.stringify({ fileId: file.id, title: `E2E任务Brief${fx}`, platforms: ['xiaohongshu'] }) }),
    '登记 Brief', 201);
  // 契约：register 返回 briefId/sourceVersionId（非 brief.id/latestSourceVersionId）

  const version = await mustOk(await apiCall(page, `/briefs/${brief.briefId}/rule-versions`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `seed-${fx}-rv` },
    body: JSON.stringify({ sourceVersionId: brief.sourceVersionId }) }),
    '创建草稿', 201);
  const versionId = version.id as string;

  // 人工 must 规则：source 嵌套 manualNote（品牌补充——无文档坐标）；
  // If-Match 从最近 GET 实取（添加会 bump 父 revision——不缓存旧值）
  const req = await mustOk(await apiCall(page, `/rule-versions/${versionId}/requirements`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `seed-${fx}-req`,
               'If-Match': String(await currentRevision(page, versionId)) },
    body: JSON.stringify({ text: `E2E 必须露出品牌 ${fx}`, category: '禁用表达',
      importance: 'must', platforms: ['xiaohongshu'], contentTypes: ['video'],
      phases: ['general'], reviewStages: ['script', 'video'],
      source: { manualNote: 'E2E 人工补充' } }) }),
    '添加 must 规则', 201);

  // 确认：If-Match 重新 GET 实取（add 已 bump）；key 含本 fixture id 不跨用例撞
  await mustOk(await apiCall(page, `/rule-versions/${versionId}/confirm`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `seed-${fx}-cfm`,
               'If-Match': String(await currentRevision(page, versionId)) },
    body: JSON.stringify({ requirementIds: [req.id] }) }), '批量确认');

  // 启用：If-Match 再 GET（confirm 又 bump）
  await mustOk(await apiCall(page, `/rule-versions/${versionId}/enable`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `seed-${fx}-en`,
               'If-Match': String(await currentRevision(page, versionId)) },
    body: JSON.stringify({ changeSummary: 'E2E 启用' }) }), '启用');

  return { projectId: project.id as string, versionLabel: version.version_label as string, fx };
}

/** Drawer 表单填充（平台/版本选择后 detail 加载，再由调用方断言徽标）。 */
async function fillDrawer(page: import('@playwright/test').Page, creatorName: string) {
  const drawer = page.locator('.ant-drawer', { hasText: '新建达人任务' });
  await drawer.getByLabel('达人名称').fill(creatorName);
  await drawer.getByLabel('投放平台').click();
  await page.getByText('小红书', { exact: true }).last().click();
  await page.keyboard.press('Escape');
  await drawer.getByLabel('绑定规则版本').click();
  await page.getByText(/E2E任务Brief/).last().click();
  await page.keyboard.press('Escape');
  return drawer;
}

async function submitViaSummary(page: import('@playwright/test').Page) {
  const drawer = page.locator('.ant-drawer', { hasText: '新建达人任务' });
  await drawer.getByRole('button', { name: '创建任务' }).click();
  const summary = page.locator('.ant-modal-confirm', { hasText: '确认创建任务' });
  await expect(summary).toBeVisible();
  await summary.getByRole('button', { name: /^创\s*建$/ }).click();
}

test('任务创建闭环：enabled 可选→中文创建→409 重复确认→取消/ack', async ({ page }) => {
  test.setTimeout(180_000);
  const { projectId } = await seedEnabledRule(page);
  const idempotencyKeys: string[] = [];
  page.on('request', r => {
    const k = r.headers()['idempotency-key'];
    if (k && r.url().includes('/tasks') && r.method() === 'POST') idempotencyKeys.push(k);
  });

  // 正常创建：选完平台/版本（detail 拉取）→ 兼容徽标出现 → 提交
  await page.goto(`/projects/${projectId}`);
  await page.getByRole('button', { name: '新建任务' }).click();
  const drawer1 = await fillDrawer(page, '达'.repeat(50));  // 50 字中文名
  await expect(drawer1.getByText(/匹配 1 条已确认必须项/)).toBeVisible({ timeout: 30_000 });
  await submitViaSummary(page);
  const summary = page.locator('.ant-modal-confirm', { hasText: '确认创建任务' });
  await expect(summary.locator('strong').first()).toHaveText('达'.repeat(50));  // 摘要显式呈现
  await expect(page.getByText('任务已创建')).toBeVisible({ timeout: 30_000 });
  // header 只允许 UUID
  expect(idempotencyKeys.length).toBeGreaterThanOrEqual(1);
  for (const k of idempotencyKeys)
    expect(k).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
  await expect(page.getByText('达'.repeat(50)).first()).toBeVisible();  // 列表真值

  // 重复创建 → 409 → 重复确认 → 取消（零第二 POST）
  const postsBefore = idempotencyKeys.length;
  await page.getByRole('button', { name: '新建任务' }).click();
  const drawer2 = await fillDrawer(page, '达'.repeat(50));
  await expect(drawer2.getByText(/匹配 1 条已确认必须项/)).toBeVisible({ timeout: 30_000 });
  await submitViaSummary(page);
  const dupConfirm = page.locator('.ant-modal-confirm', { hasText: '确认创建重复' });
  await expect(dupConfirm).toBeVisible({ timeout: 30_000 });
  await dupConfirm.getByRole('button', { name: /返回修改/ }).click();
  await expect(dupConfirm).toBeHidden();  // UI 事件替代 waitForTimeout
  expect(idempotencyKeys.length).toBe(postsBefore + 1);  // 仅首个 POST，无 ack 重发

  // 关闭抽屉（有未保存输入 → 真实 UI 放弃确认）
  await drawer2.getByRole('button', { name: '取消' }).click();
  const discard = page.locator('.ant-modal-confirm', { hasText: '放弃未保存' });
  await expect(discard).toBeVisible();
  await discard.getByRole('button', { name: /^放\s*弃$/ }).click();
  // 关闭断言（内容语义级）：antd6 根壳常驻 DOM（.ant-drawer 恒 visible，
  // 仅 content-wrapper-hidden）——断言 dialog 角色隐藏 + 表单字段不可见，
  // 并验证项目页按钮可点击无遮罩（真实可用性，非壳计数）
  await expect(page.locator('.ant-drawer [role="dialog"]', { hasText: '新建达人任务' })).toBeHidden({ timeout: 10_000 });
  await expect(page.getByLabel('达人名称')).toBeHidden({ timeout: 10_000 });
  const newTaskBtn = page.getByRole('button', { name: '新建任务' });
  await expect(newTaskBtn).toBeVisible();
  await newTaskBtn.click();  // 无遮罩拦截：能直接重开
  // 重开后字段已清（丢弃确认真的 reset——旧输入不残留）
  const drawer3 = page.locator('.ant-drawer', { hasText: '新建达人任务' });
  await expect(drawer3.getByLabel('达人名称')).toHaveValue('', { timeout: 10_000 });
  await expect(drawer3.getByLabel('达人名称')).toBeEditable();  // 抽屉实际可用

  // 确认路径（沿用已重开的 drawer3）：填同名同平台同版本 → 409 → 确认 ack
  await drawer3.getByLabel('达人名称').fill('达'.repeat(50));
  await drawer3.getByLabel('投放平台').click();
  await page.getByText('小红书', { exact: true }).last().click();
  await page.keyboard.press('Escape');
  await drawer3.getByLabel('绑定规则版本').click();
  await page.getByText(/E2E任务Brief/).last().click();
  await page.keyboard.press('Escape');
  await expect(drawer3.getByText(/匹配 1 条已确认必须项/)).toBeVisible({ timeout: 30_000 });
  await submitViaSummary(page);
  const dupConfirm2 = page.locator('.ant-modal-confirm', { hasText: '确认创建重复' });
  await expect(dupConfirm2).toBeVisible({ timeout: 30_000 });
  const ackStart = idempotencyKeys.length;
  await dupConfirm2.getByRole('button', { name: /确认创建/ }).click();
  await expect(page.getByText('任务已创建')).toBeVisible({ timeout: 30_000 });
  expect(idempotencyKeys.length).toBe(ackStart + 1);  // 恰一次 ack POST
  expect(idempotencyKeys[ackStart]).not.toBe(idempotencyKeys[ackStart - 1]);  // 新 key
});

test('网络故障注入（单列，非真实服务证据）：502 后同 payload 重试复用 key', async ({ page }) => {
  test.setTimeout(180_000);
  const { projectId, fx } = await seedEnabledRule(page);
  const keys: string[] = [];
  let injected502 = false;
  await page.route('**/api/v1/projects/*/tasks', async route => {
    if (route.request().method() === 'POST' && injected502) {
      injected502 = false;
      await route.fulfill({ status: 502, contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'PROVIDER_ERROR', message: '注入' }, requestId: 'inj' }) });
      return;
    }
    await route.continue();
  });
  page.on('request', r => {
    const k = r.headers()['idempotency-key'];
    if (k && r.url().includes('/tasks') && r.method() === 'POST') keys.push(k);
  });

  await page.goto(`/projects/${projectId}`);
  injected502 = true;  // 首次 POST 注入 502
  await page.getByRole('button', { name: '新建任务' }).click();
  const drawer = await fillDrawer(page, `E2E达人${fx}`);
  await expect(drawer.getByText(/匹配 1 条已确认必须项/)).toBeVisible({ timeout: 30_000 });
  await submitViaSummary(page);
  // 502 呈现（产品实际反馈约定：error.message 透传）
  await expect(page.getByText('注入').or(page.getByText('创建失败，请重试'))).toBeVisible({ timeout: 30_000 });
  // 草稿保留（确认弹窗已关、抽屉与输入仍在——内容语义级断言）→ 同 payload 直接重试
  const drawerStill = page.locator('.ant-drawer', { hasText: '新建达人任务' });
  await expect(drawerStill.getByLabel('达人名称')).toBeVisible();
  await expect(drawerStill.getByLabel('达人名称')).toHaveValue(`E2E达人${fx}`);
  await submitViaSummary(page);
  await expect(page.getByText('任务已创建')).toBeVisible({ timeout: 30_000 });
  expect(keys.length).toBe(2);
  expect(keys[1]).toBe(keys[0]);  // 同 payload 复用 key（网络重试语义）
});
