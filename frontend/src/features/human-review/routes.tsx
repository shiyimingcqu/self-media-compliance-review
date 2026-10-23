import type { RouteObject } from 'react-router-dom';
export const humanReviewRoutes: RouteObject[] = [
  { path: '/reviews', lazy: async () => ({ Component: (await import('./pages/HumanReviewsPage')).HumanReviewsPage }) },
  { path: '/reviews/:reviewId', lazy: async () => ({ Component: (await import('./pages/ReviewDetailPage')).ReviewDetailPage }) },
];
