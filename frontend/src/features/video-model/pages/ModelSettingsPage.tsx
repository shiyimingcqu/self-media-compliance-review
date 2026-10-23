import { AiElapsed, useAiCallTiming } from '../../../shared/ui/AiElapsed';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Form, Input, AutoComplete, Skeleton, Space, Tag } from 'antd';
import { PageHeader, ErrorState } from '../../../shared/ui';
import { evidenceApi } from '../api';
import type { SettingsInput } from '../api';
import { EvidenceConsole } from '../components/EvidenceConsole';
import { useSearchParams } from 'react-router-dom';
import { DecompositionModelSettings } from '../components/DecompositionModelSettings';
import { PromptCatalog } from '../../workflow/PromptCatalog';

export function ModelSettingsPage() {
  const { message, modal } = App.useApp(), cache = useQueryClient();
  const query = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings });
  const [form] = Form.useForm<SettingsInput>();
  const [searchParams, setSearchParams] = useSearchParams();
  const testTiming = useAiCallTiming('connection-test');
  const tests = useQuery({ queryKey: ['evidence-runs', 'tests'], queryFn: () => evidenceApi.list(), refetchInterval: q => q.state.data?.data.some(r => ['queued', 'processing'].includes(r.status)) ? 2000 : false });
  const testRun = tests.data?.data.find(r => r.kind === 'connection' && r.id === searchParams.get('run')) || tests.data?.data.find(r => r.kind === 'connection');
  const [saving, setSaving] = useState(false), [testing, setTesting] = useState(false), [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string>();
  const testKey = useRef<string | null>(null), busy = useRef(false);
  useEffect(() => { if (query.data) { form.setFieldsValue({ endpoint: query.data.data.endpoint, model: query.data.data.model as SettingsInput['model'], apiKey: undefined }); setDirty(false); } }, [query.data, form]);
  async function save(clearKey = false) {
    if (saving) return;
    let values: SettingsInput;
    try { values = await form.validateFields(); } catch { return; }
    setSaving(true); setError(undefined);
    try {
      const r = await evidenceApi.save({ endpoint: values.endpoint, model: values.model, revision: query.data!.data.revision, apiKey: clearKey ? undefined : values.apiKey || undefined, clearKey });
      form.setFieldValue('apiKey', undefined); cache.setQueryData(['evidence-model-settings'], r);
      testKey.current = null; setDirty(false); message.success(clearKey ? 'Key 已清除' : '配置已保存，尚未调用模型');
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  }
  function test() {
    if (busy.current || !query.data) return;
    busy.current = true;
    modal.confirm({ title: '确认进行一次真实文本连接测试？', content: '使用已保存 Key 发送简短文本，可能计费。不上传视频，不自动重试。', okText: '确认真实调用', cancelText: '取消', onCancel: () => { busy.current = false; },
      onOk: async () => {
        setTesting(true); setError(undefined); testTiming.start();
        try { testKey.current ||= crypto.randomUUID(); const r = await evidenceApi.start({ kind: 'connection', configRevision: query.data!.data.revision, allowRealCall: true, testRule: '文本连接测试', compressionPreset: 'balanced' }, testKey.current); testTiming.finish(r.data.status, r.data); testKey.current = null; setSearchParams(previous => { const next = new URLSearchParams(previous); next.set('run', r.data.id); return next; }, { replace: true }); await cache.invalidateQueries({ queryKey: ['evidence-runs', 'tests'] }); message.success('已提交，请在下方调用记录查看结果'); }
        catch (e) { testTiming.finish('failed'); setError(e instanceof Error ? e.message : '提交失败'); }
        finally { setTesting(false); busy.current = false; }
      },
    });
  }
  return <>
    <PageHeader title="模型与运行设置" description="分别配置 Brief 解构语言模型与内容审核模型，两套服务、Key 和参数独立保存。" action={<Tag color="orange">真实调用需单独确认</Tag>} />
    <DecompositionModelSettings />
    <div className="review-grid"><Card title="内容审核模型服务配置">
      {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <Form form={form} layout="vertical" onValuesChange={() => { setDirty(true); testKey.current = null; }} onFinish={() => save()}>
        <Form.Item name="endpoint" label="接口地址" extra="填写兼容接口的基础地址，例如以 /compatible-mode/v1 结尾；不需要填写 /chat/completions。" rules={[{ required: true }, { type: 'url', message: '请输入有效的 HTTPS 地址' }]}><AutoComplete aria-label="模型接口地址" options={[
          { value: 'https://maas.qianwenaiapi.com/compatible-mode/v1', label: '用户提供 · maas.qianwenaiapi.com' },
          { value: 'https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1', label: '用户提供 · token-plan.maas.qianwenaiapi.com' },
          { value: 'https://dashscope.aliyuncs.com/compatible-mode/v1', label: '北京 · dashscope.aliyuncs.com' },
          { value: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', label: '新加坡 · dashscope-intl.aliyuncs.com' },
          { value: 'https://dashscope-us.aliyuncs.com/compatible-mode/v1', label: '美国 · dashscope-us.aliyuncs.com' },
        ]} placeholder="https://maas.qianwenaiapi.com/compatible-mode/v1" /></Form.Item>
        <Form.Item name="model" label="模型" rules={[{ required: true }]}><AutoComplete aria-label="模型名称" options={[{ value: 'qwen3.8-flash' }, { value: 'qwen3.8-omni-flash' }]} placeholder="输入服务支持的模型名称" /></Form.Item>
        <Form.Item name="apiKey" label={<Space>API Key <Tag color={query.data.data.hasKey ? 'green' : 'default'}>{query.data.data.hasKey ? '服务端已保存' : '未配置'}</Tag></Space>} rules={[{ min: 8, message: 'Key 至少8个字符' }]} extra="留空保留已存 Key，服务端不会回传已存明文。"><Input.Password autoComplete="new-password" aria-label="阿里云 API Key" placeholder={query.data.data.hasKey ? '输入新 Key 替换，留空保留' : '输入模型服务 API Key'} /></Form.Item>
        <Space wrap><Button type="primary" htmlType="submit" loading={saving}>保存配置</Button><Button disabled={!dirty || saving} onClick={() => { form.setFieldsValue({ endpoint: query.data!.data.endpoint, model: query.data!.data.model as SettingsInput['model'], apiKey: undefined }); setDirty(false); }}>取消更改</Button><Button danger disabled={!query.data.data.hasKey || saving} onClick={() => modal.confirm({ title: '清除服务端 API Key？', content: '运行中的调用不受影响，新调用将被阻止。', onOk: () => save(true) })}>清除 Key</Button></Space>
      </Form>}
      {error && <Alert style={{ marginTop: 16 }} type="error" showIcon title={error} />}
    </Card><div><Card title="显式真实连接测试" extra={<AiElapsed run={testing || testTiming.run?.status === 'failed' ? testTiming.run : testRun || testTiming.run} />}><p>测试使用已保存配置；保存 Key 本身不会发起调用。</p><Button type="primary" loading={testing} disabled={dirty || saving || !query.data?.data.hasKey} onClick={test}>测试文本连接（真实调用）</Button>{dirty && <p className="muted">先保存或取消更改，再进行测试。</p>}<Alert style={{ marginTop: 18 }} type="warning" showIcon title="文本成功不等于视频或音轨已验证" description="短视频测试在下方单独发起；不会自动切换全局运行模式或更改任务通过资格。" /></Card><Card className="stack-card" title="凭证与调用边界"><p>Key 在服务端加密保存，主密钥独立存放。此入口仅供本机开发使用。</p><p className="muted">原片上传不设大小上限，上传后可本地压缩；平台不设发送副本大小上限，实际接收能力以模型服务返回为准。最多四次模型请求，不自动重试。</p></Card></div></div>
    <div className="section-heading" style={{ marginTop: 28, marginBottom: 16 }}><div><h2>完整视频与工具取证测试</h2><p>自行选择待发送素材，确认后才使用已保存 Key 调用所选服务。</p></div></div>
    <EvidenceConsole disabled={dirty || saving} />
    <PromptCatalog />
  </>;
}
