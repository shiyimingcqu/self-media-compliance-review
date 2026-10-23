import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, AutoComplete, Button, Card, Form, Input, InputNumber, Select, Skeleton, Space, Tag } from 'antd';
import { decompositionModelApi, type DecompositionSettingsInput } from '../../workflow/decompositionModelApi';
import { evidenceApi } from '../api';
import { ErrorState } from '../../../shared/ui';

export function DecompositionModelSettings() {
  const cache = useQueryClient(), { message } = App.useApp(), [form] = Form.useForm<DecompositionSettingsInput>();
  const query = useQuery({ queryKey: ['decomposition-model-settings'], queryFn: decompositionModelApi.settings });
  const audit = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings });
  const [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false), [error, setError] = useState<string>();
  const lock = useRef(false);
  useEffect(() => { if (query.data) { form.setFieldsValue({ ...query.data.data, apiKey: undefined }); setDirty(false); } }, [query.data, form]);
  async function save(clearKey = false, copy = false) {
    if (lock.current || !query.data) return;
    let values: DecompositionSettingsInput;
    try { values = await form.validateFields(); } catch { return; }
    lock.current = true; setBusy(true); setError(undefined);
    try {
      const r = copy ? await decompositionModelApi.copyReviewCredentials(query.data.data.revision) : await decompositionModelApi.save({
        endpoint: values.endpoint, model: values.model, reasoningEffort: values.reasoningEffort, temperature: values.temperature,
        revision: query.data.data.revision, clearKey, apiKey: clearKey ? undefined : values.apiKey || undefined,
      });
      cache.setQueryData(['decomposition-model-settings'], r); setDirty(false); form.setFieldValue('apiKey', undefined);
      message.success(copy ? '解构模型已沿用审核服务地址与 Key，两套配置独立保存，未调用模型' : clearKey ? '解构模型 Key 已清除，审核模型不受影响' : '解构模型配置已保存，未调用模型');
    } catch (e) { setError(e instanceof Error ? e.message : '解构配置保存失败'); }
    finally { lock.current = false; setBusy(false); }
  }
  return <Card title="Brief 解构语言模型" extra={<Tag color="cyan">独立配置</Tag>} style={{ marginBottom: 24 }}>
    <p>将 Brief 转换为候选审核规则，交品牌核对出处后保存。默认 qwen3.8-flash；脚本、图文、视频审核以及 Brief 图片识读继续使用下方审核模型。</p>
    {query.isPending ? <Skeleton active /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <Form name="brief-decomposition-model" form={form} layout="vertical" onValuesChange={() => setDirty(true)} onFinish={() => void save()}>
      <div className="form-grid"><Form.Item name="endpoint" label="解构模型接口地址" rules={[{ required: true }, { type: 'url' }]} extra="兼容接口基础地址，不包含 /chat/completions。"><AutoComplete aria-label="解构模型接口地址" options={[{ value: 'https://maas.qianwenaiapi.com/compatible-mode/v1' }, { value: 'https://dashscope.aliyuncs.com/compatible-mode/v1' }, { value: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' }]} /></Form.Item><Form.Item name="model" label="解构语言模型" rules={[{ required: true }]}><AutoComplete aria-label="解构语言模型" options={[{ value: 'qwen3.8-flash' }]} placeholder="qwen3.8-flash" /></Form.Item></div>
      <div className="form-grid"><Form.Item name="reasoningEffort" label="解构思考强度" rules={[{ required: true }]} extra="高强度可能增加等待时间和用量；兼容服务须支持对应参数。"><Select aria-label="解构思考强度" options={[{ value: 'xhigh', label: '最高 · xhigh' }, { value: 'medium', label: '中等 · medium' }, { value: 'low', label: '低 · low' }, { value: 'none', label: '关闭 · none' }]} /></Form.Item><Form.Item name="temperature" label="解构 Temperature" rules={[{ required: true }]} extra="默认0.1；参数稳定不代表解构结论正确，候选仍需人工确认。"><InputNumber aria-label="解构 Temperature" min={0} max={1} step={0.1} style={{ width: '100%' }} /></Form.Item></div>
      <Form.Item name="apiKey" label={<Space>解构模型 API Key<Tag color={query.data.data.hasKey ? 'green' : 'default'}>{query.data.data.hasKey ? '独立加密保存' : '未配置'}</Tag></Space>} rules={[{ min: 8 }]} extra="留空保留解构 Key；修改或清除此 Key 不改变审核模型 Key。"><Input.Password aria-label="解构模型 API Key" autoComplete="new-password" placeholder={query.data.data.hasKey ? '留空保留已存 Key' : '输入语言模型 Key，或沿用审核服务凭证'} /></Form.Item>
      <Space wrap><Button type="primary" htmlType="submit" loading={busy}>保存解构配置</Button><Button disabled={busy || dirty || !audit.data?.data.hasKey} onClick={() => void save(false, true)}>沿用审核服务地址与 Key</Button><Button danger disabled={busy || !query.data.data.hasKey} onClick={() => void save(true)}>清除解构 Key</Button></Space>
      {dirty && <p className="muted" style={{ marginTop: 12 }}>有未保存的更改；沿用审核服务前请先保存配置。</p>}
    </Form>}
    {error && <Alert style={{ marginTop: 12 }} type="error" title={error} />}
  </Card>;
}
