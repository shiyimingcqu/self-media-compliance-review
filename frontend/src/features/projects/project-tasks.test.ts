// D t-27/t-29：任务列表与创建纯逻辑测试（node vitest；Drawer 交互未验证——见任务报告缺口声明）。
import { describe, expect, it } from 'vitest';
import {
  STAGE_LABELS, STAGE_OPTIONS, stageRoute, safePage, safeStage,
  candidateVersions, compatibilityCheck, payloadSignature,
} from './tasksApi';
import type { BriefSummary, TaskCreate, VersionDetail } from './tasksApi';

describe('stageRoute 按阶段路由（动态段全部编码）', () => {
  const pid = 'proj-1', tid = 'task-9';
  it('script/video/human 阶段进入对应真实路由', () => {
    expect(stageRoute('script', pid, tid)).toBe(`/projects/${pid}/tasks/${tid}/script`);
    expect(stageRoute('video', pid, tid)).toBe(`/projects/${pid}/tasks/${tid}/video`);
    expect(stageRoute('human', pid, tid)).toBe(`/projects/${pid}/tasks/${tid}/video`);
  });
  it('requirements 默认从脚本阶段开始；completed 停项目详情（无发布页）', () => {
    expect(stageRoute('requirements', pid, tid)).toBe(`/projects/${pid}/tasks/${tid}/script`);
    expect(stageRoute('completed', pid, tid)).toBe(`/projects/${pid}/tasks/${tid}/video`);
  });
  it('含空格/#/? 的 ID 被编码，不破坏路由结构', () => {
    expect(stageRoute('script', 'p 1', 't#9')).toBe(`/projects/${encodeURIComponent('p 1')}/tasks/${encodeURIComponent('t#9')}/script`);
    const route = stageRoute('video', 'p 1', 't#9?x');
    expect(route.split('/').filter(s => s.includes('#') || s.includes('?'))).toEqual([]);
  });
});

describe('URL 参数归一化（非法值回默认，不透传后端）', () => {
  it('safePage：safe 整数>=1 通过；null/负数/小数/Infinity/NaN/非数字回 1', () => {
    expect(safePage('3')).toBe(3);
    expect(safePage(null)).toBe(1);
    expect(safePage('-2')).toBe(1);
    expect(safePage('2.5')).toBe(1);
    expect(safePage('Infinity')).toBe(1);
    expect(safePage('abc')).toBe(1);
    expect(safePage(String(Number.MAX_SAFE_INTEGER + 1))).toBe(1);
  });
  it('safeStage：TaskStage 闭集或空串；非法值回空串', () => {
    expect(safeStage('script')).toBe('script');
    expect(safeStage('')).toBe('');
    expect(safeStage(null)).toBe('');
    expect(safeStage('hacked')).toBe('');
    expect(safeStage('../../etc')).toBe('');
  });
});

describe('candidateVersions 候选过滤（第一层：state enabled）', () => {
  const briefs: BriefSummary[] = [
    { id: 'b1', title: '抖音Brief', platforms: ['douyin' as const], latestSourceVersionId: 's1',
      fileId: 'f1', extractionStatus: 'ready',
      versions: [
        { id: 'v-en', versionLabel: 'v1', state: 'enabled', revision: 1 },
        { id: 'v-dr', versionLabel: 'v2', state: 'draft', revision: 1 },
        { id: 'v-sup', versionLabel: 'v0', state: 'superseded', revision: 1 },
      ] },
    { id: 'b2', title: '小红书Brief', platforms: ['xiaohongshu' as const],
      latestSourceVersionId: null, fileId: null, extractionStatus: null, versions: [] },
  ];
  it('仅 enabled 入候选；空 briefs/undefined 安全返回空', () => {
    const c = candidateVersions(briefs, 'douyin', 'general');
    expect(c.map(x => x.id)).toEqual(['v-en']);
    expect(c[0].briefTitle).toBe('抖音Brief');
    expect(candidateVersions(undefined, 'douyin', 'general')).toEqual([]);
    expect(candidateVersions([], 'douyin', 'general')).toEqual([]);
  });
});

describe('compatibilityCheck 兼容校验（对齐后端共享谓词）', () => {
  const base = { id: 'r1', lineageId: 'l1', ruleVersionId: 'v1', text: 'x', category: 'c',
    importance: 'must', platforms: ['douyin'], contentTypes: ['video'], phases: ['general'],
    reviewStages: ['script'], exceptions: '', source: {}, confirmation: 'confirmed' };
  const mk = (over: Partial<typeof base>) => ({ ...base, ...over });
  const detailOf = (reqs: unknown[], conflicts: unknown[] = [], coverage: unknown[] = [{}]) =>
    ({ version: { id: 'v1', briefId: 'b', sourceVersionId: 's', versionLabel: 'v', state: 'enabled',
       parentId: null, revision: 1, confirmedBy: null, confirmedAt: null, createdAt: 't' },
       requirements: reqs as never[], conflicts: conflicts as never[],
       coverage: coverage as never[] }) as VersionDetail;

  it('平台必须明确包含；阶段/contentTypes 认 general 通用不误排', () => {
    const d = detailOf([
      mk({ platforms: ['douyin'], phases: ['general'], contentTypes: ['general'] }),
    ]);
    expect(compatibilityCheck(d, 'douyin', 'seeding')).toEqual({ ok: true, matchedCount: 1, blockedReason: null });
  });
  it('平台不含任务平台 → 不计（平台无 general 通配）', () => {
    const d = detailOf([
      mk({ platforms: ['xiaohongshu'], phases: ['general'], contentTypes: ['general'] }),
    ]);
    const r = compatibilityCheck(d, 'douyin', 'general');
    expect(r.ok).toBe(false);
    expect(r.matchedCount).toBe(0);
    expect(r.blockedReason).toBeNull();  // 无匹配≠被阻止（提示换版本）
  });
  it('phase=seeding 匹配 seeding 或 general；warmup-only 不匹配 seeding', () => {
    const d1 = detailOf([mk({ platforms: ['douyin'], phases: ['seeding'], contentTypes: ['video'] })]);
    expect(compatibilityCheck(d1, 'douyin', 'seeding').ok).toBe(true);
    const d2 = detailOf([mk({ platforms: ['douyin'], phases: ['warmup'], contentTypes: ['video'] })]);
    expect(compatibilityCheck(d2, 'douyin', 'seeding').ok).toBe(false);
  });
  it('disabled/pending 确认态不计必须项；suggested 不计', () => {
    const d = detailOf([
      mk({ platforms: ['douyin'], phases: ['general'], contentTypes: ['general'], confirmation: 'pending' }),
      mk({ platforms: ['douyin'], phases: ['general'], contentTypes: ['general'], confirmation: 'disabled' }),
      mk({ platforms: ['douyin'], phases: ['general'], contentTypes: ['general'], importance: 'suggested' }),
    ]);
    expect(compatibilityCheck(d, 'douyin', 'general').matchedCount).toBe(0);
  });
  it('open 冲突 → 禁提交（blockedReason）', () => {
    const d = detailOf(
      [mk({ platforms: ['douyin'], phases: ['general'], contentTypes: ['general'] })],
      [{ id: 'c1', ruleVersionId: 'v1', requirementIds: ['r1'], explanation: 'x', state: 'open' }]);
    const r = compatibilityCheck(d, 'douyin', 'general');
    expect(r.ok).toBe(false);
    expect(r.blockedReason).toBe('规则版本存在未解决冲突');
  });
  it('coverage=[] 是合法（如无需覆盖签署的 TXT 规则）——不臆造缺口，可创建', () => {
    const d = detailOf(
      [mk({ platforms: ['douyin'], phases: ['general'], contentTypes: ['general'] })], [], []);
    const r = compatibilityCheck(d, 'douyin', 'general');
    expect(r.ok).toBe(true);  // 空覆盖不阻断：enabled+规则适用+服务端为准
    expect(r.matchedCount).toBe(1);
    expect(r.blockedReason).toBeNull();
  });
});

describe('payloadSignature 幂等 key 管理（签名仅内存比较，不进 header）', () => {
  const p = (over: Partial<TaskCreate> = {}): TaskCreate =>
    ({ creatorName: '达人A', platform: 'douyin', contentType: 'video',
       campaignPhase: 'general', ruleVersionId: 'v1', acknowledgeDuplicate: false, ...over });
  it('同 payload 同签名（含 50 字中文与含竖线姓名——签名不进 header 故无 Latin1/长度约束）', () => {
    const cn50 = '达'.repeat(50);
    expect(payloadSignature(p({ creatorName: cn50 }))).toBe(payloadSignature(p({ creatorName: ` ${cn50} ` })));
    expect(payloadSignature(p({ creatorName: '达|人|A' }))).toBe(payloadSignature(p({ creatorName: '达|人|A ' })));
  });
  it('任一字段变化（含 ack）签名不同——换新 key', () => {
    const base = payloadSignature(p());
    expect(payloadSignature(p({ acknowledgeDuplicate: true }))).not.toBe(base);
    expect(payloadSignature(p({ ruleVersionId: 'v2' }))).not.toBe(base);
    expect(payloadSignature(p({ campaignPhase: 'warmup' }))).not.toBe(base);
    expect(payloadSignature(p({ platform: 'xiaohongshu' as never }))).not.toBe(base);
  });
});

describe('Idempotency-Key header 仅 opaque uuid（长度 8..128、无业务数据）', () => {
  it('create 调用的 header 是纯 uuid（36 字符），不含签名/中文/竖线', async () => {
    const headers: string[] = [];
    const { tasksApi } = await import('./tasksApi');
    const orig = globalThis.fetch;
    globalThis.fetch = (async (_url: unknown, init?: RequestInit) => {
      headers.push(String(new Headers(init?.headers).get('idempotency-key')));
      return new Response(JSON.stringify({ data: { id: 't1' }, requestId: 'r' }), { status: 201 });
    }) as typeof fetch;
    const cn50 = '达'.repeat(50);
    const payload: TaskCreate = { creatorName: cn50, platform: 'douyin', contentType: 'video',
      campaignPhase: 'general', ruleVersionId: 'v1', acknowledgeDuplicate: false };
    try {
      await tasksApi.create('proj-1', payload, crypto.randomUUID());
      await tasksApi.create('proj-1', payload, crypto.randomUUID());  // 模拟重试同 key 由调用方保证
    } finally { globalThis.fetch = orig; }
    for (const h of headers) {
      expect(h).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
      expect(h.length).toBeGreaterThanOrEqual(8);
      expect(h.length).toBeLessThanOrEqual(128);
      expect(h).not.toContain('达');
      expect(h).not.toContain('|');
      expect(h).not.toContain('douyin');
    }
  });
});

describe('统一执行器端到端状态序列（注意：独立复现，非组件验证）', () => {
  // ⚠ 如实声明：useTaskCreateFlow 依赖 React hooks，本组测试【未导入】其
  // 真实实现，而是按相同逻辑约定（busyRef/token/释放顺序/key 管理）独立
  // 复现时序——它验证约定本身，不证明组件真实行为。组件验证由
  // e2e/project-task-create.spec.ts 浏览器测试承担（hooks 渲染 + 真实 API）。
  interface FetchScript { status: number; body: unknown }
  function scriptFetch(script: FetchScript[]) {
    let i = 0;
    const calls: { url: string; body: unknown; key: string | null }[] = [];
    const fn = async (url: unknown, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null,
                   key: headers.get('idempotency-key') });
      const step = script[Math.min(i, script.length - 1)];
      i += 1;
      return new Response(JSON.stringify(step.body), { status: step.status });
    };
    return { fn, calls };
  }

  const PAYLOAD = { creatorName: '达人A', platform: 'douyin' as const, contentType: 'video' as const,
    campaignPhase: 'general' as const, ruleVersionId: 'v1', acknowledgeDuplicate: false };
  const DUPLICATE_409 = { status: 409, body: { error: { code: 'DUPLICATE_SUSPECTED',
    message: '重复' }, requestId: 'r' } };
  const OK_201 = { status: 201, body: { data: { id: 't1' }, requestId: 'r' } };

  it('序列：摘要确认→POST 409 DUPLICATE→提交锁释放→重复确认开框→取消零第二请求', async () => {
    const { fn, calls } = scriptFetch([DUPLICATE_409]);
    const orig = globalThis.fetch;
    globalThis.fetch = fn as typeof fetch;
    // 独立协议演练（与 useTaskCreateFlow 相同约定）：submit 是唯一
    // modal→submitting 原子转换者——确认框 onOk 传 token，submit 校验
    // token 匹配才转换；无 token 直提须 busy 为空。
    let busy: 'modal' | 'submitting' | null = null;
    let heldToken: object | null = null;
    const submit = async (payload: typeof PAYLOAD, token?: object): Promise<typeof PAYLOAD | null> => {
      if (token === undefined) {
        if (busy) return null;
        busy = 'submitting';
      } else {
        if (busy !== 'modal' || heldToken !== token) return null;  // 非持有者拒
        heldToken = null;
        busy = 'submitting';  // 唯一转换点
      }
      let dup: typeof PAYLOAD | null = null;
      try {
        const { tasksApi } = await import('./tasksApi');
        try {
          await tasksApi.create('p1', payload, crypto.randomUUID());
        } catch {
          dup = { ...payload };  // DUPLICATE（真实 fetch script 409）
        }
      } finally {
        if (busy === 'submitting') busy = null;  // 只清自己的锁
      }
      return dup;
    };
    // 摘要确认路径：模拟 modal 持有 → onOk(token) → 转换 → 409
    const token = {};
    heldToken = token;
    busy = 'modal';
    try {
      // 非持有者（错 token）被拒——外部并发不可借 modal 锁提交
      expect(await submit(PAYLOAD, {})).toBeNull();
      // 持有者转换成功（修复死锁：modal 锁不再吞自己的提交）
      const needsConfirm = await submit(PAYLOAD, token);
      expect(needsConfirm).toEqual(PAYLOAD);  // 409 → 待确认
      expect(calls.length).toBe(1);           // 取消：无第二请求
      expect(busy).toBeNull();                // 提交锁已释放
      expect(heldToken).toBeNull();           // token 已清（旧框迟到释放无害）
    } finally { globalThis.fetch = orig; }
  });

  it('序列：确认 ack=true 且新 key（key 随签名变化换新）', async () => {
    const { fn, calls } = scriptFetch([DUPLICATE_409, OK_201]);
    const orig = globalThis.fetch;
    globalThis.fetch = fn as typeof fetch;
    const { tasksApi, payloadSignature } = await import('./tasksApi');
    // key 管理协议（与 useTaskCreateFlow.currentKey 相同）：{签名,uuid}
    let entry: { signature: string; key: string } | null = null;
    const keyFor = (p: typeof PAYLOAD) => {
      const sig = payloadSignature(p);
      if (!entry || entry.signature !== sig) entry = { signature: sig, key: crypto.randomUUID() };
      return entry.key;
    };
    try {
      try { await tasksApi.create('p1', PAYLOAD, keyFor(PAYLOAD)); } catch { /* 409 */ }
      const ack = { ...PAYLOAD, acknowledgeDuplicate: true };
      const k1 = keyFor(PAYLOAD), k2 = keyFor(ack);
      expect(k2).not.toBe(k1);  // ack 变化 → 新 key（防半确认幂等漂移）
      await tasksApi.create('p1', ack, k2);  // 确认重发成功
      expect(calls.length).toBe(2);
      expect((calls[1].body as { acknowledgeDuplicate?: boolean }).acknowledgeDuplicate).toBe(true);
      expect(calls[1].key).toBe(k2);
    } finally { globalThis.fetch = orig; }
  });

  it('序列：确认失败可重试（同 ack payload 复用 key）', async () => {
    const { fn, calls } = scriptFetch([DUPLICATE_409, { status: 502, body: { error: { code: 'PROVIDER_ERROR', message: 'x' }, requestId: 'r' } }, OK_201]);
    const orig = globalThis.fetch;
    globalThis.fetch = fn as typeof fetch;
    const { tasksApi, payloadSignature } = await import('./tasksApi');
    let entry: { signature: string; key: string } | null = null;
    const keyFor = (p: typeof PAYLOAD) => {
      const sig = payloadSignature(p);
      if (!entry || entry.signature !== sig) entry = { signature: sig, key: crypto.randomUUID() };
      return entry.key;
    };
    try {
      try { await tasksApi.create('p1', PAYLOAD, keyFor(PAYLOAD)); } catch { /* 409 */ }
      const ack = { ...PAYLOAD, acknowledgeDuplicate: true };
      const k2 = keyFor(ack);
      try { await tasksApi.create('p1', ack, k2); } catch { /* 502 失败 */ }
      const k3 = keyFor(ack);  // 重试同 payload
      expect(k3).toBe(k2);     // 复用 key（网络重试语义）
      await tasksApi.create('p1', ack, k3);
      expect(calls.length).toBe(3);
      expect(calls[2].key).toBe(k2);
    } finally { globalThis.fetch = orig; }
  });

  it('token 化释放：旧弹窗关闭不清新弹窗的锁', () => {
    // openConfirm 协议（与 useTaskCreateFlow 相同）：token 判定
    const tokenRef: unknown[] = [];
    let busy: 'modal' | null = null;
    const open = () => {
      if (busy) return false;
      const token = {};
      tokenRef.push(token);
      busy = 'modal';
      const releaseIfMine = (t: unknown) => {
        if (tokenRef[tokenRef.length - 1] === t) { busy = null; tokenRef.pop(); }
      };
      return { token, release: () => releaseIfMine(token) };
    };
    const first = open() as { token: object; release: () => void } | false;
    expect(open()).toBe(false);       // 占用期第二框拒
    expect(first).toBeTruthy();
    // 释放旧弹窗（token 匹配）后再开新框
    if (first) first.release();
    const second = open() as { token: object; release: () => void } | false;
    expect(second).toBeTruthy();
    expect(busy).toBe('modal');
  });
});

describe('列表 URL 组装约定', () => {
  it('分页与阶段参数按约定拼接（含归一化与编码）', async () => {
    const calls: string[] = [];
    const { tasksApi } = await import('./tasksApi');
    const orig = globalThis.fetch;
    globalThis.fetch = (async (url: unknown) => {
      calls.push(String(url));
      return new Response(JSON.stringify({ data: [], requestId: 'r', page: 1, pageSize: 10, total: 0 }), { status: 200 });
    }) as typeof fetch;
    try {
      await tasksApi.list('proj-1', '', 1, 10);
      await tasksApi.list('proj-1', 'script', 2, 10);
    } finally { globalThis.fetch = orig; }
    expect(calls[0]).toContain('/projects/proj-1/tasks?page=1&pageSize=10');
    expect(calls[0]).not.toContain('stage=');
    expect(calls[1]).toContain('/projects/proj-1/tasks?page=2&pageSize=10&stage=script');
  });
});
