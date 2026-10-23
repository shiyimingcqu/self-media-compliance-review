import { createBrowserRouter, Navigate, Link, useRouteError } from 'react-router-dom';
import { Result, Button, Skeleton } from 'antd';
import { AppShell } from './AppShell';
import { projectRoutes } from '../features/projects/routes';
import { briefScriptRoutes } from '../features/brief-script/routes';
import { videoModelRoutes } from '../features/video-model/routes';
import { humanReviewRoutes } from '../features/human-review/routes';

function RouteError() {
  const error = useRouteError();
  return <Result status="error" title="页面暂时无法打开" subTitle={error instanceof Error ? error.message : '请返回工作台重试'} extra={<Link to="/projects"><Button type="primary">返回工作台</Button></Link>} />;
}

export const router = createBrowserRouter([{ element: <AppShell />, errorElement: <RouteError />, hydrateFallbackElement: <div style={{ padding: 32 }}><Skeleton active /></div>, children: [
  { index: true, element: <Navigate to="/projects" replace /> },
  { path: '/creator', lazy: async () => ({ Component: (await import('../features/creator/CreatorHomePage')).CreatorHomePage }) },
  { path: '/creator/projects/:projectId/tasks/:taskId', lazy: async () => ({ Component: (await import('../features/creator/CreatorTaskPage')).CreatorTaskPage }) },
  { path: '/creator/projects/:projectId/tasks/:taskId/video', lazy: async () => ({ Component: (await import('../features/creator/CreatorVideoPage')).CreatorVideoPage }) },
  { path: '/creator/projects/:projectId/tasks/:taskId/image', lazy: async () => ({ Component: (await import('../features/image-posts/ImagePostPage')).ImagePostPage }) },
  { path: '/projects/:projectId/tasks/:taskId/image', lazy: async () => ({ Component: (await import('../features/image-posts/ImagePostPage')).ImagePostPage }) },
  { path: '/projects/:projectId/brief-review', lazy: async () => ({ Component: (await import('../features/workflow/BriefReviewPage')).BriefReviewPage }) },
  { path: '/rules', lazy: async () => { const { ProjectResourcePage } = await import('./WorkspacePages'); return { Component: () => <ProjectResourcePage scope="rules" /> }; } },
  { path: '/feedback', lazy: async () => { const { ProjectResourcePage } = await import('./WorkspacePages'); return { Component: () => <ProjectResourcePage scope="feedback" /> }; } },
  { path: '/dashboard', lazy: async () => ({ Component: (await import('./WorkspacePages')).DashboardPage }) },
  ...projectRoutes, ...briefScriptRoutes, ...videoModelRoutes, ...humanReviewRoutes,
  { path: '*', element: <Result status="404" title="页面不存在" extra={<Link to="/projects"><Button type="primary">返回项目列表</Button></Link>} /> },
] }]);
