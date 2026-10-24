import type { CreatorTask, Delivery, DeliveryKind } from './api';

export function taskDeliveries(task: CreatorTask): Record<DeliveryKind, Delivery> {
  const legacy = (status: string): Delivery => ({ status, submitted: ['pending_brand', 'approved', 'returned'].includes(status), approved: status === 'approved' });
  const normalize = (delivery: Delivery): Delivery => delivery.hasMaterial === false ? { ...delivery, status: 'not_submitted', submitted: false, approved: false }
    : delivery.hasReview === false && !delivery.submitted && !delivery.approved ? { ...delivery, status: 'pending_review' } : delivery;
  return {
    script: normalize(task.deliveries?.script || legacy(task.brandApproved ? 'approved' : task.brandDecision?.decision === 'returned' ? 'returned' : task.scriptProgress || (task.currentVersion ? 'pending_review' : 'not_submitted'))),
    video: normalize(task.deliveries?.video || legacy(task.videoProgress || (task.videoReviewReady ? 'ready_to_submit' : 'not_submitted'))),
    image: normalize(task.deliveries?.image || legacy(task.imageBrandApproved ? 'approved' : task.imageAcceptance?.passed ? 'pending_brand' : task.imageVersion ? 'pending_review' : 'not_submitted')),
  };
}

export function deliveryAction(delivery: Delivery) {
  if (delivery.hasMaterial === false || delivery.status === 'not_submitted') return { label: '提交材料', upload: true };
  if (delivery.hasReview === false || delivery.status === 'pending_review') return { label: '提交验收', upload: false };
  if (delivery.status === 'reviewing') return { label: '查看验收进度', upload: false };
  if (delivery.status === 'call_failed') return { label: '查看异常并重试', upload: false };
  if (['returned', 'modifying'].includes(delivery.status)) return { label: '修改并重新提交', upload: false };
  if (delivery.status === 'ready_to_submit') return { label: '提交品牌', upload: false };
  return { label: '查看反馈', upload: false };
}

export function deliveryProgress(deliveries: Record<DeliveryKind, Delivery>) {
  // The final material is a choice: neither a third requirement nor a third point.
  const materialSubmitted = deliveries.video.submitted || deliveries.image.submitted;
  return {
    submitted: Number(deliveries.script.submitted) + Number(materialSubmitted), total: 2,
    complete: deliveries.script.approved && (deliveries.video.approved || deliveries.image.approved),
    videoChosen: deliveries.video.status !== 'not_submitted', imageChosen: deliveries.image.status !== 'not_submitted',
  };
}

export const deliveryLabels: Record<string, string> = {
  not_submitted: '未提交', pending_review: '待 AI 验收', reviewing: 'AI 审核中', call_failed: '审核未完成',
  ready_to_submit: '待提交品牌', needs_response: '待处理反馈', modifying: '修改中', disputed: '有异议',
  pending_brand: '待品牌复核', approved: '已完成', returned: '品牌退回',
};
