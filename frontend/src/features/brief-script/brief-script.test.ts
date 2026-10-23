// t-26 Brief UI 纯逻辑/状态机单测（渲染/E2E 本环境未验，如实边界）。
import { describe, expect, it } from 'vitest';
import { BriefUploadFlow, KeyedOp, OperationRunner, buildSource, classifyBriefError, coverageBodyOf, currentScriptVersion, enableBlockerReasons, enableBlockers, newKey, pageLabels, pickActiveVersion, previewGuard, scriptCharCountText, scriptContextGate, scriptCoverageView, scriptCreateBody, versionSourceOf, versionSyncKeys } from './api';

const requirement = (id: string, confirmation = 'pending') => ({
  id, lineageId: `l-${id}`, text: 't', category: '口播', importance: 'must', platforms: ['xiaohongshu'],
  contentTypes: ['video'], phases: ['seeding'], reviewStages: ['script'], exceptions: '', source: {}, confirmation,
});

describe('幂等键策略', () => {
  it('同签名重试复用 key；语义签名变化才换新 key', () => {
    let n = 0;
    const op = new KeyedOp(() => `k${++n}`);
    const k1 = op.ensure('a');
    expect(op.ensure('a')).toBe(k1); // 重试不换 key
    const k2 = op.ensure('b'); // 内容变更=新逻辑操作
    expect(k2).not.toBe(k1);
    expect(op.ensure('b')).toBe(k2);
  });
  it('newKey 每次不同且非空', () => {
    expect(newKey()).not.toBe(newKey());
  });
});

describe('来源构造互斥', () => {
  it('段落引用携带 fileId/paragraphId/quote；品牌补充只有 manualNote', () => {
    expect(buildSource({ mode: 'paragraph', fileId: 'f1', paragraphId: 'p2', quote: '原文' }))
      .toEqual({ fileId: 'f1', paragraphId: 'p2', quote: '原文' });
    expect(buildSource({ mode: 'manual', manualNote: ' 品牌补充 '})).toEqual({ manualNote: '品牌补充' });
  });
});

describe('启用预检 blockers（真值驱动，不乐观）', () => {
  const base = { version: {}, conflicts: [], coverage: [] };
  it('pending 与 open 冲突都被列出', () => {
    const blockers = enableBlockers({
      ...base, requirements: [requirement('r1'), requirement('r2', 'confirmed')],
      conflicts: [{ id: 'c1', ruleVersionId: 'v', requirementIds: ['r2'], explanation: 'e', state: 'open', resolution: null }],
    } as never, []);
    expect(blockers.pending).toEqual(['r1']);
    expect(blockers.openConflicts).toEqual(['r2']);
  });
  it('uncovered 无逐 item 链/仅签名不算解除', () => {
    const blockers = enableBlockers({
      ...base, requirements: [requirement('r1', 'confirmed')],
      coverage: [
        { uncoveredItems: ['page:20'], statement: 's', reason: 'r', itemMappings: [], created_at: '' }, // 无关联链
        { uncoveredItems: ['page:21'], statement: 's', reason: 'r', itemMappings: [{ uncoveredSourceId: 'page:21', requirementId: 'r1', note: 'n' }], created_at: '' },
      ],
    } as never, ['page:20', 'page:21', 'page:22']);
    expect(blockers.missingCoverage).toEqual(['page:20', 'page:22']);
  });
});

describe('错误分类', () => {
  it('409 保留草稿语义；501 明示未接入；0 归网络', () => {
    expect(classifyBriefError({ status: 409, message: '冲突' }).kind).toBe('conflict');
    // 带服务端 message 时原样透出；缺省时给出保留草稿的引导文案
    expect(classifyBriefError({ status: 409, message: '冲突' }).message).toBe('冲突');
    expect(classifyBriefError({ status: 409 } as never).message).toContain('草稿');
    expect(classifyBriefError({ code: 'MODULE_NOT_IMPLEMENTED' }).kind).toBe('notImplemented');
    expect(classifyBriefError({ status: 0, message: '无法连接' }).kind).toBe('network');
    expect(classifyBriefError({ status: 400, code: 'VALIDATION_ERROR', fieldErrors: { platforms: '越界' } }).fieldErrors)
      .toEqual({ platforms: '越界' });
  });
});

describe('BriefUploadFlow 两步状态机', () => {
  it('register 失败重试复用已上传 fileId 与同登记 key；不重复上传', () => {
    let n = 0;
    const flow = new BriefUploadFlow(() => `k${++n}`);
    const uploadKey = flow.uploadingKey;
    flow.onUploaded('file-1');
    const sig = 'file-1|标题|xiaohongshu';
    expect(flow.currentFileId).toBe('file-1');
    expect(flow.registerKeyFor(sig)).toBe(flow.registerKeyFor(sig)); // 同签名重试同 key
    expect(flow.uploadingKey).toBe(uploadKey); // 上传 key 不因登记失败改变
  });
  it('标题/平台变更仅换登记 key，fileId 保留；文件变更重置上传 key 与 fileId', () => {
    const flow = new BriefUploadFlow();
    flow.onUploaded('file-1');
    const k1 = flow.registerKeyFor('file-1|A|xhs');
    const k2 = flow.registerKeyFor('file-1|B|xhs'); // 语义变更→新 key
    expect(k2).not.toBe(k1);
    expect(flow.currentFileId).toBe('file-1');
    flow.onFileChange(); // 换文件
    expect(flow.currentFileId).toBeNull();
    expect(flow.uploadingKey).not.toBe(k1);
  });
  it('同步 in-flight 闸门拒绝并发第二操作；endOperation 后放行', () => {
    const flow = new BriefUploadFlow();
    expect(flow.tryBegin()).toBe(true);
    expect(flow.tryBegin()).toBe(false); // 不靠 loading 晚一拍
    flow.endOperation();
    expect(flow.tryBegin()).toBe(true);
  });
  it('整体成功后重置：下一次上传/登记为新高逻辑操作', () => {
    const flow = new BriefUploadFlow();
    flow.onUploaded('f');
    const k = flow.registerKeyFor('sig');
    flow.onSuccess();
    expect(flow.currentFileId).toBeNull();
    expect(flow.registerKeyFor('sig')).not.toBe(k);
  });
});

describe('pickActiveVersion 真值回落', () => {
  const list = [{ id: 'v1', state: 'draft' }, { id: 'v2', state: 'enabled' }];
  it('URL 优先、其次选择、非法回落首个', () => {
    expect(pickActiveVersion(list, 'v2', 'v1')).toBe('v2');
    expect(pickActiveVersion(list, null, 'v1')).toBe('v1');
    expect(pickActiveVersion(list, 'evil', 'v2')).toBe('v2'); // 不命中列表的 URL 值不生效
    expect(pickActiveVersion(list, 'evil', null)).toBe('v1');
    expect(pickActiveVersion([], 'x', 'y')).toBeNull();
  });
});

describe('versionSourceOf 只认版本自带 source（禁 latest 回退）', () => {
  const versions = [
    { id: 'v1', state: 'enabled', source: { id: 's1', fileId: 'f1', extractionStatus: 'ready' } },
    { id: 'v2', state: 'draft', source: null },
  ] as never;
  it('命中=返回该版源；异常指针=null 不借任何 latest/他 brief', () => {
    expect(versionSourceOf(versions, 'v1')).toEqual({ sourceVersionId: 's1', fileId: 'f1' });
    expect(versionSourceOf(versions, 'v2')).toBeNull();
    expect(versionSourceOf(versions, 'missing')).toBeNull();
    expect(versionSourceOf(versions, null)).toBeNull();
  });
  it('previewGuard：返回 ID 与请求完全一致才注入，错绑拒绝', () => {
    const req = { sourceVersionId: 's1', fileId: 'f1' };
    expect(previewGuard(req, { sourceVersionId: 's1', fileId: 'f1' })).toEqual({ ok: true });
    const bad = previewGuard(req, { sourceVersionId: 's9', fileId: 'f1' });
    expect(bad.ok).toBe(false);
  });
  it('偏移回验：块偏移相对后端解析全文（完整原文 fixture，不拿分块 text 当全文）', () => {
    const fullText = '第一段全文\n\n第二段全文尾';
    const blocks = [
      { paragraphId: 'p1', ordinal: 0, startOffset: 0, endOffset: 5, text: '第一段全文' },
      { paragraphId: 'p2', ordinal: 1, startOffset: 7, endOffset: 14, text: '第二段全文尾' },
    ];
    for (const b of blocks) expect(fullText.slice(b.startOffset, b.endOffset)).toBe(b.text);
  });
});

describe('t-31 脚本页纯函数', () => {
  const s = (id: string, isCurrent = false, inputType = 'text', charCount: number | null = inputType === 'text' ? 12 : null) =>
    ({ scriptVersionId: id, ordinal: 1, versionLabel: 's1', inputType, fileId: null,
       extractionStatus: 'ready', charCount, createdAt: '', isCurrent });
  it('当前版本=isCurrent 项（后端 ordinal max），不硬编首项；空列表=null 空态', () => {
    expect(currentScriptVersion([s('a'), s('b', true), s('c')])?.scriptVersionId).toBe('b');
    expect(currentScriptVersion([s('a')])).toBeNull(); // 无 isCurrent 行=不硬编首项
    expect(currentScriptVersion([])).toBeNull();
    expect(currentScriptVersion(null)).toBeNull();
  });
  it('文件输入 charCount=None 如实文案，不伪 0；粘贴显示真实值', () => {
    expect(scriptCharCountText(s('f', true, 'file'))).toContain('未持久');
    expect(scriptCharCountText({ inputType: 'file', charCount: null })).toContain('未持久');
    expect(scriptCharCountText(s('t', true, 'text', 42))).toBe('字符数：42');
  });
  it('t-38 覆盖度三态：full/partial/unknown，缺元数据绝不等于 full', () => {
    expect(scriptCoverageView({ coverage: 'full', warnings: [], uncoveredMedia: [], partial: false }).level).toBe('full');
    const p = scriptCoverageView({ coverage: 'partial', warnings: ['含 1 处图片'],
      uncoveredMedia: ['media:xl/media/image1.png（引用锚点 ID_X）'], partial: true });
    expect(p.level).toBe('partial');
    expect(p.mediaCount).toBe(1);
    expect(p.locations[0]).toContain('image1.png');
    expect(p.warnings[0]).toContain('图片');
    // 历史空 meta → unknown（不补 full）
    expect(scriptCoverageView({}).level).toBe('unknown');
    expect(scriptCoverageView(null).level).toBe('unknown');
    expect(scriptCoverageView(undefined).level).toBe('unknown');
    // 未声明 coverage 但有图片 → 推导 partial，仍不冒出 full
    expect(scriptCoverageView({ uncoveredMedia: ['m:1'] } as never).level).toBe('partial');
  });
  it('提交 key：同 payload 失败重试复用，成功 reset 后新操作（脚本域同 Brief 约束）', () => {
    let n = 0;
    const op = new KeyedOp(() => `k${++n}`);
    const sig = 'task1:file:f9';
    expect(op.ensure(sig)).toBe(op.ensure(sig));
    op.reset();
    expect(op.ensure(sig)).not.toBe('k1');
  });
  it('上下文门控：task 未取回/项目未就绪/归属不符一律 false（pending 不提前拉脚本）', () => {
    const task = { projectId: 'p1' };
    expect(scriptContextGate(task, 'p1', false, false)).toBe(false); // 双 pending
    expect(scriptContextGate(task, 'p1', true, false)).toBe(false); // 项目上下文未有效
    expect(scriptContextGate(task, 'p1', true, true)).toBe(true);
    expect(scriptContextGate(task, 'p2', true, true)).toBe(false); // 错项目
    expect(scriptContextGate(null, 'p1', true, true)).toBe(false);
  });
  it('ScriptCreate 合法互斥：file 显式 text=null，text 显式 fileId=null，不同时带值', () => {
    expect(scriptCreateBody({ inputType: 'file', fileId: 'f9' }))
      .toEqual({ inputType: 'file', fileId: 'f9', text: null });
    expect(scriptCreateBody({ inputType: 'text', text: '正文' }))
      .toEqual({ inputType: 'text', fileId: null, text: '正文' });
  });
  it('历史 ?version 语义：命中当前任务真实列表才生效，非法回落 isCurrent（不清 URL 参数）', () => {
    const list = [s('a'), s('b', true)];
    // 与 ScriptPage 一致：selectedVersion=列表命中项，否则 currentScriptVersion
    const hit = list.find((v) => v.scriptVersionId === 'a') ?? null;
    expect(hit?.scriptVersionId).toBe('a'); // 刷新恢复不丢
    const miss = list.find((v) => v.scriptVersionId === 'stale') ?? null;
    expect(miss).toBeNull();
    expect(currentScriptVersion(list)?.scriptVersionId).toBe('b'); // 回落当前而非清 URL
  });
});

describe('t-30 修订回归（A 10:42）', () => {
  it('草稿创建 key：网络失败重试复用同 key，成功 reset 后为新高逻辑操作', () => {
    let n = 0;
    const op = new KeyedOp(() => `k${++n}`);
    const sig = 'brief-1:source-1';
    const k1 = op.ensure(sig);
    expect(op.ensure(sig)).toBe(k1); // 失败后重试=同 key（不每次 newKey 回归）
    op.reset();
    expect(op.ensure(sig)).not.toBe(k1); // 成功清：下一次为新操作
  });
  it('versionSyncKeys：新建/派生后必须同时失效 project 投影与 versions 列表', () => {
    const keys = versionSyncKeys('p1', 'b1');
    expect(keys.project).toEqual(['project', 'p1']);
    expect(keys.versions).toEqual(['brief-rule-versions', 'b1']);
  });
});

// 事件序列级：OperationRunner 是弹窗 onOk 契约的执行内核（modal.confirm 打开不调 run；
// onOk 成功=run 后 await refresh；onOk 失败=rejected 保弹窗、不 refresh、同签名重试复用 key）。
describe('t-41 未覆盖页补录签署', () => {
  const base = { version: {}, conflicts: [], coverage: [] };

  it('页号标签：page:N → 第 N 页；非标准项原样（不猜格式）', () => {
    expect(pageLabels(['page:3', 'page:20', 'weird:x'])).toEqual(['第 3 页', '第 20 页', 'weird:x']);
  });

  it('enableBlockerReasons 分类展示：规则门禁与未覆盖页未签署分开列，不合成长文案', () => {
    const detail = {
      ...base,
      requirements: [requirement('r1'), requirement('r2', 'confirmed')],
      conflicts: [{ id: 'c1', ruleVersionId: 'v', requirementIds: ['r2'], explanation: 'e', state: 'open', resolution: null }],
      coverage: [],
    };
    const reasons = enableBlockerReasons(detail as never, ['page:7', 'page:12']);
    expect(reasons).toHaveLength(3);
    expect(reasons[0]).toContain('待确认要求 1 条');
    expect(reasons[1]).toContain('未解决冲突 1 项');
    expect(reasons[2]).toContain('未覆盖页未签署 2 页');
    expect(reasons[2]).toContain('第 7 页');
    expect(reasons[2]).toContain('第 12 页');
  });

  it('无阻塞时返回空数组（仍由服务端最终把关，不前端乐观放行）', () => {
    const detail = {
      ...base, requirements: [requirement('r1', 'confirmed')], conflicts: [],
      coverage: [{ uncoveredItems: ['page:1'], statement: 's', reason: 'r',
        itemMappings: [{ uncoveredSourceId: 'page:1', requirementId: 'r1', note: 'n' }], createdAt: '' }],
    };
    expect(enableBlockerReasons(detail as never, ['page:1'])).toEqual([]);
  });

  it('coverageBodyOf：顶层 statement/reason 必填且 trim；note 为每页说明 trim；一一对应', () => {
    const body = coverageBodyOf(
      [{ uncoveredSourceId: 'page:3', statement: '  第3页是图片，已人工核对  ', requirementId: 'r1' },
       { uncoveredSourceId: 'page:7', statement: '第7页产品图', requirementId: 'r2' }],
      { statement: '  已核对第3、7页  ', reason: '  PDF含图片页，文本层未覆盖  ' });
    expect(body.statement).toBe('已核对第3、7页');
    expect(body.reason).toBe('PDF含图片页，文本层未覆盖');
    expect(body.uncoveredSourceIds).toEqual(['page:3', 'page:7']);
    expect(body.itemMappings.map((m) => m.uncoveredSourceId)).toEqual(['page:3', 'page:7']);
    expect(body.itemMappings.map((m) => m.note)).toEqual(['第3页是图片，已人工核对', '第7页产品图']);
    expect(body.itemMappings.every((m) => m.note.length >= 1 && m.note.length <= 500)).toBe(true);
  });
});

describe('OperationRunner 真实事件序列', () => {
  it('成功：提交后刷新真值并 reset key；下一次同签名为新操作', async () => {
    let n = 0;
    const runner = new OperationRunner(() => `k${++n}`);
    const refreshes: number[] = [];
    const first = await runner.run('enable', async (key) => key,
      { signature: 'sig', onRefresh: async () => { refreshes.push(1); }, onError: () => {} });
    expect(refreshes).toEqual([1]);
    const second = await runner.run('enable', async (key) => key,
      { signature: 'sig', onRefresh: async () => {}, onError: () => {} });
    expect(second).not.toBe(first); // 成功即新逻辑操作
  });
  it('409 失败：不刷新成功路径、保留 key 供同 payload 重试；重试复用同 key', async () => {
    let n = 0;
    const runner = new OperationRunner(() => `k${++n}`);
    let refreshed = false;
    const seen: string[] = [];
    const fail = await runner.run('patch', async (key) => {
      seen.push(key);
      throw Object.assign(new Error('冲突'), { status: 409, code: 'REVISION_CONFLICT' });
    }, { signature: 'p:sig', onRefresh: async () => { refreshed = true; }, onError: (kind) => { expect(kind).toBe('conflict'); } });
    expect(fail).toBeNull();
    expect(refreshed).toBe(false); // 成功刷新不触发（真值重拉由调用方另路，不算操作成功）
    const retry = await runner.run('patch', async (key) => { seen.push(key); return key; },
      { signature: 'p:sig', onRefresh: async () => { refreshed = true; }, onError: () => {} });
    expect(seen[0]).toBe(seen[1]); // 失败后同签名重试=复用 key
    expect(retry).toBe(seen[1]);
    expect(refreshed).toBe(true); // 重试成功才刷新
  });
  it('inflight 闸门：同名在途第二请求直接返回 null 不触发 action', async () => {
    const runner = new OperationRunner();
    let calls = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const p1 = runner.run('add', async () => { calls += 1; await gate; return 'a'; },
      { signature: 's', onRefresh: async () => {}, onError: () => {} });
    const p2 = runner.run('add', async () => { calls += 1; return 'b'; },
      { signature: 's', onRefresh: async () => {}, onError: () => {} });
    expect(await p2).toBeNull();
    release();
    expect(await p1).toBe('a');
    expect(calls).toBe(1);
  });
});
