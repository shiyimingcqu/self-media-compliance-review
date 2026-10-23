import { test, expect } from '@playwright/test';

test('设置Key、保存不调用、显式确认、视频证据展示（模拟HTTP，不计费）', async ({ page }) => {
  let revision = 0, hasKey = false, calls = 0;
  let endpoint = 'https://dashscope.aliyuncs.com/compatible-mode/v1', model = 'qwen3.8-omni-flash', maxInlineBytes: number | null = 7_000_000;
  let runs: Record<string, unknown>[] = [];
  const config = () => ({ endpoint, model, hasKey, revision, maxInlineBytes });
  await page.route('**/api/v1/settings/models', async route => {
    if (route.request().method() === 'PUT') {
      const input = route.request().postDataJSON();
      expect(input.apiKey).toBe('sk-browser-test-key');
      expect(input.maxInlineBytes).toBeUndefined();
      expect(input.model).toBe('custom-video-model');
      expect(input.endpoint).toBe('https://models.example.com/v1');
      endpoint = input.endpoint; model = input.model; maxInlineBytes = null;
      hasKey = true; revision++;
    }
    await route.fulfill({ json: { data: config(), requestId: 'test' } });
  });
  await page.route('**/api/v1/files', async route => route.fulfill({ json: { data: { id: 'file-browser-test', originalName: 'test.mp4' }, requestId: 'test' } }));
  await page.route('**/api/v1/video-preparations', async route => route.fulfill({ json: { data: { fileId: 'file-browser-test', preset: 'balanced', originalBytes: 10000000, preparedBytes: 10000000, original: { width: 1080, height: 1920 }, prepared: { width: 720, height: 1280 }, canSend: true }, requestId: 'test' } }));
  await page.route('**/api/v1/evidence-runs/*/artifacts/*', async route => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') }));
  await page.route('**/api/v1/evidence-runs*', async route => {
    if (route.request().method() === 'POST') {
      const input = route.request().postDataJSON();
      expect(input.allowRealCall).toBe(true); calls++;
      const video = input.kind === 'video';
      const run = { id: `run-${calls}`, kind: input.kind, fileId: video ? 'file-browser-test' : null, status: 'succeeded', configRevision: revision, snapshot: {}, createdAt: '2026-10-20T08:00:00Z', updatedAt: '2026-10-20T08:00:00Z',
        result: video ? { report: { summary: '模拟：完成工具取证', findings: [{ id: 'i1', rule_id: 'r1', verdict: 'needs_confirmation', description: '请核对截图与原视频', evidence_ids: ['a1'] }] }, artifacts: [{ id: 'a1', kind: 'image', issueId: 'i1', startMs: 1000 }], providerRequestIds: ['provider-mock-2'], usage: [] } : { text: 'OK', providerRequestIds: ['provider-mock-1'], usage: [] } };
      runs = [run, ...runs];
      await route.fulfill({ status: 202, json: { data: run, requestId: 'test' } });
    } else await route.fulfill({ json: { data: runs, requestId: 'test' } });
  });
  await page.goto('/settings/models');
  await page.getByRole('combobox', { name: '模型接口地址', exact: true }).fill('https://models.example.com/v1');
  await page.getByRole('combobox', { name: '模型名称' }).fill('custom-video-model');
  await expect(page.getByRole('spinbutton', { name: '视频发送大小上限（MB）' })).toHaveCount(0);
  await page.getByRole('textbox', { name: '阿里云 API Key' }).fill('sk-browser-test-key');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.getByText('服务端已保存')).toBeVisible();
  expect(calls).toBe(0);
  await expect(page.getByRole('textbox', { name: '阿里云 API Key' })).toHaveValue('');
  await page.getByRole('button', { name: '测试文本连接（真实调用）' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(calls).toBe(0);
  await page.getByRole('button', { name: '确认真实调用', exact: true }).click();
  await expect(page.getByText('文本连接可用，视频与音轨能力仍待验证')).toBeVisible();
  expect(calls).toBe(1);
  await page.getByLabel('上传完整测试视频').setInputFiles({ name: 'test.mp4', mimeType: 'video/mp4', buffer: Buffer.from('mock-local-upload') });
  await expect(page.getByText('test.mp4', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '真实调用并收集证据', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '真实调用并收集证据', exact: true }).click();
  expect(calls).toBe(1);
  await page.getByRole('button', { name: '确认真实调用', exact: true }).click();
  await expect(page.getByText('模拟：完成工具取证')).toBeVisible();
  await expect(page.getByRole('tab', { name: /未通过/ })).toHaveAttribute('data-state', 'active');
  await page.getByRole('tab', { name: /待确定/ }).click();
  await expect(page.getByText('截图证据', { exact: true })).toBeVisible();
  expect(calls).toBe(2);
  await page.screenshot({ path: 'test-results/model-settings-evidence.png', fullPage: true });
});
