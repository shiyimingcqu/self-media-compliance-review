import { api } from '../../shared/api/client';

export type DecompositionSettings = { endpoint: string; model: string; hasKey: boolean; revision: number; updatedAt: string | null; reasoningEffort: 'none' | 'low' | 'medium' | 'xhigh'; temperature: number };
export type DecompositionSettingsInput = Pick<DecompositionSettings, 'endpoint' | 'model' | 'revision' | 'reasoningEffort' | 'temperature'> & { apiKey?: string; clearKey?: boolean };
export const decompositionModelApi = {
  settings: () => api<{ data: DecompositionSettings }>('/settings/models/decomposition'),
  save: (body: DecompositionSettingsInput) => api<{ data: DecompositionSettings }>('/settings/models/decomposition', { method: 'PUT', body: JSON.stringify(body) }),
  copyReviewCredentials: (revision: number) => api<{ data: DecompositionSettings }>('/settings/models/decomposition/copy-review-credentials', { method: 'POST', body: JSON.stringify({ revision }) }),
};
