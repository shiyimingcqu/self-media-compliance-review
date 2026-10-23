import { api } from '../../shared/api/client';
import type { components } from '../../shared/api/generated';
export type ComponentNode = components['schemas']['ComponentNode'];
export type ComponentPlan = components['schemas']['ComponentPlan'];
export type ComponentParameters = components['schemas']['ComponentParameters'];
export type ComponentKind = ComponentNode['kind'];
export type ReferenceImage = { id: string; project_id: string; name: string; sha256: string; width: number; height: number; byte_size: number };
export type ComponentDefinition = { kind: ComponentKind; name: string; requiresReference: boolean; visual: boolean; prompt: { content: string; version: string } };
const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
export const referenceImageUrl = (projectId: string, id: string) => `${base}/projects/${encodeURIComponent(projectId)}/reference-images/${encodeURIComponent(id)}/content`;
export const videoComponentsApi = {
  catalog: () => api<{ data: ComponentDefinition[] }>('/workflow/video-components'),
  references: (projectId: string) => api<{ data: ReferenceImage[] }>(`/projects/${projectId}/reference-images`),
  upload: (projectId: string, file: File, key: string) => { const body = new FormData(); body.append('file', file); return api<{ data: ReferenceImage }>(`/projects/${projectId}/reference-images`, { method: 'POST', headers: { 'Idempotency-Key': key }, body }); },
};
export const newNode = (kind: ComponentKind): ComponentNode => ({ id: crypto.randomUUID(), kind, parameters: { referenceImageIds: [], startSeconds: 0, endSeconds: null, requireWholeProduct: kind === 'product_presence', requireClearLogo: kind === 'logo_presence', allowOcclusion: false, minContinuousSeconds: 1, minTotalSeconds: 1, minOccurrences: 1, occurrenceGapSeconds: 1, terms: [], action: '' } });
