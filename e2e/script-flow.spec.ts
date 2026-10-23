// t-31 第4文件：脚本页 ScriptPage 隔离浏览器验收（A 11:32 批准编写，执行待 D 两例整体绿+端口释放）。
// 隔离栈复用 playwright.config webServer：8001=scripts/e2e-backend.py（TemporaryDirectory 专属库）、
// 5175=vite（API_PROXY_TARGET=8001）、reuseExistingServer=false、workers=1；不触 demo 库/其他服务。
// 硬约束（A 修正）：① 每用例独立 UUID namespace（不共享模块级 Date.now，避免 seed 幂等键串台）；
// ② invalid 走真实上传+解析（utf-16-le 无 BOM，经文件层通过、decode_reliable 严格 utf-8 失败，
//    与 backend/test_script_versions.py:236 同一实证路径）不直插/不绕校验；
// ③ 归档场景：grep 实证 foundation 无归档写端点（archived_at/status='archived' 仅读侧门）→
//    如实标注能力缺口/未覆盖，不 SQL/mock/临时扩后端；
// ④ task 切换用客户端 SPA 导航（面包屑/任务链接），不用整页 goto 重载替代该上下文竞争证据；
// ⑤ 成功反馈按 ScriptPage 实际 `脚本版本 sN 已登记（解析：…）` 语义，不套"任务已创建"。
// 未运行前口径：以下定位器均据 ScriptPage/端点源码书写，尚未真实浏览器核过（见回报）。
import { expect, test } from '@playwright/test';

const NS = crypto.randomUUID().replace(/-/g, '');

/** 浏览器内 fetch：path/method/headers/body 全可序列化入参（不跨界引用 Node 闭包变量）。 */
async function apiCall(page: import('@playwright/test').Page,
  req: { path: string; method?: string; headers?: Record<string, string>; body?: string }) {
  return page.evaluate(async ({ p, method, headers, body }) => {
    const r = await fetch(`/api/v1${p}`, { method: method ?? 'GET', headers, body });
    const json = await r.json().catch(() => null);
    return { status: r.status, ok: r.ok, data: json?.data ?? null, error: json?.error ?? null };
  }, { p: req.path, method: req.method, headers: req.headers, body: req.body });
}

async function mustStatus(page: import('@playwright/test').Page,
  req: Parameters<typeof apiCall>[1], expectStatus: number, step: string) {
  const res = await apiCall(page, req);
  expect(res.status, `${step} 状态`).toBe(expectStatus);
  expect(res.error, `${step} 无错误`).toBeNull();
  expect(res.data, `${step} 有数据`).toBeTruthy();
  return res.data as Record<string, unknown>;
}

/** 写前从 VersionDetail 真实取 revision（嵌套 version.revision），不手算/不缓存旧值。 */
async function currentRevision(page: import('@playwright/test').Page, versionId: string): Promise<number> {
  const detail = await mustStatus(page, { path: `/rule-versions/${versionId}` }, 200, '读规则版本 detail');
  const rev = (detail.version as Record<string, unknown> | undefined)?.revision;
  expect(typeof rev, 'detail.version.revision 为数字').toBe('number');
  return rev as number;
}

const JSONH = (extra: Record<string, string> = {}) => ({ 'Content-Type': 'application/json', ...extra });

/** 真实 API 准备：项目+TXT brief+草稿+must 规则+确认+启用，返回可绑定 enabled 的项目与版本。 */
async function seedEnabledVersion(page: import('@playwright/test').Page) {
  const project = await mustStatus(page, {
    path: '/projects', method: 'POST', headers: JSONH({ 'Idempotency-Key': crypto.randomUUID() }),
    body: JSON.stringify({ name: `脚本E2E${NS}`, brandName: 'E2E品牌', platforms: ['xiaohongshu'] }),
  }, 201, '建项目');
  // 上传 brief TXT（浏览器内构造 FormData，键经参数注入）
  const fileRes = await page.evaluate(async ({ ns, key }) => {
    const form = new FormData();
    form.append('file', new File([`必须口播品牌全称 ${ns}\n\n禁止功效词 ${ns}`], `brief-${ns}.txt`, { type: 'text/plain' }));
    form.append('purpose', 'brief');
    const r = await fetch('/api/v1/files', { method: 'POST', headers: { 'Idempotency-Key': key }, body: form });
    return { status: r.status, data: (await r.json()).data as Record<string, unknown> };
  }, { ns: NS, key: crypto.randomUUID() });
  expect(fileRes.status, '上传 brief TXT').toBe(201);
  const brief = await mustStatus(page, {
    path: `/projects/${project.id}/briefs`, method: 'POST', headers: JSONH({ 'Idempotency-Key': crypto.randomUUID() }),
    body: JSON.stringify({ fileId: fileRes.data.id, title: `脚本Brief${NS}`, platforms: ['xiaohongshu'] }),
  }, 201, '登记 brief');
  const draft = await mustStatus(page, {
    path: `/briefs/${brief.briefId}/rule-versions`, method: 'POST', headers: JSONH({ 'Idempotency-Key': crypto.randomUUID() }),
    body: JSON.stringify({ sourceVersionId: brief.sourceVersionId }),
  }, 201, '建规则草稿');
  const versionId = draft.id as string;
  await mustStatus(page, {
    path: `/rule-versions/${versionId}/requirements`, method: 'POST',
    headers: JSONH({ 'Idempotency-Key': crypto.randomUUID(), 'If-Match': String(await currentRevision(page, versionId)) }),
    body: JSON.stringify({ text: `脚本E2E 必须露出品牌 ${NS}`, category: '禁用表达', importance: 'must',
      platforms: ['xiaohongshu'], contentTypes: ['video'], phases: ['general'],
      reviewStages: ['script', 'video'], source: { manualNote: 'E2E 人工补充' } }),
  }, 201, '加 must 规则');
  const detail = await mustStatus(page, { path: `/rule-versions/${versionId}` }, 200, '读规则');
  const reqIds = (detail.requirements as { id: string }[]).map((r) => r.id);
  await mustStatus(page, {
    path: `/rule-versions/${versionId}/confirm`, method: 'POST',
    headers: JSONH({ 'Idempotency-Key': crypto.randomUUID(), 'If-Match': String(await currentRevision(page, versionId)) }),
    body: JSON.stringify({ requirementIds: reqIds }),
  }, 200, '批量确认');
  await mustStatus(page, {
    path: `/rule-versions/${versionId}/enable`, method: 'POST',
    headers: JSONH({ 'Idempotency-Key': crypto.randomUUID(), 'If-Match': String(await currentRevision(page, versionId)) }),
    body: JSON.stringify({ changeSummary: 'E2E 启用' }),
  }, 200, '启用版本');
  return { projectId: project.id as string, versionId };
}

async function createTask(page: import('@playwright/test').Page, projectId: string, ruleVersionId: string, name: string) {
  const task = await mustStatus(page, {
    path: `/projects/${projectId}/tasks`, method: 'POST', headers: JSONH({ 'Idempotency-Key': crypto.randomUUID() }),
    body: JSON.stringify({ creatorName: name, platform: 'xiaohongshu', contentType: 'video',
      campaignPhase: 'seeding', ruleVersionId }),
  }, 201, `建任务 ${name}`);
  return task.id as string;
}

test('脚本页：粘贴/TXT 登记、invalid 当前不被顶替、历史 ?version reload 仍同版、SPA 切换不残旧态', async ({ page }) => {
  test.setTimeout(180_000);
  // seed 用浏览器内相对 fetch，须先落到真实前端路由建立同源（about:blank 下相对 URL 无法解析）。
  await page.goto('/projects');
  const { projectId, versionId } = await seedEnabledVersion(page);
  const tA = await createTask(page, projectId, versionId, `达A${NS}`);
  const tB = await createTask(page, projectId, versionId, `达B${NS}`);

  // 进入任务 A 脚本页（首进用 goto 建立初始上下文可接受）
  await page.goto(`/projects/${projectId}/tasks/${tA}/script`);
  await expect(page.getByRole('strong').filter({ hasText: `达A${NS}` })).toBeVisible({ timeout: 30_000 });

  // a) 粘贴合法正文 → 真实登记语义 toast + 当前 ready + 段落 p1 可见
  await page.getByRole('radio', { name: '粘贴文本' }).check();
  await page.getByPlaceholder(/脚本正文/).fill(`第一段口播 ${NS}\n\n第二段禁用词 ${NS}`);
  await page.getByRole('button', { name: '提交脚本版本' }).click();
  await expect(page.getByText(/脚本版本 s1 已登记/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('cell', { name: 'p1' }).first()).toBeVisible();

  // b) 纯空白 → 前端先拦（按钮禁用），不产生新版本
  await page.getByPlaceholder(/脚本正文/).fill('   ');
  await expect(page.getByRole('button', { name: '提交脚本版本' })).toBeDisabled();

  // c) 正常 TXT 上传 → s2 ready，文件行 charCount 如实"未持久"不伪 0
  await page.getByRole('radio', { name: '上传 TXT' }).check();
  await page.locator('input[type=file]').setInputFiles({
    name: `ok-${NS}.txt`, mimeType: 'text/plain', buffer: Buffer.from(`正常脚本 ${NS}`, 'utf-8') });
  await page.getByRole('button', { name: '提交脚本版本' }).click();
  await expect(page.getByText(/脚本版本 s2 已登记/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('未持久（文件来源）').first()).toBeVisible();

  // d) invalid：真实上传 utf-16-le（无 BOM）经文件层通过、解析失败 → s3 invalid 当前，不被旧 ready 顶替
  await page.locator('input[type=file]').setInputFiles({
    name: `bad-${NS}.txt`, mimeType: 'text/plain', buffer: Buffer.from('乱码字节流', 'utf16le') }); // 无 BOM，严格 utf-8 解码失败
  await page.getByRole('button', { name: /提交（复用已上传文件）|提交脚本版本/ }).click();
  await expect(page.getByText(/脚本版本 s3 已登记/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('解析：invalid')).toBeVisible(); // 当前版本 toast 明示 invalid
  // 列表最新行 s3=失败徽标可见（当前 invalid），s2 仍可作历史
  await expect(page.locator('.ant-table-tbody tr').filter({ hasText: 's3' }).first().getByText('失败')).toBeVisible({ timeout: 30_000 });

  // e) 选历史 s2 写 ?version → reload 仍同版（刷新恢复）
  await page.locator('.ant-table-tbody tr').filter({ hasText: 's2' }).first().click();
  await expect(page).toHaveURL(/version=/, { timeout: 10_000 });
  const versionUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(versionUrl);
  // 选中历史 s2 后详情对应其源映射（reload 后仍该版）
  await expect(page.getByText(/来源文件/)).toBeVisible({ timeout: 30_000 });

  // f) SPA 路由切到任务 B（点"打开任务"链接=直达 B 的 script 路由，非整页 goto）：
  //    不残旧 file DOM/旧 loading；旧 A 输入不泄漏（切到粘贴态应为空）。
  await page.getByRole('link', { name: '项目详情' }).click(); // 面包屑 SPA 导航回项目
  await expect(page).toHaveURL(/\/projects\/[^/]+$/);
  await page.getByRole('link', { name: `打开任务 达B${NS}` }).first().click(); // 直达 B script 页
  await expect(page).toHaveURL(new RegExp(`/tasks/${tB}/script`));
  // 到达 B（前一步 e) reload 后组件重挂，mode 回默认粘贴态）：旧 A 文本不得残留。
  await expect(page.getByRole('strong').filter({ hasText: `达B${NS}` })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByPlaceholder(/脚本正文/)).toHaveValue('');
  // 切上传态：旧 A 上传的 file DOM 必须已清空（切 task 的 effect 清 inputRef.value，且组件已重挂）：
  await page.getByRole('radio', { name: '上传 TXT' }).check();
  await expect(page.locator('input[type=file]')).toHaveValue('');
  // 新任务提交按钮不得被旧在途操作永久锁 loading（无 file 时禁用属正常，非"提交中"spinner）：
  await expect(page.getByRole('button', { name: '提交脚本版本' })).toBeVisible();
});

// g) 归档只读：grep 实证 foundation 无归档写端点 → 本批无法经真实 API 造归档态。
//    如实标为能力缺口/未覆盖，不直 SQL/mock/临时扩后端（A 11:32 修正3）。
test.skip('脚本页归档只读（阻塞：产品暂无归档写 API，能力缺口）', async () => {
  test.info().annotations.push({ type: 'issue',
    description: 't-31 g) 需真实归档写端点；foundation 未提供，待后端补 API 后启用本用例。' });
});
