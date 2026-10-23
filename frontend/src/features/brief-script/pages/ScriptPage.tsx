// 脚本页（t-31）：路由 projectId/taskId 唯一事实 + 真实后端接线。
// 硬约束（A 11:03）：task.projectId 必须 == 路由 projectId，不匹配即停止加载并显示不存在，
// 不借正确 taskId 在错误项目下展示；历史选择写 URL ?version 且须属当前任务真实列表；
// 切任务/版本清旧输入与引用、忽略旧响应（react-query signal）；归档任务或归档项目=只读；
// 每次重选文件=新上传操作（不看 name/size）；useRef 稳定 flow + 同步 inflight；
// 提交重试复用 fileId/key；>50k 或空白不先上传也不创建；成功 invalidate 任务/列表/项目统计。
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { App, Alert, Breadcrumb, Button, Card, Empty, Input, Radio, Skeleton, Space, Table, Tag, Typography } from 'antd';
import { projectApi } from '../../projects/api';
import { uploadFile } from '../../../shared/api/client';
import { BriefUploadFlow } from '../api';
import {
  classifyBriefError, currentScriptVersion, KeyedOp, newKey, scriptApi, scriptCharCountText, scriptCoverageView,
  scriptContextGate, scriptCreateBody, taskApi, type ScriptCreate, type ScriptVersionSummary,
} from '../api';

import { WorkflowPanel } from '../../workflow/WorkflowPanel';
import { InlineScriptRules } from '../../workflow/SimpleReviewDrawer';
import { evidenceApi } from '../../video-model/api';
import { workflowApi } from '../../workflow/api';

const MAX_TEXT_CHARS = 50_000;

export function ScriptPage() {
  const { projectId = '', taskId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message } = App.useApp();
  const queryClient = useQueryClient();

  const taskQuery = useQuery({
    queryKey: ['task', taskId], queryFn: ({ signal }) => taskApi.get(taskId, signal),
    enabled: !!taskId,
  });
  const task = taskQuery.data?.data ?? null;
  // 归属硬校验：task 的项目必须等于路由项目，否则视为不存在（不跨项目借显）。
  const mismatch = !!task && task.projectId !== projectId;
  const projectQuery = useQuery({
    queryKey: ['project', projectId], queryFn: () => projectApi.detail(projectId), // shared helper 单参（不越权改）
    enabled: !!projectId && !!task && task.projectId === projectId,
  });
  // 修订2（A 11:16）：上下文门控=任务已成功 且 项目上下文有效 且 归属匹配（pending 时绝不提前拉脚本）。
  const contextReady = scriptContextGate(task, projectId, taskQuery.isSuccess, projectQuery.isSuccess);
  const versionsQuery = useQuery({
    queryKey: ['script-versions', taskId],
    queryFn: ({ signal }) => scriptApi.list(taskId, signal).then((r) => r.data),
    enabled: contextReady,
  });
  const versions = useMemo(() => versionsQuery.data ?? [], [versionsQuery.data]);
  const selected = searchParams.get('version');
  // 历史选择：URL ?version 必须命中当前任务真实列表；非法值（含切任务后的旧值）忽略并回落 isCurrent，
  // 不清空 URL（修订1：首挂/切任务都不无差别清参数，刷新恢复 ?version 不丢）。
  const selectedVersion = selected ? versions.find((v) => v.scriptVersionId === selected) ?? null : null;
  const active = selectedVersion ?? currentScriptVersion(versions);
  const models = useQuery({ queryKey: ['evidence-model-settings'], queryFn: evidenceApi.settings, enabled: contextReady });
  const workflow = useQuery({ queryKey: ['workflow', taskId], queryFn: () => workflowApi.state(taskId), enabled: contextReady });
  const [rulesDirty, setRulesDirty] = useState(false);
  const detailQuery = useQuery({
    queryKey: ['script-version-detail', active?.scriptVersionId],
    queryFn: ({ signal }) => scriptApi.detail(active!.scriptVersionId, signal).then((r) => r.data),
    enabled: !!active && contextReady,
  });

  // 切任务只重置输入/flow/key（首挂不清），URL 版本经真实列表验证自然失效。
  const prevTaskRef = useRef(taskId);
  const contextGenRef = useRef(0); // 上下文切换/卸载均递增，submit 内所有 await 后校验
  useEffect(() => {
    contextGenRef.current += 1;
    if (prevTaskRef.current !== taskId) {
      prevTaskRef.current = taskId;
      setPaste('');
      flowRef.current = new BriefUploadFlow(); // 新上下文新 flow；旧锁只由旧操作自己的 finally 释放
      submitKeyRef.current = null;
      setFileName(null);
      setSubmitError(null);
      setSubmitting(false); // 新视图不得残留旧 loading
      if (inputRef.current) inputRef.current.value = ''; // DOM 旧 file 必须清，防旧文件被当新任务提交
    }
    return () => { contextGenRef.current += 1; }; // 卸载后旧 generation 作废：不得继续登记/写新路由
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, projectId]);

  const readOnly = mismatch || task?.archivedAt != null || projectQuery.data?.data.status === 'archived';

  const [mode, setMode] = useState<'paste' | 'file'>('paste');
  const [paste, setPaste] = useState('');
  const flowRef = useRef<BriefUploadFlow | null>(null);
  flowRef.current ??= new BriefUploadFlow();
  const flow = flowRef.current;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const submitKeyRef = useRef<KeyedOp | null>(null);
  submitKeyRef.current ??= new KeyedOp(() => newKey());
  const [fileName, setFileName] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const submit = async () => {
    if (readOnly || !task || rulesDirty || !models.data?.data.hasKey || !workflow.data?.data.rulesReady) return;
    if (!flow.tryBegin()) return; // 同步 inflight 闸门（闭包捕获当次 render 的 flow：上下文切换后旧操作锁旧 flow）
    const generation = contextGenRef.current; // 固定上下文：路由/任务切换后旧结果全部作废
    const boundTaskId = task.id;
    setSubmitting(true);
    setSubmitError(null);
    try {
      let body: ScriptCreate;
      if (mode === 'paste') {
        const text = paste;
        if (!text.trim()) { setSubmitError('粘贴内容为空白：不创建版本（前端先拦，与后端 400 同语义）'); return; }
        if (text.length > MAX_TEXT_CHARS) { setSubmitError(`粘贴内容 ${text.length} 字超过 ${MAX_TEXT_CHARS} 上限：不创建版本`); return; }
        body = scriptCreateBody({ inputType: 'text', text }); // 互斥合法：fileId 显式 null
      } else {
        const file = inputRef.current?.files?.[0];
        if (!file) { setSubmitError('请选择脚本 TXT 文件'); return; }
        let fileId = flow.currentFileId;
        if (!fileId) {
          const uploaded = await uploadFile(file, 'script', flow.uploadingKey);
          if (generation !== contextGenRef.current) return; // 旧上传回包不覆盖新任务
          fileId = uploaded.data.id;
          flow.onUploaded(fileId);
        }
        body = scriptCreateBody({ inputType: 'file', fileId });
      }
      const signature = `${boundTaskId}:${body.inputType}:${body.text ?? body.fileId}:${models.data.data.revision}`;
      const key = submitKeyRef.current!.ensure(signature);
      const submitted = await scriptApi.submitAndReview(boundTaskId, { ...body, configRevision: models.data.data.revision, allowRealCall: true }, key);
      const created = { data: submitted.data.script };
      if (generation !== contextGenRef.current) {
        // 上下文已切换：结果不回写新 UI；但成功登记不可撤回——仅刷新对应旧 key 缓存并如实提示。
        queryClient.invalidateQueries({ queryKey: ['script-versions', boundTaskId] });
        message.info(`后台任务 ${boundTaskId.slice(0, 6)}… 的登记已成功，已刷新其历史（当前视图未受影响）`);
        return;
      }
      submitKeyRef.current!.reset(); // 成功清：下一次为新逻辑操作；失败保留同 key 重试
      flow.onSuccess();
      if (inputRef.current) inputRef.current.value = '';
      setFileName(null); setPaste('');
      // 成功后刷新任务上下文/历史列表/项目统计（阶段已回 script；新当前版本可能 invalid，不被旧 ready 顶替）
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['script-versions', boundTaskId] }),
        queryClient.invalidateQueries({ queryKey: ['task', boundTaskId] }),
        queryClient.invalidateQueries({ queryKey: ['workflow', boundTaskId] }),
        queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
      ]);
      // 承诺兑现：每个 await 后校验——刷新期间路由切换则不回写新 UI（旧缓存刷新已完成即可）。
      if (generation !== contextGenRef.current) return;
      setSelectedVersion(created.data.scriptVersionId);
      if (submitted.data.autoReviewError) setSubmitError(`脚本已保存：${submitted.data.autoReviewError}`);
      else if (submitted.data.autoReview?.status === 'succeeded') message.success('脚本已提交，AI 审核反馈已返回');
      else message.warning('脚本已保存，AI 审核未完成，请查看下方原因');
    } catch (e) {
      if (generation !== contextGenRef.current) return; // 旧上下文错误不打扰新任务
      const c = classifyBriefError(e);
      setSubmitError(`提交失败（${c.kind}）：${c.message}${c.kind === 'conflict' ? '（重试复用同一幂等键，不重复创建）' : ''}`);
    } finally {
      flow.endOperation();
      if (generation === contextGenRef.current) setSubmitting(false); // 旧上下文不清新 UI 态
    }
  };

  const setSelectedVersion = (id: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('version', id);
    setSearchParams(next, { replace: true });
  };

  if (taskQuery.isPending) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (taskQuery.isError || !task || mismatch) {
    return <Alert type="error" showIcon
      message={mismatch ? '任务不存在或不属于当前项目' : '任务不存在'}
      description="路由 projectId/taskId 与真实归属不匹配时停止加载脚本（不借正确 taskId 在错误项目下显示）。" />;
  }

  return <>
    <Breadcrumb items={[{ title: <Link to="/projects">项目列表</Link> },
      { title: <Link to={`/projects/${projectId}`}>项目详情</Link> },
      { title: <Link to={`/projects/${projectId}/tasks/${taskId}`}>{task.creatorName}</Link> },
      { title: '脚本' }]} />
    <Card size="small" style={{ margin: '12px 0' }}>
      <Space wrap>
        <Typography.Text strong>{task.creatorName}</Typography.Text>
        <Tag>{task.platform}</Tag>
        <Tag>{task.contentType}</Tag>
        <Tag color="blue">阶段 {task.stage}</Tag>
        {task.archivedAt && <Tag color="orange">已归档（只读）</Tag>}
        {projectQuery.data?.data.status === 'archived' && <Tag color="orange">项目已归档（只读）</Tag>}
      </Space>
    </Card>
    {contextReady && <InlineScriptRules taskId={taskId} projectId={projectId} onDirtyChange={setRulesDirty} />}
    <div className="review-grid">
      <Card title="提交脚本并自动审核">
        <p className="muted">点击提交后，平台会使用已保存的规则与模型自动审核并返回反馈，可能产生模型费用。</p>
        {rulesDirty && <Alert type="info" title="规则有未保存修改，请先保存审核规则" />}
        {!models.data?.data.hasKey && <p><Link to="/settings/models">请先配置模型服务与 Key</Link></p>}
        {!workflow.data?.data.rulesReady && <Alert type="info" title="先在上方保存审核规则，即可提交脚本自动审核" />}
        {readOnly ? (
          <Alert type="warning" showIcon message="只读：任务或项目已归档"
            description="历史版本仍可读；新登记由后端 TASK_ARCHIVED 强制拒绝（不只前端判断）。" />
        ) : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Radio.Group value={mode} onChange={(e) => { setMode(e.target.value); setSubmitError(null); }}
              options={[{ value: 'paste', label: '粘贴文本' }, { value: 'file', label: '上传 TXT / XLSX / DOCX' }]} />
            {mode === 'paste' ? (
              <Input.TextArea rows={6} placeholder={`脚本正文（≤${MAX_TEXT_CHARS} 字；空白不提交）`}
                value={paste} onChange={(e) => setPaste(e.target.value)} />
            ) : (
              <input ref={inputRef} type="file"
                accept=".txt,.xlsx,.docx,text/plain,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                aria-label="选择脚本文件" disabled={submitting}
                onChange={() => {
                  // 每次重选=新上传操作（不看 name/size 判同）；重置 flow 防复用旧 fileId/key
                  flow.onFileChange();
                  setFileName(inputRef.current?.files?.[0]?.name ?? null);
                  setSubmitError(null);
                }} />
            )}
            {submitError && <Alert type="error" showIcon message={submitError} />}
            <Button type="primary" loading={submitting}
              disabled={rulesDirty || !models.data?.data.hasKey || !workflow.data?.data.rulesReady || (mode === 'paste' ? !paste.trim() || paste.length > MAX_TEXT_CHARS : !fileName)}
              onClick={() => void submit()}>
              {submitting ? '正在提交并审核…' : '提交脚本并自动审核'}
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              支持 TXT、XLSX、DOCX。旧版 .doc 请先另存为 .docx。
            </Typography.Text>
          </Space>
        )}
      </Card>
      <Card title="脚本历史与详情">
        {versionsQuery.isPending ? <Skeleton active />
          : versions.length === 0 ? <Empty description="该任务尚无脚本版本（空态，非加载中）" />
          : <>
            <Table<ScriptVersionSummary> rowKey="scriptVersionId" size="small" pagination={false}
              dataSource={versions}
              onRow={(r) => ({ onClick: () => setSelectedVersion(r.scriptVersionId), style: { cursor: 'pointer' } })}
              columns={[
                { title: '版本', dataIndex: 'versionLabel', width: 70, render: (v, r) => <Space>
                  <Typography.Text>{v}</Typography.Text>{r.isCurrent && <Tag color="green">当前</Tag>}
                </Space> },
                { title: '输入', dataIndex: 'inputType', width: 90, render: (v) => v === 'file' ? '文件' : '粘贴' },
                { title: '解析', dataIndex: 'extractionStatus', width: 90,
                  render: (v) => <Tag color={v === 'ready' ? 'green' : 'orange'}>{v === 'ready' ? '就绪' : '失败'}</Tag> },
                { title: '字符', width: 150, render: (_, r) => scriptCharCountText(r) },
                { title: '时间', dataIndex: 'createdAt', width: 160 },
              ]} />
            {active && (detailQuery.data ? (
              <div style={{ marginTop: 12 }}>
                <Typography.Paragraph>
                  <Typography.Text strong>{active.versionLabel}</Typography.Text>
                  {' · '}解析{detailQuery.data.extractionStatus === 'ready' ? '就绪' : `失败（不可作为审核输入）`}
                  {detailQuery.data.inputType === 'file' && ` · 来源文件 ${detailQuery.data.fileId}`}
                  {` · 段落 ${detailQuery.data.sourceMap?.length ?? '未提取'}`}
                </Typography.Paragraph>
                {detailQuery.data.extractionStatus !== 'ready' && (
                  <Alert type="warning" showIcon message="该版本解析失败，历史保留可见" style={{ marginBottom: 8 }} />)}
                {/* t-38：覆盖度必须醒目——partial/unknown 不得被当作"文本完整"。
                    数据取自**该版本自己的** detail.parseMeta（detailQuery 按 active.scriptVersionId 查询，不拿当前版替换）。 */}
                {(() => {
                  const view = scriptCoverageView(detailQuery.data.parseMeta);
                  const verLabel = detailQuery.data.versionLabel;
                  if (view.level === 'full') {
                    return <Alert type="success" showIcon style={{ marginBottom: 8 }}
                      message={`${verLabel} 文本解析完整（未发现未纳入的图片/嵌入内容）`} />;
                  }
                  if (view.level === 'partial') {
                    // 文案不写死"图片"：未覆盖可能是图片、无缓存公式、超限截断等多种原因，
                    // 只如实汇总并逐条列出 warnings（A 13:53④）。
                    const parts: string[] = [];
                    if (view.mediaCount > 0) parts.push(`${view.mediaCount} 处图片/嵌入内容`);
                    const other = view.warnings.filter((w) => !/图片/.test(w)).length;
                    if (other > 0) parts.push(`${other} 项其他原因`);
                    return <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                      message={`${verLabel} 文本解析不完整${parts.length ? `：${parts.join('、')}` : ''}`}
                      description={<Space direction="vertical" size={2} style={{ width: '100%' }}>
                        {view.locations.length > 0 && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            未覆盖位置：{view.locations.join('；')}
                          </Typography.Text>)}
                        {view.warnings.map((w, i) => (
                          <Typography.Text key={i} type="secondary" style={{ fontSize: 12 }}>{w}</Typography.Text>))}
                      </Space>} />;
                  }
                  return <Alert type="info" showIcon style={{ marginBottom: 8 }}
                    message={`${verLabel} 覆盖度未知（该版本无解析元数据，不代表文本完整）`}
                    description="本条为历史版本或非真实格式，请勿据此认定全文已审核。" />;
                })()}
                {detailQuery.data.text != null && (
                  <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}>
                    {detailQuery.data.text}
                  </Typography.Paragraph>
                )}
                <Table size="small" rowKey="paragraphId" pagination={false}
                  dataSource={detailQuery.data.sourceMap ?? []}
                  locale={{ emptyText: detailQuery.data.extractionStatus === 'ready' ? '无段落数据' : '解析失败，无段落'}}
                  columns={[
                    { title: '段', dataIndex: 'paragraphId', width: 60 },
                    { title: '来源位置', width: 170, render: (_, b) => b.location
                      ?? (b.startOffset === 0 && b.endOffset === 0 ? '—' : `${b.startOffset}–${b.endOffset}`) },
                    { title: '文本', dataIndex: 'text' },
                  ]} />
              </div>
            ) : detailQuery.isError ? <Alert type="error" showIcon message="版本详情读取失败" /> : null)}
          </>}
      </Card>
    </div>
    {contextReady && <WorkflowPanel key={`${taskId}-${active?.scriptVersionId || 'empty'}`} taskId={taskId} projectId={projectId} scope="script" selectedScriptVersionId={active?.scriptVersionId} />}
  </>;
}
