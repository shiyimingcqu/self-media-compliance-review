import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

test('脚本未提交时品牌和达人均可上传视频，不触发模型调用', async ({ page, request }) => {
  const headers = () => ({ 'Idempotency-Key': crypto.randomUUID() });
  const project = (await (await request.post('/api/v1/projects', { headers: headers(), data: { name: '视频先行提交', brandName: '品牌', platforms: ['douyin'] } })).json()).data;
  const rule = { text: '产品出现', criterion: '产品可辨认出现即可', importance: 'must' };
  const response = await request.post(`/api/v1/projects/${project.id}/publish-task`, { headers: headers(), data: { creatorName: '视频先行达人', platform: 'douyin', scriptRules: [rule], videoRules: [rule], scriptConfirmed: true, videoConfirmed: true, confirmationNote: '固定测试确认' } });
  expect(response.status()).toBe(201);
  const taskId = (await response.json()).data.id;
  const workflow = (await (await request.get(`/api/v1/tasks/${taskId}/workflow`)).json()).data;
  expect(workflow.currentScript).toBeNull(); expect(workflow.scriptApproval).toBeNull(); expect(workflow.videoAllowed).toBe(true);
  await page.goto(`/projects/${project.id}/tasks/${taskId}/video`);
  await expect(page.getByRole('button', { name: '上传 / 验收视频', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '上传 / 验收视频', exact: true }).click();
  await expect(page.getByLabel('上传完整测试视频', { exact: true })).toBeEnabled();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '切换到达人端', exact: true }).click();
  await expect(page).toHaveURL(`/creator/projects/${project.id}/tasks/${taskId}/video`);
  await page.getByRole('button', { name: '上传 / 验收视频', exact: true }).click();
  const clip = execFileSync(resolve('.venv/Scripts/python.exe'), ['-c', "import tempfile,subprocess,imageio_ffmpeg,sys;from pathlib import Path; tmp=tempfile.TemporaryDirectory();p=Path(tmp.name)/'clip.mp4';subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','color=s=160x120:d=1','-c:v','libx264',str(p)],check=True);sys.stdout.buffer.write(p.read_bytes())"]);
  // Simulate a >2GB browser File size without generating a multi-GB test artifact.
  await page.evaluate(() => Object.defineProperty(File.prototype, 'size', { configurable: true, get: () => 3_000_000_000 }));
  await page.getByLabel('上传完整测试视频', { exact: true }).setInputFiles({ name: 'before-script.mp4', mimeType: 'video/mp4', buffer: clip });
  await expect(page.getByText('before-script.mp4', { exact: true })).toBeVisible();
  await expect(page.getByText('本地视频准备完成，尚未发送给模型', { exact: true })).toBeVisible();
  const runs = (await (await request.get(`/api/v1/evidence-runs?taskId=${taskId}`)).json());
  expect(runs.data).toEqual([]);
});
