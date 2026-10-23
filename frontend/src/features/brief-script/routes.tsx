import type { RouteObject } from 'react-router-dom';
export const briefScriptRoutes: RouteObject[] = [
  // 登记路由（A 02:27 批准本模块第 7 文件改动；不动总表）：上传登记后导航真实 :briefId，不使用假 ID。
  { path: '/projects/:projectId/briefs', lazy: async () => ({ Component: (await import('./pages/BriefPage')).BriefPage }) },
  { path: '/projects/:projectId/briefs/:briefId', lazy: async () => ({ Component: (await import('./pages/BriefPage')).BriefPage }) },
  { path: '/projects/:projectId/tasks/:taskId/script', lazy: async () => ({ Component: (await import('./pages/BrandScriptPage')).BrandScriptPage }) },
];
