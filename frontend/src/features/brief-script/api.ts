// 模块私有 API 接线：类型一律引用 shared/api/generated（components['schemas']），不手写实体。
import { api } from '../../shared/api/client';
import type { components } from '../../shared/api/generated';
import type { Envelope } from '../../shared/api/types';

type S = components['schemas'];
export type VersionView = S['VersionView'];
export type RequirementView = S['RequirementView'];
export type VersionDetail = S['VersionDetail'];
export type ConfirmResult = S['ConfirmResult'];
export type ConflictView = S['ConflictView'];
export type CoverageView = S['CoverageView'];
export type ResolveResult = S['ResolveResult'];
export type BriefRegistration = S['BriefRegistrationResult'];
export type TextBlock = S['TextBlockView'];
export type ItemMapping = S['ItemMapping'];
export type RuleVersionSummary = S['RuleVersionSummary'];
export type PreviewResult = S['PreviewResult'];
export type PreviewBlock = S['PreviewBlock'];
export type CreatorTask = S['CreatorTask'];
export type ScriptCreate = S['ScriptCreate'];
export type ScriptVersionSummary = S['ScriptVersionSummary'];
export type ScriptVersionDetail = S['ScriptVersionDetail'];
export type ScriptVersionCreated = S['ScriptVersionCreated'];
export type ParseMeta = S['ParseMeta'];

const H = (key: string) => ({ 'Idempotency-Key': key });
const M = (revision: number) => ({ 'If-Match': String(revision) });
const both = (key: string, revision: number) => ({ ...H(key), ...M(revision) });
const enc = encodeURIComponent;

export const briefApi = {
  register: (projectId: string, body: { fileId: string; title: string; platforms: string[] }, key: string) =>
    api<Envelope<BriefRegistration>>(`/projects/${enc(projectId)}/briefs`, { method: 'POST', headers: H(key), body: JSON.stringify(body) }),
  listVersions: (briefId: string) =>
    api<Envelope<VersionView[]>>(`/briefs/${enc(briefId)}/rule-versions`),
  createVersion: (briefId: string, body: { sourceVersionId: string; parentId?: string | null }, key: string) =>
    api<Envelope<VersionView>>(`/briefs/${enc(briefId)}/rule-versions`, { method: 'POST', headers: H(key), body: JSON.stringify(body) }),
};

export const fileApi = {
  /** t-30 只读持久来源投影：key 含 fileId+sourceVersionId 两者（调用方 queryKey 负责）。 */
  preview: (fileId: string, sourceVersionId: string, signal?: AbortSignal) =>
    api<Envelope<PreviewResult>>(`/files/${enc(fileId)}/preview?sourceVersionId=${enc(sourceVersionId)}`, { signal }),
};

// t-31 脚本页真实接线：任务读取 + 脚本版本登记/列表/详情（后端已收口域，不新增语义）。
export const taskApi = {
  get: (taskId: string, signal?: AbortSignal) =>
    api<Envelope<CreatorTask>>(`/tasks/${enc(taskId)}`, { signal }),
};

export const scriptApi = {
  submitAndReview: (taskId: string, body: S['AutomaticScriptInput'], key: string) => api<Envelope<S['AutomaticScriptResult']>>(`/tasks/${enc(taskId)}/scripts/submit-and-review`, { method: 'POST', headers: H(key), body: JSON.stringify(body) }),
  create: (taskId: string, body: ScriptCreate, key: string) =>
    api<Envelope<ScriptVersionCreated>>(`/tasks/${enc(taskId)}/scripts`, { method: 'POST', headers: H(key), body: JSON.stringify(body) }),
  list: (taskId: string, signal?: AbortSignal) =>
    api<Envelope<ScriptVersionSummary[]>>(`/tasks/${enc(taskId)}/scripts`, { signal }),
  detail: (versionId: string, signal?: AbortSignal) =>
    api<Envelope<ScriptVersionDetail>>(`/script-versions/${enc(versionId)}`, { signal }),
};

/** 当前版本判定=列表 isCurrent（后端按 ordinal max 计算），不硬编；空列表=null 空态而非 loading。 */
export function currentScriptVersion(versions: ScriptVersionSummary[] | null | undefined): ScriptVersionSummary | null {
  return (versions ?? []).find((v) => v.isCurrent) ?? null;
}

/** 文件输入 charCount=None 如实映射（后端不持久原文，不伪 0）；粘贴有值。 */
export function scriptCharCountText(summary: Pick<ScriptVersionSummary, 'inputType' | 'charCount'>): string {
  return summary.inputType === 'file'
    ? '字符数：未持久（文件来源）'
    : `字符数：${summary.charCount ?? '未知'}`;
}

/** t-38 覆盖度展示语义（纯函数）：partial=有图片等未纳入；unknown=无元数据（历史行）。
 * **unknown 绝不等于 full**——不得把历史版本当作"文本已完整审核"。 */
export function scriptCoverageView(parseMeta: Partial<ParseMeta> | null | undefined): {
  level: 'full' | 'partial' | 'unknown';
  mediaCount: number;
  locations: string[];
  warnings: string[];
} {
  const meta: Partial<ParseMeta> = parseMeta ?? {};
  const media = meta.uncoveredMedia ?? [];
  const declared = meta.coverage;
  const level: 'full' | 'partial' | 'unknown' =
    declared === 'full' || declared === 'partial'
      ? declared
      : (meta.partial || media.length > 0 ? 'partial' : 'unknown');
  return { level, mediaCount: media.length, locations: media, warnings: meta.warnings ?? [] };
}

/** 门控纯函数（A 11:16 修订2）：脚本列表/详情仅在任务已成功、归属匹配、项目上下文有效后才请求；
 * task 尚 pending（mismatch 未知=false）不得提前加载错误项目下的脚本。 */
export function scriptContextGate(task: { projectId: string } | null | undefined, routeProjectId: string,
  taskFetched: boolean, projectFetched: boolean): boolean {
  return taskFetched && projectFetched && !!task && task.projectId === routeProjectId;
}

/** 生成 ScriptCreate 合法互斥 payload（修订4，去 as never）：file 输入 text 显式 null，
 * text 输入 fileId 显式 null，不假同时字段。 */
export function scriptCreateBody(input: { inputType: 'file'; fileId: string } | { inputType: 'text'; text: string }): ScriptCreate {
  return input.inputType === 'file'
    ? { inputType: 'file', fileId: input.fileId, text: null }
    : { inputType: 'text', fileId: null, text: input.text };
}

export const ruleApi = {
  detail: (versionId: string) => api<Envelope<VersionDetail>>(`/rule-versions/${enc(versionId)}`),
  add: (versionId: string, body: unknown, key: string, revision: number) =>
    api<Envelope<RequirementView>>(`/rule-versions/${enc(versionId)}/requirements`, { method: 'POST', headers: both(key, revision), body: JSON.stringify(body) }),
  patch: (requirementId: string, body: unknown, key: string, revision: number) =>
    api<Envelope<RequirementView>>(`/requirements/${enc(requirementId)}`, { method: 'PATCH', headers: both(key, revision), body: JSON.stringify(body) }),
  confirm: (versionId: string, requirementIds: string[], key: string, revision: number) =>
    api<Envelope<ConfirmResult>>(`/rule-versions/${enc(versionId)}/confirm`, { method: 'POST', headers: both(key, revision), body: JSON.stringify({ requirementIds }) }),
  disable: (versionId: string, requirementId: string, reason: string, key: string, revision: number) =>
    api<Envelope<S['DisableResult']>>(`/rule-versions/${enc(versionId)}/disable`, { method: 'POST', headers: both(key, revision), body: JSON.stringify({ requirementId, reason }) }),
  addConflict: (versionId: string, body: { requirementIds: string[]; explanation: string }, key: string, revision: number) =>
    api<Envelope<ConflictView>>(`/rule-versions/${enc(versionId)}/conflicts`, { method: 'POST', headers: both(key, revision), body: JSON.stringify(body) }),
  resolveConflict: (conflictId: string, body: unknown, key: string, revision: number) =>
    api<Envelope<ResolveResult>>(`/conflicts/${enc(conflictId)}/resolve`, { method: 'POST', headers: both(key, revision), body: JSON.stringify(body) }),
  coverage: (versionId: string, body: unknown, key: string, revision: number) =>
    api<Envelope<CoverageView>>(`/rule-versions/${enc(versionId)}/coverage-confirmation`, { method: 'POST', headers: both(key, revision), body: JSON.stringify(body) }),
  enable: (versionId: string, changeSummary: string, key: string, revision: number) =>
    api<Envelope<VersionView>>(`/rule-versions/${enc(versionId)}/enable`, { method: 'POST', headers: both(key, revision), body: JSON.stringify({ changeSummary }) }),
};

export function newKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** 稳定 key 策略：同一逻辑操作的重试复用同 key；签名变化=新逻辑操作才换 key。 */
export class KeyedOp {
  private key: string | null = null;
  private signature: string | null = null;

  constructor(private gen: () => string = newKey) {}

  ensure(signature: string): string {
    if (this.key !== null && this.signature !== signature) this.key = null; // 语义变更→新 key
    this.signature = signature;
    this.key ??= this.gen();
    return this.key;
  }

  reset(): void {
    this.key = null;
    this.signature = null;
  }
}

/** 写操作统一执行器（可测事件序列）：inflight 同步闸门防连点；
 * 成功→reset 该 key（下次为新高逻辑操作）并 await 真值刷新；
 * 失败→不 reset key（同 payload 重试复用同 key）、调用方保留草稿/弹窗，仅按需重拉真值；
 * 弹窗"打开后取消"不会调用 run——不触发任何 invalidate。 */
export class OperationRunner {
  private inflight: Record<string, boolean> = {};
  private ops: Record<string, KeyedOp> = {};

  constructor(private gen: () => string = newKey) {}

  isBusy(name: string): boolean { return this.inflight[name] === true; }
  anyBusy(): boolean { return Object.keys(this.inflight).length > 0; }

  async run<T>(name: string, action: (key: string) => Promise<T>, opts: {
    signature: string;
    onRefresh: () => Promise<unknown>;
    onError: (kind: 'field' | 'conflict' | 'notImplemented' | 'notFound' | 'network' | 'unknown', message: string) => void;
  }): Promise<T | null> {
    if (this.inflight[name]) return null; // 同步闸门：在途不重复发起
    this.inflight[name] = true;
    try {
      const op = (this.ops[name] ??= new KeyedOp(this.gen));
      const result = await action(op.ensure(opts.signature));
      op.reset(); // 成功：下一次同签名字符串也是新逻辑操作
      await opts.onRefresh();
      return result;
    } catch (e) {
      const c = classifyBriefError(e);
      opts.onError(c.kind, c.message); // 输入/弹窗由调用方保持；key 不 reset → 同 payload 重试复用
      return null;
    } finally {
      delete this.inflight[name];
    }
  }
}

/** 上传→登记两步操作的可测状态机（组件用 useRef 持有，避免 render 重建丢 key）：
 * 文件变化才重置上传 key 并清空已传 fileId；标题/平台变化只换登记 key 且复用 fileId；
 * 同步 in-flight 闸门防并发双击重复上传；整体成功后重置为下一逻辑操作。 */
export class BriefUploadFlow {
  private uploadKey: string;
  private fileId: string | null = null;
  private registerKey: string | null = null;
  private registerSignature: string | null = null;
  private inflight = false;

  constructor(private gen: () => string = newKey) {
    this.uploadKey = gen();
  }

  get currentFileId(): string | null { return this.fileId; }
  get uploadingKey(): string { return this.uploadKey; }

  onFileChange(): void {
    this.uploadKey = this.gen();
    this.fileId = null;
    this.resetRegister();
  }

  onUploaded(fileId: string): void { this.fileId = fileId; }

  registerKeyFor(signature: string): string {
    if (this.registerKey !== null && this.registerSignature !== signature) this.registerKey = null;
    this.registerSignature = signature;
    this.registerKey ??= this.gen();
    return this.registerKey;
  }

  resetRegister(): void { this.registerKey = null; this.registerSignature = null; }

  tryBegin(): boolean {
    if (this.inflight) return false;
    this.inflight = true;
    return true;
  }

  endOperation(): void { this.inflight = false; }

  onSuccess(): void {
    this.uploadKey = this.gen();
    this.fileId = null;
    this.registerKey = null;
    this.registerSignature = null;
    this.inflight = false;
  }
}

export interface VersionOption { id: string; state: string }

/** 本次登记的 blocks 临时上下文（按真实 briefId 严格缓存；刷新即缺，SourcePreview 显式声明）。 */
export interface BriefBlocksCache {
  blocks: TextBlock[] | null; sourceVersionId: string; fileId: string; platforms: string[];
}

/** active 版本选择：URL ?version 优先，其次界面选择；均须命中当前 Brief 的真实列表，否则回落首个。 */
export function pickActiveVersion(versions: VersionOption[], ...candidates: (string | null | undefined)[]): string | null {
  const ids = new Set(versions.map((v) => v.id));
  for (const c of candidates) if (c && ids.has(c)) return c;
  return versions[0]?.id ?? null;
}

export interface SourceContext { sourceVersionId: string; fileId: string }

/** 新建/派生版本后必须同步的两把真值缓存（t-30 修订2，A 10:42）：
 * project 投影带 versions[].source——只 refetch versions 列表会让新版本无 source，
 * 原文 preview 假性不可用直到整页 reload。调用方写成功后两把都 invalidate。 */
export function versionSyncKeys(projectId: string, briefId: string) {
  return { project: ['project', projectId], versions: ['brief-rule-versions', briefId] } as const;
}

/** 文档引用/预览来源唯一事实=所选版本投影自带 source（t-30，A 10:30 口径）。
 * 禁止 latest 回退与跨 Brief 冒用；异常指针（source=null/缺字段）→ null 并显式提示。 */
export function versionSourceOf(versions: RuleVersionSummary[] | undefined, versionId: string | null): SourceContext | null {
  if (!versionId) return null;
  const v = (versions ?? []).find((x) => x.id === versionId);
  const s = v?.source;
  if (!s || !s.id || !s.fileId) return null;
  return { sourceVersionId: s.id, fileId: s.fileId };
}

/** 预览就绪判定：仅当返回的 sourceVersionId/fileId 与请求完全一致才注入引用（防错绑）。
 * 409 文案用真实 extractionStatus；无状态则"未就绪"，不猜 invalid（client ApiError 暂不携 details
 * ——该最小缺口如实展示为"服务端未回传状态"，不在 UI 造状态）。 */
export function previewGuard(requested: SourceContext, got: { sourceVersionId: string; fileId: string }):
  { ok: true } | { ok: false; reason: string } {
  if (got.sourceVersionId !== requested.sourceVersionId || got.fileId !== requested.fileId) {
    return { ok: false, reason: '预览响应与所选版本源不匹配，已拒绝注入（不冒用其他来源内容）' };
  }
  return { ok: true };
}

export interface EnableBlocker {
  pending: string[];
  openConflicts: string[];
  missingCoverage: string[];
}

/** enable 预检纯函数：返回未齐门禁清单（不乐观放行；空数组=可尝试提交）。 */
export function enableBlockers(detail: VersionDetail, uncovered: string[]): EnableBlocker {
  const reqs = detail.requirements ?? [];
  const pending = reqs.filter((r) => r.confirmation === 'pending').map((r) => r.id);
  const openConflicts = (detail.conflicts ?? []).filter((c) => c.state === 'open')
    .flatMap((c) => c.requirementIds ?? [c.id]);
  const signed = new Set((detail.coverage ?? []).flatMap((c) => c.uncoveredItems ?? []));
  const linkedConfirmed = new Set(
    (detail.coverage ?? [])
      .filter((c) => (c.itemMappings ?? []).length > 0)
      .flatMap((c) => c.uncoveredItems ?? []),
  );
  const missingCoverage = (uncovered ?? []).filter((u) => !signed.has(u) || !linkedConfirmed.has(u));
  return { pending, openConflicts, missingCoverage };
}

/** 启用门禁理由合并展示（t-41，A 17:51③）：区分「既有规则门禁」与「未覆盖页未签署」两类，
 * 不合并成一条含糊文案；返回空数组=无阻塞（仍由服务端最终把关）。 */
export function enableBlockerReasons(detail: VersionDetail, uncovered: string[]): string[] {
  const b = enableBlockers(detail, uncovered);
  const reasons: string[] = [];
  if (b.pending.length > 0) reasons.push(`待确认要求 ${b.pending.length} 条`);
  if (b.openConflicts.length > 0) reasons.push(`未解决冲突 ${b.openConflicts.length} 项`);
  if (b.missingCoverage.length > 0) {
    reasons.push(`未覆盖页未签署 ${b.missingCoverage.length} 页（${pageLabels(b.missingCoverage).join('、')}）`);
  }
  return reasons;
}

/** 'page:20' → '第 20 页'；非标准项原样（不猜格式）。 */
export function pageLabels(ids: string[]): string[] {
  return ids.map((u) => { const m = /^page:(\d+)$/.exec(u); return m ? `第 ${m[1]} 页` : u; });
}

/** coverage-confirmation 请求体组装（t-41，A 17:51①②）：
 * - 顶层 statement/reason 必填（trim 后 1–500 字），来自用户填写，不静默写死；
 * - itemMappings[].note 为该页说明（trim 后 1–500 字，空白不算已签）；
 * - uncoveredSourceIds 与 itemMappings 的 uncoveredSourceId 严格一一对应。 */
export interface CoverageSignItemInput {
  uncoveredSourceId: string;
  statement: string;
  requirementId: string;
}

export function coverageBodyOf(items: CoverageSignItemInput[],
                               header: { statement: string; reason: string }): {
  uncoveredSourceIds: string[];
  statement: string;
  reason: string;
  itemMappings: { uncoveredSourceId: string; requirementId: string; note: string }[];
} {
  return {
    uncoveredSourceIds: items.map((i) => i.uncoveredSourceId),
    statement: header.statement.trim(),
    reason: header.reason.trim(),
    itemMappings: items.map((i) => ({
      uncoveredSourceId: i.uncoveredSourceId,
      requirementId: i.requirementId,
      note: i.statement.trim(),
    })),
  };
}

/** 来源构造：段落引用 与 品牌补充互斥（与后端 validate_source 语义对齐）。 */
export function buildSource(input: { mode: 'paragraph' | 'manual'; fileId?: string; paragraphId?: string; quote?: string; manualNote?: string }): Record<string, string> {
  if (input.mode === 'manual') return { manualNote: (input.manualNote ?? '').trim() };
  return { fileId: input.fileId ?? '', paragraphId: input.paragraphId ?? '', quote: input.quote ?? '' };
}

export function classifyBriefError(error: unknown): { kind: 'field' | 'conflict' | 'notImplemented' | 'notFound' | 'network' | 'unknown'; message: string; fieldErrors?: Record<string, string> } {
  const e = error as { status?: number; code?: string; message?: string; fieldErrors?: Record<string, string> };
  if (e?.status === 0) return { kind: 'network', message: e.message || '无法连接后端' };
  if (e?.code === 'VALIDATION_ERROR') return { kind: 'field', message: e.message || '请检查输入', fieldErrors: e.fieldErrors };
  if (e?.status === 409) return { kind: 'conflict', message: e.message || '状态冲突，已保留你的草稿，请刷新后确认再提交' };
  if (e?.code === 'MODULE_NOT_IMPLEMENTED') return { kind: 'notImplemented', message: e.message || '该能力尚未接入' };
  if (e?.status === 404) return { kind: 'notFound', message: e.message || '资源不存在' };
  return { kind: 'unknown', message: e?.message || '请求失败' };
}
