// 上传 TXT → 两步登记（BriefUploadFlow 经 useRef 跨 render 稳定）：
// 身份语义（A 02:37①）：每次用户在 file input 重新选择=新上传操作（重置上传 key 与 fileId），
// 不以 name/size 元数据代字节身份；busy 期间 input/Form disabled 并在 onChange 处二次拒绝（generation 双保险）；
// projectId 变化重建 flow，不携带旧项目/路由缓存。register 失败重试复用已传 fileId 与登记 key。
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { App, Alert, Button, Card, Form, Input, Select, Space, Typography } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { uploadFile } from '../../../shared/api/client';
import type { Platform } from '../../../shared/api/types';
import { briefApi, BriefUploadFlow, classifyBriefError, type BriefBlocksCache } from '../api';

type Props = { projectId: string; projectPlatforms: Platform[] };

export function BriefUploadPanel({ projectId, projectPlatforms }: Props) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const flowRef = useRef<BriefUploadFlow>(new BriefUploadFlow());
  const inputRef = useRef<HTMLInputElement | null>(null);
  const generationRef = useRef(0); // busy 期间若发生选择/项目变化，旧异步结果作废
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const [form] = Form.useForm<{ title: string; platforms: Platform[] }>();

  useEffect(() => {
    // 项目/路由切换：重建状态机并清空输入与缓存键，不携带旧上下文。
    flowRef.current = new BriefUploadFlow();
    generationRef.current += 1;
    setBusy(false); setError(null); setHasFile(false);
    form.resetFields();
    if (inputRef.current) inputRef.current.value = '';
  }, [projectId, form]);

  const onPick = () => {
    if (busy) return; // 在途禁换文件（双保险：即便事件穿透也已由 generation 作废）
    generationRef.current += 1; // 重新选择=新上传操作
    flowRef.current.onFileChange();
    setHasFile(true);
    setError(null);
  };

  const submit = async () => {
    const file = inputRef.current?.files?.[0];
    if (!file) return message.error('请选择 TXT 或 PDF 文件');
    const values = await form.validateFields();
    const flow = flowRef.current;
    if (!flow.tryBegin()) return;
    const generation = generationRef.current;
    setBusy(true);
    setError(null);
    try {
      let fileId = flow.currentFileId;
      if (!fileId) {
        const uploaded = await uploadFile(file, 'brief', flow.uploadingKey);
        if (generation !== generationRef.current) return; // 期间换文件/切项目：丢弃旧结果
        fileId = uploaded.data.id;
        flow.onUploaded(fileId);
      }
      const platforms = [...values.platforms].sort();
      const signature = `${fileId}|${values.title.trim()}|${platforms.join(',')}`;
      const created = await briefApi.register(projectId,
        { fileId, title: values.title.trim(), platforms }, flow.registerKeyFor(signature));
      if (generation !== generationRef.current) return;
      if (created.data.extractionStatus === 'invalid') {
        flow.resetRegister();
        flow.onFileChange(); // 解析失败：修正原件后重传属新上传操作
        setHasFile(false);
        setError(`文件已登记但解析失败：${created.data.error?.message ?? '无法解析'}。原件与登记保留可审计；请修正内容后重新选择文件上传（旧结果已作废）。`);
        return;
      }
      flow.onSuccess();
      queryClient.setQueryData<BriefBlocksCache>(['brief-blocks', created.data.briefId], {
        blocks: created.data.blocks ?? null, sourceVersionId: created.data.sourceVersionId,
        fileId: created.data.fileId, platforms: created.data.platforms ?? [],
      });
      await queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      if (inputRef.current) inputRef.current.value = '';
      setHasFile(false);
      navigate(`/projects/${projectId}/briefs/${created.data.briefId}`);
    } catch (e) {
      const c = classifyBriefError(e);
      setError(`${c.message}${c.fieldErrors ? `（${Object.values(c.fieldErrors).join('; ')}）` : ''}`
        + (c.kind === 'conflict' || c.kind === 'field' ? '｜已保留填写与已上传文件；重试复用同一登记键，不会重复上传' : ''));
    } finally {
      flow.endOperation();
      if (generation === generationRef.current) setBusy(false);
    }
  };

  return (
      <Card title="登记品牌 Brief（TXT / PDF）" className="panel-padding">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text type="secondary">
          PDF 读取**文本层**并保留真实物理页号；含图片或无法提取文本的页会明确列为"未覆盖页"，不计入已审核。
          旧 .doc / .docx 解析未接入，会以明确错误提示，不会静默成功。每次重新选择文件都会作为新上传登记，即使同名。
        </Typography.Text>
        <input ref={(el) => { inputRef.current = el; }} id="brief-file-input" type="file"
          accept=".txt,.pdf,text/plain,application/pdf"
          aria-label="选择 Brief 文件" disabled={busy} onChange={onPick} />
        <Form form={form} layout="vertical" disabled={busy} initialValues={{ platforms: [] }}>
          <Form.Item name="title" label="Brief 标题" rules={[{ required: true, whitespace: true, message: '标题不能为空' }]}>
            <Input maxLength={100} placeholder="例如：麦咖啡 小红书 Brief" />
          </Form.Item>
          <Form.Item name="platforms" label="适用平台（须为项目平台子集）" rules={[{ required: true, message: '至少选择一个平台' }]}>
            <Select mode="multiple" options={projectPlatforms.map((p) => ({ value: p, label: p }))} />
          </Form.Item>
        </Form>
        {error && <Alert type="error" showIcon message={error} />}
        <Button type="primary" icon={<UploadOutlined />} loading={busy} disabled={!hasFile} onClick={submit}>
          上传并登记
        </Button>
      </Space>
    </Card>
  );
}
