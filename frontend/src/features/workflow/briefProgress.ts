import type { VisualRun } from './projectBriefApi';

export function briefProgressLabel(run?: VisualRun | null) {
  if (!['queued', 'processing'].includes(run?.status || '')) return undefined;
  const progress = run?.result?.progress;
  if (!progress) return '正在准备 Brief 页面';
  const labels: Record<string, string> = { direct: '提炼产品事实与四组规则', layout: '判断页面布局', ocr: '识读页面与区域', summary: '提炼全文重点' };
  return `${labels[progress.stage] || '处理 Brief'} · ${progress.completed}/${progress.total} 批`;
}
