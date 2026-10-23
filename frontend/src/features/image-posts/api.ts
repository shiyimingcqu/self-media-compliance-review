import { api } from '../../shared/api/client';
import type { ScriptReview } from '../workflow/api';

export type PostAsset = { id: string; name: string; width: number; height: number; byteSize: number; sha256: string; caption?: string; ordinal?: number };
export type PostVersion = { id: string; ordinal: number; versionLabel: string; title: string; body: string; images: PostAsset[]; createdAt: string };
export type PostAcceptance = { passed: boolean; status: string; message: string; blockingCount: number };
export type PostReview = { updatedAt?: string; id: string; versionId: string; contextKey: string; status: string; errorCode?: string; errorMessage?: string; createdAt: string; acceptance: PostAcceptance; result?: ScriptReview['result']; snapshot: { version: PostVersion; rules?: PostState['rules'] } };
export type PostState = {
  taskId: string; projectId: string; creatorName: string; contextKey: string; rulesReady: boolean; writable: boolean;
  rules: { id: string; ruleNumber: number; text: string; criterion: string; importance: string }[]; standardSetId: string | null; briefTitle: string;
  currentVersion: PostVersion | null; versions: PostVersion[]; review: PostReview | null; reviews: PostReview[]; acceptance: PostAcceptance;
  brandReviewAllowed: boolean; brandApproved: boolean; decision: { decision: 'approved' | 'returned'; note: string; review_id: string } | null;
  decisions: { decision: 'approved' | 'returned'; note: string; review_id: string; created_at: string }[];
};
export type PostDraft = { title: string; body: string; images: { assetId: string; caption: string }[]; baseVersionId: string | null };
const post = <T,>(path: string, body: unknown, key: string) => api<{ data: T }>(path, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) });
export const postAssetUrl = (taskId: string, id: string, original = false) => `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/tasks/${encodeURIComponent(taskId)}/image-assets/${encodeURIComponent(id)}/${original ? 'original' : 'content'}`;
export const imagePostsApi = {
  state: (taskId: string) => api<{ data: PostState }>(`/tasks/${taskId}/image-posts`),
  upload: (taskId: string, file: File, key: string) => {
    const form = new FormData(); form.append('file', file);
    return api<{ data: PostAsset }>(`/tasks/${taskId}/image-assets`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: form });
  },
  save: (taskId: string, body: PostDraft, key: string) => post<{ version: PostVersion; contextKey: string }>(`/tasks/${taskId}/image-posts`, body, key),
  analyze: (taskId: string, body: { versionId: string; contextKey: string; configRevision: number; allowRealCall: true }, key: string) => post<PostReview>(`/tasks/${taskId}/image-posts/reviews`, body, key),
  decide: (taskId: string, body: { reviewId: string; contextKey: string; decision: 'approved' | 'returned'; note: string; reviewedMaterial: boolean }, key: string) => post<{ id: string; decision: string }>(`/tasks/${taskId}/image-posts/decisions`, body, key),
};
