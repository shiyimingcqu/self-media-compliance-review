import type { RouteObject } from 'react-router-dom';

export const projectRoutes: RouteObject[] = [
  { path: '/projects/:projectId/rules', lazy: async () => ({ Component: (await import('../workflow/ProjectRulesPage')).ProjectRulesPage }) },
  { path: '/projects', lazy: async () => ({ Component: (await import('./pages/ProjectsPage')).ProjectsPage }) },
  { path: '/projects/:projectId', lazy: async () => ({ Component: (await import('./pages/ProjectDetailPage')).ProjectDetailPage }) },
];
