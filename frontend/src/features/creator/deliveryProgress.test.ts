import { describe, expect, it } from 'vitest';
import { deliveryAction, deliveryProgress, taskDeliveries } from './deliveryProgress';
import type { CreatorTask, Delivery } from './api';

const empty: Delivery = { status: 'not_submitted', submitted: false, approved: false };
const submitted: Delivery = { status: 'pending_brand', submitted: true, approved: false };
const approved: Delivery = { status: 'approved', submitted: true, approved: true };

describe('script plus either video or image delivery', () => {
  it('routes empty material and an unaudited upload to submission rather than feedback', () => {
    expect(deliveryAction(empty)).toEqual({ label: '提交材料', upload: true });
    expect(deliveryAction({ ...empty, status: 'pending_review', hasMaterial: true, hasReview: false }).label).toBe('提交验收');
    expect(deliveryAction({ ...submitted, hasMaterial: false }).label).toBe('提交材料');
    expect(deliveryAction(submitted).label).toBe('查看反馈');
    expect(deliveryAction({ ...empty, status: 'reviewing' }).label).toBe('查看验收进度');
  });
  it('counts video and image as alternatives, with a total of two', () => {
    for (const [video, image] of [[submitted, empty], [empty, submitted], [submitted, submitted]]) {
      expect(deliveryProgress({ script: submitted, video, image })).toMatchObject({ submitted: 2, total: 2, complete: false });
    }
  });
  it('does not confuse upload or AI completion with submission to brand', () => {
    expect(deliveryProgress({ script: { ...empty, status: 'ready_to_submit' }, video: submitted, image: empty }).submitted).toBe(1);
  });
  it('finishes after brand approval of script and either material', () => {
    expect(deliveryProgress({ script: approved, video: approved, image: empty }).complete).toBe(true);
    expect(deliveryProgress({ script: approved, video: empty, image: approved }).complete).toBe(true);
    expect(deliveryProgress({ script: submitted, video: approved, image: approved }).complete).toBe(false);
  });
  it('uses explicit delivery metadata ahead of legacy status fields', () => {
    const task = { scriptProgress: 'pending_brand', deliveries: { script: { ...empty, status: 'ready_to_submit' } } } as CreatorTask;
    expect(taskDeliveries(task).script.submitted).toBe(false);
  });
});
