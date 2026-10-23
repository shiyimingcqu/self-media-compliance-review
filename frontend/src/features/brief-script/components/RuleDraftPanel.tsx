// 规则草稿/确认/停用/冲突/启用（t-26 三轮，A 02:37 口径）。
// 执行模型：所有写操作经 OperationRunner——inflight 闸门防连点；提交发生在 modal onOk 的异步内
// （成功 await 刷新真值；失败保留弹窗与草稿、返回 rejected 使 modal 不关闭；key 不 reset 供同 payload 重试）。
// 冲突解决必须由用户显式选择停用哪一方并展示双方原文；登记冲突说明必填，不自动补文案。
import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { App, Alert, Badge, Button, Card, Form, Input, Radio, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { briefApi, buildSource, enableBlockers, OperationRunner, ruleApi,
  type RequirementView, type SourceContext, type VersionDetail, type PreviewBlock } from '../api';

const CATEGORIES = ['产品事实', '卖点', '画面', '口播', '字幕', '禁用表达', '发布材料', '其他'];
const PLATFORM_LABEL: Record<string, string> = { douyin: '抖音', xiaohongshu: '小红书', bilibili: 'B站' };
const CONFIRMATION_TAG: Record<string, { color: string; text: string }> = {
  pending: { color: 'orange', text: '待确认' },
  confirmed: { color: 'green', text: '已确认' },
  disabled: { color: 'default', text: '已停用' },
};

type Props = {
  detail: VersionDetail;
  versionId: string;
  briefPlatforms: string[];
  blocks: PreviewBlock[] | null; // 持久 preview 命中块（未就绪=null，不拿登记缓存代验证引用）
  sourceContext: SourceContext | null;
  /** t-41：该源未覆盖页（page:N）——未全部签署时「确认并启用」禁用并列出缺页。 */
  uncovered?: string[];
  onVersionCreated: (childId: string) => Promise<void> | void;
};

const reqOf = (detail: VersionDetail, id: string) =>
  (detail.requirements ?? []).find((r) => r.id === id);

export function RuleDraftPanel({ detail, versionId, briefPlatforms, blocks, sourceContext, uncovered = [], onVersionCreated }: Props) {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [draftText, setDraftText] = useState<string | null>(null); // 409 后保留用户输入
  const runnerRef = useRef<OperationRunner | null>(null);
  runnerRef.current ??= new OperationRunner();
  const runner = runnerRef.current;

  const revision = detail.version.revision;
  const readOnly = detail.version.state === 'enabled' || detail.version.state === 'superseded';
  const [selected, setSelected] = useState<string[]>([]);
  const [reqForm] = Form.useForm();
  const blockers = enableBlockers(detail, uncovered);
  const openConflicts = (detail.conflicts ?? []).filter((c) => c.state === 'open');

  const refreshTruth = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['rule-version', versionId] }),
      queryClient.invalidateQueries({ queryKey: ['brief-rule-versions', detail.version.briefId] }),
    ]);
  };
  const onError = (kind: string, msg: string) => {
    setError(kind === 'conflict'
      ? `${msg}（服务端真值即将刷新；你的输入已保留，核对后再次提交将按新状态使用新幂等键）`
      : `操作失败（${kind}）：${msg}`);
  };

  const addRequirement = async () => {
    const values = await reqForm.validateFields();
    const mode = values.sourceMode === 'manual' ? 'manual' : 'paragraph';
    if (mode === 'paragraph' && !sourceContext) {
      setError('文档引用不可用：所选版本绑定源无法从真值恢复（缺按版本读取接口），请改用品牌补充');
      return;
    }
    const picked = mode === 'paragraph' ? (blocks ?? []).find((b) => b.paragraphId === values.paragraphId) : undefined;
    if (mode === 'paragraph' && !picked) { setError('请选择有效引用段落'); return; }
    const source = mode === 'manual'
      ? buildSource({ mode, manualNote: values.manualNote })
      : buildSource({ mode, fileId: sourceContext!.fileId, paragraphId: values.paragraphId, quote: picked!.text });
    const body = { text: values.text.trim(), category: values.category, importance: values.importance,
      platforms: values.platforms, contentTypes: values.contentTypes, phases: values.phases,
      reviewStages: values.reviewStages, exceptions: values.exceptions ?? '', source };
    await runner.run('add', (key) => ruleApi.add(versionId, body, key, revision).then((r) => r.data), {
      signature: `${versionId}:${revision}:${JSON.stringify(body)}`,
      onRefresh: refreshTruth, onError,
    });
    reqForm.resetFields();
  };

  const confirmSelected = async () => {
    const ids = [...selected].sort();
    const result = await runner.run('confirm', (key) => ruleApi.confirm(versionId, ids, key, revision).then((r) => r.data), {
      signature: `${versionId}:${revision}:${ids.join(',')}`, onRefresh: refreshTruth, onError,
    });
    if (result) {
      const skipped = result.skipped ?? [];
      message.info(`确认 ${result.confirmedIds?.length ?? 0} 条${skipped.length ? `，跳过 ${skipped.length}：${skipped.map((s) => `${s.id.slice(0, 4)}…(${s.reason})`).join('；')}` : ''}`);
      setSelected([]);
    }
  };

  const disableOne = (r: RequirementView) => {
    let reason = '';
    modal.confirm({ title: `停用要求（确认后该条不再适用）`, icon: null,
      content: <Input placeholder="停用理由（必填，1–500 字）" onChange={(e) => { reason = e.target.value; }} />,
      okText: '确认停用',
      onOk: async () => {
        if (!reason.trim()) { message.error('理由必填'); return Promise.reject(new Error('reason-required')); }
        const done = await runner.run(`disable:${r.id}`, (key) =>
          ruleApi.disable(versionId, r.id, reason.trim(), key, revision).then((x) => x.data),
          { signature: `${versionId}:${revision}:${r.id}:${reason.trim()}`, onRefresh: refreshTruth, onError });
        if (!done) return Promise.reject(new Error('keep-modal-open')); // 失败保留弹窗与输入
      } });
  };

  const patchOne = (r: RequirementView) => {
    let text = draftText ?? r.text;
    modal.confirm({ title: '编辑要求（语义变更后该条回到待确认）', icon: null,
      content: <Input.TextArea defaultValue={text} rows={3}
        onChange={(e) => { text = e.target.value; setDraftText(text); }} />,
      okText: '保存',
      onOk: async () => {
        const done = await runner.run(`patch:${r.id}`, (key) =>
          ruleApi.patch(r.id, { text: text.trim() }, key, revision).then((x) => x.data),
          { signature: `${r.id}:${revision}:${text.trim()}`, onRefresh: refreshTruth, onError });
        if (!done) return Promise.reject(new Error('keep-modal-open'));
        setDraftText(null);
      } });
  };

  const addConflict = () => {
    let explanation = '';
    modal.confirm({ title: `登记冲突（已选 ${selected.length} 条）`, icon: null,
      content: <Input.TextArea placeholder="冲突说明（必填，不得自动生成）" rows={3}
        onChange={(e) => { explanation = e.target.value; }} />,
      okText: '登记',
      onOk: async () => {
        if (!explanation.trim()) { message.error('冲突说明必填'); return Promise.reject(new Error('need-explanation')); }
        const ids = [...selected].sort();
        const done = await runner.run('conflict', (key) => ruleApi.addConflict(versionId,
          { requirementIds: ids, explanation: explanation.trim() }, key, revision).then((x) => x.data),
          { signature: `${versionId}:${revision}:${ids.join(',')}:${explanation.trim()}`, onRefresh: refreshTruth, onError });
        if (!done) return Promise.reject(new Error('keep-modal-open'));
        setSelected([]);
      } });
  };

  // 冲突解决（A 02:37③）：展示双方原文/ID，用户显式单选"保留哪条/停用哪条"；不自动 slice(1)。
  const resolveConflict = (conflictId: string, involved: string[]) => {
    const candidates = involved.map((id) => reqOf(detail, id)).filter((r): r is RequirementView => !!r);
    if (candidates.length < 2) { setError('冲突关联要求已不可见，请在列表刷新后重试'); return; }
    let keepId: string | null = null;
    let resolution = '';
    modal.confirm({ title: '解决冲突（显式选择停用项，不自动决定）', icon: null, width: 640,
      content: <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text type="secondary">选择保留的要求，另一方将被停用（理由入审计）：</Typography.Text>
        <Radio.Group onChange={(e) => { keepId = e.target.value as string; }}
          style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {candidates.map((r) => (
            <Radio key={r.id} value={r.id}>
              <Space direction="vertical" size={0}>
                <Typography.Text>{r.text}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {r.id.slice(0, 8)}… · {PLATFORM_LABEL[r.platforms[0]] ?? r.platforms[0]} · {r.confirmation}
                </Typography.Text>
              </Space>
            </Radio>
          ))}
        </Radio.Group>
        <Input.TextArea placeholder="解决口径与理由（必填）" rows={2} onChange={(e) => { resolution = e.target.value; }} />
      </Space>,
      okText: '解决',
      onOk: async () => {
        if (!keepId) { message.error('请选择保留哪一条'); return Promise.reject(new Error('need-choice')); }
        if (!resolution.trim()) { message.error('理由必填'); return Promise.reject(new Error('need-reason')); }
        const disableIds = involved.filter((id) => id !== keepId);
        const changes = disableIds.map((id) => ({ requirementId: id, action: 'disable' as const }));
        const done = await runner.run(`resolve:${conflictId}`, (key) => ruleApi.resolveConflict(conflictId,
          { resolution: resolution.trim(), affectedRequirementChanges: changes }, key, revision).then((x) => x.data),
          { signature: `${conflictId}:${revision}:${keepId}:${resolution.trim()}`, onRefresh: refreshTruth, onError });
        if (!done) return Promise.reject(new Error('keep-modal-open'));
      } });
  };

  const doEnable = () => {
    // t-41：三类门禁分别提示（待确认 / 未解决冲突 / 未覆盖页未签署），不合成长文案
    if (blockers.pending.length || blockers.openConflicts.length || blockers.missingCoverage.length) {
      const parts: string[] = [];
      if (blockers.pending.length) parts.push(`待确认 ${blockers.pending.length} 条`);
      if (blockers.openConflicts.length) parts.push(`未解决冲突 ${blockers.openConflicts.length} 项`);
      if (blockers.missingCoverage.length) {
        const pages = blockers.missingCoverage.map((u) => {
          const m = /^page:(\d+)$/.exec(u);
          return m ? `第 ${m[1]} 页` : u;
        });
        parts.push(`未覆盖页未签署 ${blockers.missingCoverage.length} 页（${pages.join('、')}）`);
      }
      setError(`门禁未齐：${parts.join('；')}`);
      return;
    }
    let summary = '';
    modal.confirm({ title: '确认并启用（不可变快照；不改其他版本与任务绑定）', icon: null,
      content: <Input.TextArea placeholder="变更摘要（必填）" onChange={(e) => { summary = e.target.value; }} />,
      okText: '启用',
      onOk: async () => {
        if (!summary.trim()) { message.error('摘要必填'); return Promise.reject(new Error('need-summary')); }
        const done = await runner.run('enable', (key) => ruleApi.enable(versionId, summary.trim(), key, revision)
          .then((x) => x.data),
          { signature: `${versionId}:${revision}:${summary.trim()}:${detail.requirements.map((r) => r.confirmation).join(',')}`,
            onRefresh: refreshTruth, onError });
        if (!done) return Promise.reject(new Error('keep-modal-open'));
        message.success('已启用，版本转为只读快照');
      } });
  };

  const deriveDraft = async () => {
    const bind = detail.version.sourceVersionId;
    const child = await runner.run('derive', (key) =>
      briefApi.createVersion(detail.version.briefId, { sourceVersionId: bind, parentId: versionId }, key),
      { signature: `derive:${versionId}:${bind}`, onRefresh: refreshTruth, onError });
    if (child) onVersionCreated(child.data.id);
  };

  const busy = runner.anyBusy();
  return (
    <Card title={`审核要求 · ${detail.version.versionLabel}（${detail.version.state}，revision ${revision}）`}
      extra={<Space>
        <Tooltip title="模型规则提取未接入，仅人工补充/编辑">
          <Button disabled icon={<Badge status="processing" />}>AI 提取（未接入）</Button>
        </Tooltip>
        {readOnly ? <Button disabled={busy} onClick={deriveDraft}>派生新草稿</Button> : <>
          <Button disabled={selected.length < 2 || busy} onClick={addConflict}>登记冲突</Button>
          <Button disabled={selected.length === 0 || busy} onClick={confirmSelected}>批量确认（{selected.length}）</Button>
          <Button type="primary" disabled={busy} onClick={doEnable}>确认并启用</Button>
        </>}
      </Space>}>
      {!readOnly && (blockers.pending.length > 0 || blockers.openConflicts.length > 0
        || blockers.missingCoverage.length > 0) && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message={<ul style={{ margin: 0, paddingLeft: 18 }}>
            {blockers.pending.length > 0 && <li>待确认要求 {blockers.pending.length} 条</li>}
            {blockers.openConflicts.length > 0 && <li>未解决冲突 {blockers.openConflicts.length} 项</li>}
            {blockers.missingCoverage.length > 0 && (
              <li>未覆盖页未签署 {blockers.missingCoverage.length} 页（{blockers.missingCoverage
                .map((u) => { const m = /^page:(\d+)$/.exec(u); return m ? `第 ${m[1]} 页` : u; })
                .join('、')}）</li>)}
          </ul>} />)}
      {!sourceContext && !readOnly && (
        <Alert type="info" showIcon style={{ marginBottom: 8 }}
          message="文档引用暂不可用：所选版本绑定源无法从真值恢复（缺读取接口）；来源仅可用品牌补充，派生取 detail 真值源" />)}
      {error && <Alert type="error" showIcon closable style={{ marginBottom: 8 }} message={error} onClose={() => setError(null)} />}
      {openConflicts.map((c) => (
        <Alert key={c.id} type="warning" showIcon style={{ marginBottom: 8 }}
          message={`冲突：${(c.requirementIds ?? []).map((x) => x.slice(0, 6)).join(' ↔ ')}`}
          description={<Space><span>{c.explanation}</span>
            <Button size="small" disabled={busy} onClick={() => resolveConflict(c.id, c.requirementIds ?? [])}>解决</Button></Space>} />))}
      <Table<RequirementView> rowKey="id" size="small" pagination={false}
        dataSource={detail.requirements ?? []}
        rowSelection={readOnly ? undefined : { selectedRowKeys: selected, onChange: (k) => setSelected(k as string[]) }}
        columns={[
          { title: '要求', dataIndex: 'text', render: (_, r) => <Space direction="vertical" size={0}>
            <Typography.Text>{r.text}</Typography.Text>
            {r.source?.manualNote ? <Tag color="blue">品牌补充</Tag> : <Tag>文档来源 {r.source?.paragraphId ?? ''}</Tag>}
          </Space> },
          { title: '类别', dataIndex: 'category', width: 100 },
          { title: '重要性', dataIndex: 'importance', width: 80, render: (v) => v === 'must' ? '必须' : '建议' },
          { title: '平台', dataIndex: 'platforms', width: 140, render: (v: string[]) => (v ?? []).map((p) => PLATFORM_LABEL[p] ?? p).join('/') },
          { title: '状态', dataIndex: 'confirmation', width: 90, render: (v: string) => { const t = CONFIRMATION_TAG[v]; return <Tag color={t?.color}>{t?.text ?? v}</Tag>; } },
          ...(readOnly ? [] : [{ title: '操作', width: 150, render: (_: unknown, r: RequirementView) => <Space>
            <Typography.Link onClick={() => patchOne(r)}>编辑</Typography.Link>
            {r.confirmation !== 'disabled' && <Typography.Link onClick={() => disableOne(r)}>停用</Typography.Link>}
          </Space> }]),
        ]} />
      {!readOnly && (
        <Form form={reqForm} layout="vertical" style={{ marginTop: 16 }} disabled={busy}
          initialValues={{ importance: 'must', sourceMode: sourceContext && blocks ? 'paragraph' : 'manual',
            contentTypes: ['video'], phases: ['general'], reviewStages: ['script', 'video'], platforms: briefPlatforms }}>
          <Typography.Title level={5}>人工补充要求</Typography.Title>
          <Form.Item name="text" label="要求内容（1–500 字）" rules={[{ required: true, whitespace: true }, { max: 500 }]}>
            <Input.TextArea rows={2} />
          </Form.Item>
          <Space wrap size={12}>
            <Form.Item name="category" label="类别" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} options={CATEGORIES.map((c) => ({ value: c, label: c }))} />
            </Form.Item>
            <Form.Item name="importance" label="重要性" rules={[{ required: true }]}>
              <Radio.Group options={[{ value: 'must', label: '必须' }, { value: 'suggested', label: '建议' }]} />
            </Form.Item>
            <Form.Item name="platforms" label="适用平台（⊆Brief）" rules={[{ required: true }]}>
              <Select mode="multiple" style={{ minWidth: 200 }} options={briefPlatforms.map((p) => ({ value: p, label: PLATFORM_LABEL[p] ?? p }))} />
            </Form.Item>
            <Form.Item name="contentTypes" label="内容形式" rules={[{ required: true }]}>
              <Select mode="multiple" style={{ minWidth: 170 }} options={[{ value: 'video', label: '视频' }, { value: 'image_text', label: '图文' }, { value: 'general', label: '通用' }]} />
            </Form.Item>
            <Form.Item name="phases" label="活动阶段" rules={[{ required: true }]}>
              <Select mode="multiple" style={{ minWidth: 170 }} options={[{ value: 'warmup', label: '预热' }, { value: 'seeding', label: '种草' }, { value: 'general', label: '通用' }]} />
            </Form.Item>
            <Form.Item name="reviewStages" label="检查阶段" rules={[{ required: true }]}>
              <Select mode="multiple" style={{ minWidth: 190 }} options={[{ value: 'script', label: '脚本' }, { value: 'video', label: '视频' }, { value: 'human', label: '人工' }]} />
            </Form.Item>
          </Space>
          <Form.Item name="sourceMode" label="来源类型" rules={[{ required: true }]}>
            <Radio.Group options={[{ value: 'paragraph', label: '引用段落（需登记 blocks 与版本源可恢复）', disabled: !(sourceContext && blocks) }, { value: 'manual', label: '品牌人工补充' }]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(a, b) => a.sourceMode !== b.sourceMode}>
            {({ getFieldValue }) => getFieldValue('sourceMode') === 'paragraph' ? (
              <Form.Item name="paragraphId" label="引用段落" rules={[{ required: true }]}>
                <Select options={(blocks ?? []).map((b) => ({ value: b.paragraphId, label: `${b.paragraphId}：${b.text.slice(0, 24)}…` }))} />
              </Form.Item>
            ) : (
              <Form.Item name="manualNote" label="品牌补充说明（不得携带文档坐标）" rules={[{ required: true, whitespace: true }]}>
                <Input.TextArea rows={2} />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item name="exceptions" label="例外条件（≤300 字）"><Input maxLength={300} /></Form.Item>
          <Button type="primary" ghost loading={runner.isBusy('add')} onClick={() => void addRequirement()}>添加要求</Button>
        </Form>
      )}
    </Card>
  );
}
