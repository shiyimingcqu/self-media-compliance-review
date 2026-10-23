import { api } from '../../shared/api/client';
import type { Envelope } from '../../shared/api/types';
import type { components } from '../../shared/api/generated';
export type ModelSettings = components['schemas']['ModelSettingsView'];
export type SettingsInput = components['schemas']['ModelSettingsInput'];
export type EvidenceRun = components['schemas']['EvidenceRunView'];
export type RunInput = components['schemas']['EvidenceRunInput'];
export type Preparation = components['schemas']['VideoPreparationView'];
export const evidenceApi = {
  settings: () => api<Envelope<ModelSettings>>('/settings/models'),
  save: (payload: SettingsInput) => api<Envelope<ModelSettings>>('/settings/models', { method: 'PUT', body: JSON.stringify(payload) }),
  start: (payload: RunInput, key: string) => api<Envelope<EvidenceRun>>('/evidence-runs', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload) }),
  prepare: (fileId: string, preset: 'balanced' | 'compact' | 'original') => api<Envelope<Preparation>>('/video-preparations', { method: 'POST', body: JSON.stringify({ fileId, preset }) }),
  list: (taskId?: string) => api<Envelope<EvidenceRun[]>>(`/evidence-runs${taskId ? `?taskId=${encodeURIComponent(taskId)}` : ''}`),
};
export function artifactUrl(runId: string, id: string) {
  return `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/evidence-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(id)}`;
}
export function preparedVideoUrl(fileId: string, preset: string) {
  return `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/video-preparations/${encodeURIComponent(fileId)}/${encodeURIComponent(preset)}/content`;
}
