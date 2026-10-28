import { api } from '../../shared/api/client';
import type { Brand, Dashboard, Envelope, PageEnvelope, Project, ProjectCreate, ProjectDetail } from '../../shared/api/types';

export const projectApi = {
  list: (query: string, signal?: AbortSignal) => api<PageEnvelope<Project>>(`/projects?${query}`, { signal }),
  // 详情端点返回 ProjectDetail（含 briefs 投影）；列表/创建仍是 Project，不假称其含 briefs。
  detail: (id: string) => api<Envelope<ProjectDetail>>(`/projects/${encodeURIComponent(id)}`),
  dashboard: () => api<Envelope<Dashboard>>('/dashboard'),
  brands: () => api<Envelope<Brand[]>>('/brands'),
  create: (payload: ProjectCreate, key: string) => api<Envelope<Project>>('/projects', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload) }),
  remove: (id: string, revision: number, key: string) => api<Envelope<{ id: string; deleted: boolean; cleanupPending: boolean }>>(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { 'Idempotency-Key': key, 'If-Match': String(revision) } }),
};
