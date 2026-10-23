import { api } from '../../shared/api/client';
import type { Platform } from '../../shared/api/types';
export type Acceptance = { passed: boolean; status: string; blockingCount: number; message: string; checks: { rule_id: string; rule_number?: number; image_ids?: string[]; requirement: string; verdict: string; quote: string; reason: string }[] };
export type CreatorTask = { videoAllowed?: boolean; videoReviewReady?: boolean; imageRulesReady?: boolean; imageVersion?: string | null; imageAcceptance?: { passed: boolean; message: string }; imageBrandApproved?: boolean; id: string; project_id: string; creator_name: string; platform: Platform; project_name: string; brand_name: string; briefTitle: string; briefFileId: string | null; rulesReady: boolean; currentVersion: string | null; scriptAcceptance: Acceptance; brandApproved: boolean; brandDecision: { decision: string; note: string } | null };
export const creatorApi = { tasks: () => api<{ data: CreatorTask[] }>('/creator/tasks') };
