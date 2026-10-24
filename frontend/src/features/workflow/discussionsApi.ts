import { api } from '../../shared/api/client';
export type DiscussionScope = 'script' | 'video';
export type DisputeStatus = 'pending' | 'needs_info' | 'accepted' | 'rejected';
export const disputeLabels: Record<DisputeStatus, string> = { pending: '待品牌裁决', needs_info: '请补充说明', accepted: '品牌采纳异议', rejected: '品牌维持原判' };
export const progressLabels: Record<string, string> = { not_submitted: '未提交', pending_review: '已提交待验收', reviewing: 'AI 审核中', call_failed: '调用异常', needs_response: '未通过·待达人处理', modifying: '修改中', disputed: '有异议', pending_brand: '待品牌确认', approved: '品牌已通过' };
export type Dispute = { id: string; task_id: string; scope: DiscussionScope; review_id: string; rule_id: string; status: DisputeStatus; revision: number; created_at: string; updated_at: string; creator_name?: string; project_name?: string; project_id?: string; rule: { text: string; criterion?: string; ruleNumber?: number }; finding: { description?: string; verdict: string; quote?: string }; messages: { id: string; actor: 'creator' | 'brand'; kind: string; body: string; location: string; created_at: string }[] };
export type Discussion = { response: { action: 'modifying' | 'disputing'; revision: number } | null; disputes: Dispute[]; current: boolean; writable: boolean };
export type DisputeDetail = Dispute & { current: boolean; writable: boolean; script: string; fileId?: string; artifacts: { id: string; kind: string; startMs: number; endMs?: number }[] };
export type Progress = { id: string; creator_name: string; project_id: string; project_name: string; platform: string; status: string; focus: DiscussionScope; script: { status: string; reviewId?: string }; video: { status: string; reviewId?: string }; lastReminder?: { created_at: string; read_at?: string; message: string } };
const post = <T,>(path: string, body: unknown, key: string) => api<{ data: T }>(path, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) });
export const discussionsApi = {
  get: (task: string, scope: DiscussionScope, reviewId: string) => api<{ data: Discussion }>(`/tasks/${task}/review-discussion?scope=${scope}&reviewId=${encodeURIComponent(reviewId)}`),
  respond: (task: string, body: unknown, key: string) => post(`/tasks/${task}/review-responses`, body, key),
  dispute: (task: string, body: unknown, key: string) => post(`/tasks/${task}/review-disputes`, body, key),
  list: (params: URLSearchParams) => api<{ data: { items: Dispute[]; total: number } }>(`/workflow/review-disputes?${params}`),
  detail: (project: string, id: string) => api<{ data: DisputeDetail }>(`/projects/${project}/review-disputes/${id}`),
  decide: (project: string, id: string, body: unknown, key: string) => post(`/projects/${project}/review-disputes/${id}/decisions`, body, key),
  progress: (params: URLSearchParams) => api<{ data: { items: Progress[]; total: number; counts: Record<string, number> } }>(`/workflow/creator-progress?${params}`),
  remind: (task: string, body: unknown, key: string) => post(`/tasks/${task}/creator-reminders`, body, key),
  reminders: (task: string) => api<{ data: { id: string; scope: DiscussionScope; message: string; created_at: string; read_at?: string }[] }>(`/tasks/${task}/creator-reminders`),
  read: (task: string, id: string) => api(`/tasks/${task}/creator-reminders/${id}/read`, { method: 'POST' }),
};
