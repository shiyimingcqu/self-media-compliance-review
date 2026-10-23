import type { RouteObject } from 'react-router-dom';
export const videoModelRoutes: RouteObject[] = [
  { path: '/projects/:projectId/tasks/:taskId/video', lazy: async () => ({ Component: (await import('./pages/VideoPage')).VideoPage }) },
  { path: '/settings/models', lazy: async () => ({ Component: (await import('./pages/ModelSettingsPage')).ModelSettingsPage }) },
];
