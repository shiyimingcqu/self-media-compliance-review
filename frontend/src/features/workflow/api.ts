import type { Acceptance } from '../creator/api';
import type { StandardDraft } from './standardsApi';
import { api } from '../../shared/api/client';

type Envelope<T> = { data: T };
type ImageCheck = { image_id: string; description: string; rule_ids: string[] };
export type Rule = { id: string; text: string; category: string; importance: string; criterion?: string; reviewStages: string[]; source: Record<string, unknown> };
export type ScriptReview = { snapshot?: { paragraphMode?: boolean; rules?: { id: string; importance: string; text?: string; criterion?: string; ruleNumber?: number }[]; multimodal?: { images: { id: string; locations: string[]; width: number; height: number }[]; uncovered: string[]; incomplete: boolean } }; acceptance?: Acceptance; id: string; status: string; contextKey: string; scriptVersionId: string; ruleVersionId: string | null; createdAt: string; updatedAt?: string; errorCode?: string; errorMessage?: string; result?: { diagnostics?: { errorType?: string; validationError?: string; readTimeoutSeconds?: number; offset?: number; line?: number; column?: number; context?: string; schemaErrors?: { path: string; constraint: string; message: string }[] }; recovery?: { repaired: boolean; requiresManualReview: boolean; repairedOutput?: string }; finishReason?: string; coverage?: { totalRules: number; validatedRules: number; pendingRuleIds: string[] }; providerHttpStatus?: number; partialReport?: NonNullable<ScriptReview['result']>['report']; report?: { summary: string; mandatory_checks?: { rule_id: string; rule_number?: number; verdict: string; requirement: string; reason: string; quote?: string; quote_segments?: string[]; image_ids?: string[] }[]; image_checks?: ImageCheck[]; findings: { rule_id: string; rule_number?: number; image_ids?: string[]; verdict: string; quote: string; quote_segments?: string[]; evidence_status?: 'unverified'; evidence_warning?: string; unverified_quote?: string; unverified_quote_segments?: string[]; description: string; suggestion: string }[] }; providerRequestIds?: string[]; usage?: unknown[]; modelResponse?: string } };
export const isRecoverableOutputError = (code?: string) => ['SCRIPT_REPORT_INVALID', 'OUTPUT_INVALID_JSON', 'OUTPUT_SCHEMA_INVALID', 'RULE_REF_INVALID', 'EVIDENCE_MISSING'].includes(code || '');
export type Workflow = {
  videoSubmitted?: boolean; videoSubmittedAt?: string | null;
  scriptSubmissionReady?: boolean; scriptSubmitted?: boolean; scriptSubmittedAt?: string | null;
  videoComponentAcceptance?: { passed: boolean; blockingRules: { ruleId: string; ruleNumber?: number; text: string }[]; reviewRules?: { ruleId: string; ruleNumber?: number; text: string }[]; reviewCount?: number; message: string };
  scriptAcceptance: Acceptance; brandReviewAllowed: boolean; taskId: string; projectId: string; contextKey: string; stage: string; writable: boolean;
  standardSets?: { script: { id: string; rules: StandardDraft[]; source_version_id?: string | null } | null; video: { id: string; rules: StandardDraft[]; source_version_id?: string | null } | null }; taskRevision?: number; rulesReady: boolean; rulesMessage: string; ruleVersionId: string | null; rules: Rule[];
  currentScript: { scriptVersionId: string; versionLabel: string; extractionStatus: string; inputType?: string; fileId?: string | null; text: string | null; sourceMap: { text: string; paragraphId?: string; ordinal?: number; location?: string | null }[]; parseMeta: { warnings?: string[]; uncoveredMedia?: string[]; partial?: boolean; coverage?: string } } | null;
  scriptReview: ScriptReview | null; scriptReviews: ScriptReview[];
  scriptApproval: { id: string; note: string; created_at: string } | null;
  videoAllowed: boolean; latestVideoRunId: string | null; videoReviewReady: boolean;
  finalDecision: { decision: string; note: string; created_at: string } | null;
  history: { id: string; scope: string; decision: string; note: string; created_at: string; target_id: string }[];
};
export const workflowApi = {
  state: (taskId: string) => api<Envelope<Workflow>>(`/tasks/${taskId}/workflow`),
  analyze: (taskId: string, body: Record<string, unknown>, key: string) => api<Envelope<ScriptReview>>(`/tasks/${taskId}/workflow/script-reviews`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) }),
  revalidate: (reviewId: string, key: string) => api<Envelope<ScriptReview>>(`/workflow/script-reviews/${encodeURIComponent(reviewId)}/revalidate`, { method: 'POST', headers: { 'Idempotency-Key': key } }),
  decide: (taskId: string, body: Record<string, unknown>, key: string) => api<Envelope<{ id: string; stage: string }>>(`/tasks/${taskId}/workflow/decisions`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) }),
};
