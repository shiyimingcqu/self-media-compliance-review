import { api } from '../../shared/api/client';
import type { CSSProperties } from 'react';

export type SheetLayout = { name: string; rowCount: number; colCount: number; columns: number[]; rows: number[]; cells: { address: string; row: number; col: number; text: string; formula?: string | null; uncached: boolean; style: CSSProperties }[]; merges: { r1: number; c1: number; r2: number; c2: number }[]; images: { id: string; cell: string; x: number; y: number; width: number; height: number }[]; hiddenRows: number[]; hiddenColumns: number[]; hidden: boolean };
export type OriginalLayout = { kind: 'pages'; format: string; pages: { page: number; width: number; height: number }[] } | { kind: 'text'; format: string; text: string } | { kind: 'docx'; format: string };
export type ScriptLayout = { supported: boolean; original?: OriginalLayout; scriptVersionId?: string; fileId?: string; sheets: SheetLayout[]; warnings: string[]; images: { id: string; locations: string[]; width: number; height: number }[] };
export const scriptLayoutApi = {
  get: (taskId: string, versionId: string) => api<{ data: ScriptLayout }>(`/tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(versionId)}/layout`),
  imageUrl: (taskId: string, versionId: string, id: string) => `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(versionId)}/layout/images/${encodeURIComponent(id)}`,
  pageUrl: (taskId: string, versionId: string, page: number) => `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(versionId)}/layout/pages/${page}`,
  documentUrl: (taskId: string, versionId: string) => `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(versionId)}/layout/document`,
};
