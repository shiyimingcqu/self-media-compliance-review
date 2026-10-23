import { api } from '../../shared/api/client';
import type { components } from '../../shared/api/generated';
export type GenerationStrictness = 'lenient' | 'standard' | 'strict';
export const generationStrictnessOptions = [{ value: 'lenient', label: '宽松 · 没有大问题即可' }, { value: 'standard', label: '标准 · 常规商业验收' }, { value: 'strict', label: '严格 · 细化执行要求' }];
export const generationStrictnessLabels: Record<GenerationStrictness, string> = { lenient: '宽松', standard: '标准', strict: '严格' };
export const generationStrictnessHints: Record<GenerationStrictness, string> = { lenient: '只拦实质大问题；措辞、剪辑、表演等轻微差异只作建议，不影响通过。', standard: '覆盖主要交付要求，区分必须满足与优化建议。', strict: '细化已明确的执行要求和证据口径，无硬性依据的细节仍只作建议。' };
export type StandardDraft = components['schemas']['StandardRule'];
export type StandardTemplate = { id: string; name: string; scriptRules: StandardDraft[]; videoRules: StandardDraft[] };
export type PromptTemplate = { key: string; title: string; purpose: string; version: string; content: string };
export type SelfContainmentWarning = { ruleNumber: number; text: string; reason: string };
export type ProjectStandards = { projectId: string; revision: number; ready: boolean; excludedVideoRuleTexts?: string[]; image: { id: string; rules: StandardDraft[] } | null; script: { id: string; rules: StandardDraft[]; source_version_id?: string | null } | null; video: { id: string; rules: StandardDraft[] } | null; standardWarnings?: Record<string, SelfContainmentWarning[]>; legacyCandidates: { taskId: string; creatorName: string; scriptRules: StandardDraft[]; videoRules: StandardDraft[]; sourceVersionId?: string | null }[]; migrationMessage?: string | null };
export type Recommendation = { createdAt?: string; updatedAt?: string; id: string; scope: 'script' | 'video' | 'image'; status: string; errorMessage?: string; snapshot: { sourceVersionId: string; prompt: PromptTemplate; excludedExistingVideoRuleTexts?: string[]; strictness?: GenerationStrictness; videoRuleMode?: 'legacy' | 'layered'; inputMode?: 'facts' | 'blocks'; factsPendingCount?: number }; result?: { report?: { rules: StandardDraft[]; rulesText?: string; warnings: string[] }; providerRequestIds?: string[]; usage?: unknown[]; modelResponse?: string } };
const post = <T,>(path: string, body: unknown, key: string) => api<{ data: T }>(path, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) });
export const standardsApi = {
  projectStandards: (projectId: string) => api<{ data: ProjectStandards }>(`/projects/${projectId}/standards`),
  confirmProject: (projectId: string, body: components['schemas']['ProjectStandardsInput'], key: string) => post<{ projectId: string; revision: number }>(`/projects/${projectId}/standards/confirm`, body, key),
  addCreator: (projectId: string, body: components['schemas']['ProjectCreatorInput'], key: string) => post<{ id: string; projectId: string }>(`/projects/${projectId}/creators`, body, key),
  videoDefaults: () => api<{ data: { rules: StandardDraft[] } }>('/workflow/video-rule-defaults'),
  scriptDefaults: () => api<{ data: { systemPrompt: PromptTemplate; rulesText: string } }>('/workflow/script-rules/defaults'),
  templates: () => api<{ data: StandardTemplate[] }>('/workflow/standard-templates'),
  prompts: () => api<{ data: PromptTemplate[] }>('/workflow/prompts'),
  publish: (projectId: string, body: components['schemas']['PublishTaskInput'], key: string) => post<{ id: string; projectId: string }>(`/projects/${projectId}/publish-task`, body, key),
  confirm: (taskId: string, body: components['schemas']['ReconfirmInput'], key: string) => post<{ id: string }>(`/tasks/${taskId}/standards/confirm`, body, key),
  recommend: (projectId: string, body: components['schemas']['RecommendationInput'], key: string) => post<Recommendation>(`/projects/${projectId}/standard-recommendations`, body, key),
  history: (projectId: string) => api<{ data: Recommendation[] }>(`/projects/${projectId}/standard-recommendations`),
  revalidate: (projectId: string, recommendationId: string, key: string) => post<Recommendation>(`/projects/${projectId}/standard-recommendations/${recommendationId}/revalidate`, undefined, key),
};
