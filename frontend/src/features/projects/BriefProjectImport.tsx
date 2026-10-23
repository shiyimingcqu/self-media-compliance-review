import { AiElapsed, useAiCallTiming } from '../../shared/ui/AiElapsed';
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Alert, App, Button, Checkbox, Form, Input, Modal, Select, Space, Spin, Tag } from 'antd';
import { FileAddOutlined, FileTextOutlined, UploadOutlined } from '@ant-design/icons';
import { api, uploadFile } from '../../shared/api/client';
import type { ProjectCreate } from '../../shared/api/types';
import { platformNames } from '../../shared/lib/format';
import './brief-import.css';

type ImportRun = { createdAt?: string; updatedAt?: string;
  id: string; fileId: string; status: string; projectId?: string; errorMessage?: string;
  snapshot: { filename: string };
  result?: { metadata?: { projectName: string; brandName: string; productNames: string[]; platforms: ProjectCreate['platforms']; warnings: string[]; evidence: { field: string; value: string; source: string; quote: string }[] } };
};
const active = (r?: ImportRun) => !!r && ['queued', 'processing'].includes(r.status);
const labels: Record<string, string> = { queued: '排队中', processing: '识别中', needs_input: '待补充', succeeded: '已创建', failed: '识别失败', interrupted: '已暂停' };

export function BriefProjectImport() {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File>();
  const [runId, setRunId] = useState<string>();
  const metadataTiming = useAiCallTiming('brief-import');
  const [stage, setStage] = useState('');
  const [form] = Form.useForm<ProjectCreate>();
  const input = useRef<HTMLInputElement>(null);
  const operation = useRef<{ key: string; fileId?: string }>({ key: crypto.randomUUID() });
  const navigated = useRef<string>('');
  const refreshed = useRef<string>('');
  const navigate = useNavigate();
  const cache = useQueryClient();
  const { message } = App.useApp();
  const recent = useQuery({ queryKey: ['brief-imports'], queryFn: () => api<{ data: ImportRun[] }>('/brief-project-imports'), refetchInterval: q => q.state.data?.data.some(active) ? 2000 : false });
  const detail = useQuery({ queryKey: ['brief-import', runId], enabled: !!runId, queryFn: () => api<{ data: ImportRun }>(`/brief-project-imports/${runId}`), refetchInterval: q => !q.state.data || active(q.state.data.data) ? 1500 : false });
  const run = detail.data?.data;
  const busy = active(run);
  const refresh = async () => { await Promise.all(['projects', 'dashboard', 'brands', 'brief-imports'].map(key => cache.invalidateQueries({ queryKey: [key] }))); };
  useEffect(() => {
    if (run?.status === 'needs_input' && run.result?.metadata) {
      const m = run.result.metadata;
      form.setFieldsValue({ name: m.projectName, brandName: m.brandName, productNames: m.productNames, platforms: m.platforms });
    }
    if (run?.status === 'succeeded' && refreshed.current !== run.id) {
      refreshed.current = run.id; void refresh();
    }
    if (open && run?.status === 'succeeded' && run.projectId && navigated.current !== run.id) {
      navigated.current = run.id;
      setOpen(false); message.success('项目已创建，Brief 正在继续解析'); navigate(`/projects/${run.projectId}`);
    }
  }, [run, open]);

  const start = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error('请选择 Brief 文件');
      if (!operation.current.fileId) {
        setStage('正在上传 Brief');
        operation.current.fileId = (await uploadFile(file, 'brief', operation.current.key + ':upload')).data.id;
      }
      setStage('正在启动项目识别'); metadataTiming.start();
      return api<{ data: ImportRun }>('/brief-project-imports', { method: 'POST', headers: { 'Idempotency-Key': operation.current.key }, body: JSON.stringify({ fileId: operation.current.fileId, allowRealCall: true }) });
    },
    onSuccess: result => { metadataTiming.finish(result.data.status); setRunId(result.data.id); void cache.invalidateQueries({ queryKey: ['brief-imports'] }); },
    onError: e => { metadataTiming.finish('failed'); message.error(e.message); },
    onSettled: () => setStage(''),
  });
  const complete = useMutation({
    mutationFn: (project: ProjectCreate) => api<{ data: ImportRun }>(`/brief-project-imports/${runId}/complete`, { method: 'POST', body: JSON.stringify({ project }) }),
    onSuccess: result => { cache.setQueryData(['brief-import', runId], result); void refresh(); },
    onError: e => message.error(e.message),
  });
  const newImport = () => { metadataTiming.reset(); setRunId(undefined); setFile(undefined); operation.current = { key: crypto.randomUUID() }; setOpen(true); };
  const waiting = recent.data?.data.filter(r => ['needs_input', 'queued', 'processing', 'interrupted', 'failed'].includes(r.status)) ?? [];
  return <>
    <Button size="large" type="primary" icon={<FileAddOutlined aria-hidden />} onClick={newImport}>上传 Brief 创建项目</Button>
    {!!waiting.length && <Button size="large" onClick={() => { setRunId(waiting[0].id); setOpen(true); }}>导入记录 <Tag>{waiting.length}</Tag></Button>}
    <Modal title="上传 Brief 创建项目" open={open} onCancel={() => { if (!start.isPending && !complete.isPending) setOpen(false); }} footer={null} width={680} mask={{ closable: false }}>
      <div className="brief-import-flow"><span>01 上传 Brief</span><i>→</i><span>02 识别品牌 · 产品 · 平台</span><i>→</i><span>03 自动创建</span><AiElapsed run={start.isPending ? metadataTiming.run : run || metadataTiming.run} /></div>
      {!runId && <>
        <p className="muted">模型会结合文件名和首页内容识别项目信息，创建后继续解析全文并提取事实卡。</p>
        <input ref={input} type="file" accept=".pdf,.txt" aria-label="选择项目 Brief" hidden onChange={e => { setFile(e.target.files?.[0]); operation.current = { key: crypto.randomUUID() }; }} />
        <button className="brief-import-file" disabled={start.isPending} onClick={() => input.current?.click()}><FileTextOutlined /><strong>{file?.name || '选择 Brief 文件'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击更换` : '支持 PDF、TXT · 保留原始文件名有助于准确识别'}</span></button>
        <div className="brief-import-start"><span className="muted">使用已配置模型识别和解析，可能产生调用费用。</span><Button type="primary" icon={<UploadOutlined aria-hidden />} loading={start.isPending} disabled={!file} onClick={() => start.mutate()}>{stage || '上传并自动创建'}</Button></div>
      </>}
      {runId && detail.isPending && <Spin description="读取导入进度"><div style={{ height: 80 }} /></Spin>}
      {detail.isError && runId && <Alert type="error" title={detail.error.message} action={<Button onClick={() => detail.refetch()}>重试读取</Button>} />}
      {run && <div className="brief-import-result">
        <Space wrap><FileTextOutlined /><strong>{run.snapshot.filename}</strong><Tag color={busy ? 'processing' : run.status === 'needs_input' ? 'warning' : undefined}>{labels[run.status] || run.status}</Tag></Space>
        {busy && <div className="brief-import-progress"><Spin /><div><strong>正在识别项目信息</strong><p className="muted">结合文件名与首页核对主品牌、产品及投放平台。可以关闭窗口，任务会继续。</p></div></div>}
        {run.errorMessage && <Alert type={run.status === 'needs_input' ? 'warning' : 'error'} title={run.errorMessage} showIcon />}
        {run.status === 'needs_input' && <>
          <Alert type="info" title="请补充或核对未确定的信息" description="确认后直接创建项目并继续全文解析，不会重新调用项目识别模型。" showIcon />
          {run.result?.metadata?.warnings.map((w, i) => <div className="muted" key={i}>{w}</div>)}
          <Form layout="vertical" form={form} onFinish={v => complete.mutate(v)}>
            <div className="form-grid"><Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={50} /></Form.Item><Form.Item name="brandName" label="主品牌" rules={[{ required: true, whitespace: true }]}><Input maxLength={50} placeholder="本次投放的主品牌" /></Form.Item></div>
            <Form.Item name="productNames" label="产品名称" rules={[{ required: true, type: 'array', min: 1, message: '请填写产品名称' }]}><Select mode="tags" placeholder="输入产品名后按回车" tokenSeparators={['，']} /></Form.Item>
            <Form.Item name="platforms" label="投放平台" rules={[{ required: true, type: 'array', min: 1, message: '请选择投放平台' }]}><Checkbox.Group options={Object.entries(platformNames).map(([value, label]) => ({ value, label }))} /></Form.Item>
            <Button type="primary" htmlType="submit" loading={complete.isPending}>确认信息并创建</Button>
          </Form>
          {!!run.result?.metadata?.evidence.length && <details><summary>查看识别出处</summary>{run.result.metadata.evidence.map((e, i) => <p key={i}><Tag>{e.source === 'filename' ? '文件名' : '文档文字'}</Tag>{e.value}：{e.quote}</p>)}</details>}
        </>}
        {run.status === 'succeeded' && <Link to={`/projects/${run.projectId}`}>打开已创建项目</Link>}
        {['failed', 'interrupted'].includes(run.status) && <p className="muted">未自动重试模型调用；可以核对模型配置和用量后重新上传，或使用手动新建项目。</p>}
      </div>}
      {!!recent.data?.data.length && <details className="brief-import-history"><summary>最近导入记录</summary>{recent.data.data.map(r => <button key={r.id} onClick={() => { setRunId(r.id); if (r.status === 'succeeded') navigated.current = r.id; }}><span>{r.snapshot.filename}</span><Tag>{labels[r.status] || r.status}</Tag></button>)}</details>}
    </Modal>
  </>;
}
