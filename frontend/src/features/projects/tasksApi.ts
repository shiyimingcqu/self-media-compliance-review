// D t-27/t-29：项目达人任务 API 层。
// 类型全部引用 generated 既有 schema，不手写第二份。
// （BriefSummary/VersionDetail/RequirementView/TaskCreate 在 shared/api/types.ts
//  未导出且该文件禁改——直接从 generated 引用，同样不复制字段。）
import { api } from '../../shared/api/client';
import type { components } from '../../shared/api/generated';
import type {
  CreatorTask, Envelope, PageEnvelope, ProjectDetail, TaskStage,
} from '../../shared/api/types';

export type BriefSummary = components['schemas']['BriefSummary'];
export type VersionDetail = components['schemas']['VersionDetail'];
export type RequirementView = components['schemas']['RequirementView'];
export type TaskCreate = components['schemas']['TaskCreate'];
export type { CreatorTask, TaskStage, ProjectDetail };

/** URL page 参数归一化：safe 整数 ≥1；null/负数/小数/Infinity/非数字 → 1。 */
export function safePage(raw: string | null): number {
  const n = Number(raw);
  return Number.isSafeInteger(n) && n >= 1 ? n : 1;
}

/** URL stage 参数白名单：仅 TaskStage 闭集或 ''（全部）；非法值 → ''。 */
export function safeStage(raw: string | null): TaskStage | '' {
  const stages = ['requirements', 'script', 'video', 'human', 'completed'] as const;
  return (stages as readonly string[]).includes(raw ?? '') ? (raw as TaskStage) : '';
}

export const tasksApi = {
  list: (projectId: string, stage: TaskStage | '', page: number, pageSize: number,
         signal?: AbortSignal) =>
    api<PageEnvelope<CreatorTask>>(
      `/projects/${encodeURIComponent(projectId)}/tasks`
      + `?page=${safePage(String(page))}&pageSize=${pageSize}`
      + `${stage ? `&stage=${encodeURIComponent(stage)}` : ''}`, { signal }),
  detail: (taskId: string, signal?: AbortSignal) =>
    api<Envelope<CreatorTask>>(`/tasks/${encodeURIComponent(taskId)}`, { signal }),
  ruleVersionDetail: (versionId: string, signal?: AbortSignal) =>
    api<Envelope<VersionDetail>>(
      `/rule-versions/${encodeURIComponent(versionId)}`, { signal }),
  create: (projectId: string, payload: TaskCreate, key: string) =>
    api<Envelope<CreatorTask>>(
      `/projects/${encodeURIComponent(projectId)}/tasks`,
      { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload) }),
};

/** 候选规则版本：briefs[].versions 过滤 state==='enabled'（第一层；兼容性在选中后 detail 校验）。 */
export function candidateVersions(briefs: BriefSummary[] | undefined,
                                   platform: string, phase: string):
  { id: string; versionLabel: string; briefTitle: string }[] {
  if (!briefs) return [];
  return briefs.flatMap(brief =>
    brief.versions
      .filter(v => v.state === 'enabled')
      .map(v => ({ id: v.id, versionLabel: v.versionLabel, briefTitle: brief.title })));
}

export interface Compatibility {
  ok: boolean;
  matchedCount: number;
  /** 明确禁提交原因（open 冲突等）；无则 null（服务端最终为准） */
  blockedReason: string | null;
}

/** 兼容性校验（对齐后端共享谓词语义）：
 * platforms 必须明确包含任务平台（无通配）；phases 含任务阶段或 general；
 * contentTypes 含 video 或 general；仅 confirmed must 项计数。
 * open 冲突 → 禁提交。coverage 字段仅在未来接口提供明确未覆盖状态时
 * 才使用——空数组是合法（如无需覆盖签署的 TXT 规则），不得臆造缺口；
 * enabled + 实际规则适用 + 服务端校验为准。 */
export function compatibilityCheck(detail: VersionDetail, platform: string,
                                   phase: string): Compatibility {
  const openConflicts = detail.conflicts.filter(c => c.state === 'open');
  if (openConflicts.length > 0) {
    return { ok: false, matchedCount: 0, blockedReason: '规则版本存在未解决冲突' };
  }
  const matched = detail.requirements.filter(r =>
    r.importance === 'must' && r.confirmation === 'confirmed'
    && r.platforms.includes(platform)
    && (r.phases.includes(phase) || r.phases.includes('general'))
    && (r.contentTypes.includes('video') || r.contentTypes.includes('general')));
  return { ok: matched.length > 0, matchedCount: matched.length, blockedReason: null };
}

/** 提交 payload 稳定签名（仅用于内存 ref 比较，绝不进 HTTP 头）。 */
export function payloadSignature(payload: TaskCreate): string {
  return JSON.stringify([payload.creatorName.trim(), payload.platform, payload.campaignPhase,
    payload.ruleVersionId, payload.acknowledgeDuplicate]);
}

// 阶段筛选选项（TaskStage 闭集 → 中文标签；与后端枚举一一对应）
export const STAGE_OPTIONS: { value: TaskStage | ''; label: string }[] = [
  { value: '', label: '全部阶段' },
  { value: 'requirements', label: '待确认要求' },
  { value: 'script', label: '脚本审核' },
  { value: 'video', label: '视频审核' },
  { value: 'human', label: '人工确认' },
  { value: 'completed', label: '已完成' },
];

export const STAGE_LABELS: Record<string, string> = {
  requirements: '待确认要求', script: '脚本审核', video: '视频审核',
  human: '人工确认', completed: '已完成',
};

/** 按 stage 决定任务行进入的审核路由（动态段全部编码；未实现页由各模块呈现未接入态）。 */
export function stageRoute(stage: string, projectId: string, taskId: string): string {
  const pid = encodeURIComponent(projectId);
  const tid = encodeURIComponent(taskId);
  switch (stage) {
    case 'video': return `/projects/${pid}/tasks/${tid}/video`;
    case 'human':
    case 'completed': return `/projects/${pid}/tasks/${tid}/video`;  // 完成态停项目详情（无发布页）
    case 'script':
    case 'requirements':
    default: return `/projects/${pid}/tasks/${tid}/script`;  // 从脚本阶段开始
  }
}
