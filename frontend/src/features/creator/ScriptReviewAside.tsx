import { Alert, Tag } from 'antd';
import type { ReactNode } from 'react';
import type { CreatorTask } from './api';
import type { Workflow } from '../workflow/api';
import { CreatorReviewResponse } from '../workflow/ReviewDiscussion';
import { commentCategory, type ScriptAnnotation } from './scriptAnnotations';
import { formatDate } from '../../shared/lib/format';

export function ScriptReviewAside({ task, view, annotations, reviewing, actions }: { task: Pick<CreatorTask, 'creator_name' | 'project_name' | 'brand_name' | 'platform' | 'brandDecision'>; view?: Workflow; annotations: ScriptAnnotation[]; reviewing: boolean; actions?: ReactNode }) {
  const totals = { changes: 0, uncertain: 0, passed: 0, suggestions: 0, not_applicable: 0 };
  for (const annotation of annotations) totals[commentCategory(annotation.finding, view?.scriptReview || null)]++;
  const review = view?.scriptReview;
  return <aside className="script-review-aside" aria-label="脚本信息与提交操作">
    <section className="script-aside-info"><header><h3>脚本信息</h3><Tag>{view?.currentScript?.versionLabel || '未提交'}</Tag></header><dl>
      <dt>达人</dt><dd>{task.creator_name}</dd><dt>活动</dt><dd>{task.project_name}</dd><dt>品牌</dt><dd>{task.brand_name}</dd><dt>平台</dt><dd>{{ douyin: '抖音', xiaohongshu: '小红书', bilibili: 'B站' }[task.platform] || task.platform}</dd>
      <dt>提交时间</dt><dd>{review ? formatDate(review.createdAt) : '—'}</dd>
    </dl></section>
    <section className="script-aside-overall"><h3>整体结论</h3><div className="script-result-totals"><span className="script-state-pass"><strong>{String(totals.passed).padStart(2, '0')}</strong>通过</span><span className="script-state-uncertain"><strong>{String(totals.uncertain).padStart(2, '0')}</strong>待确定</span><span className="script-state-fail"><strong>{String(totals.changes).padStart(2, '0')}</strong>未通过</span></div><small>建议 {totals.suggestions} 项 · 不适用 {totals.not_applicable} 项</small>
      {view?.scriptAcceptance.status === 'incomplete' && <Alert type="warning" title="材料或报告待补全" description={view.scriptAcceptance.message} />}
    </section>
    <div className="script-aside-response">{actions || <><CreatorReviewResponse sidebar needsResponse={!reviewing && review?.status === 'succeeded' && view?.scriptAcceptance.status !== 'incomplete' && !view?.scriptApproval} canSubmitScript={view?.scriptSubmissionReady ?? (totals.changes === 0 && !!annotations.length)} />
      {view?.scriptApproval && <Alert type="success" title="品牌已通过脚本" description={view.scriptApproval.note} />}
      {!view?.scriptApproval && task.brandDecision?.decision === 'returned' && <Alert type="warning" title="品牌退回修改" description={task.brandDecision.note} />}</>}
    </div>
    <footer>AI 判定仅供参考，最终由品牌确认。</footer>
  </aside>;
}
