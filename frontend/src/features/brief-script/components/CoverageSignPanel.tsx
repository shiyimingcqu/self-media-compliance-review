// t-41 补录签署面板 —— 第一步：仅渲染 + 表单状态（API 调用与刷新在第二步接线）。
//
// 语义（A 16:18 口径）：
// - uncovered 页来自该 source 的持久投影（形如 'page:N'），每页必须独立签署；
// - 每页需 1–500 字补录说明 + 挂靠一条「同版本、非 disabled、source.manualNote 非空」的
//   品牌人工补充 requirement（与后端 validate_source 的 manualNote 语义一致）；
// - 无可用补充 requirement 时，引导先建品牌补充，**不自动生成**；
// - 未全部签署时提交按钮禁用，并列出缺哪几页。
import { useMemo, useState } from 'react';
import { Alert, Button, Card, Empty, Input, Select, Space, Tag, Typography } from 'antd';
import type { RequirementView } from '../api';

/** 提交给父组件的最小载荷（页号与映射由父组件组装为 CoverageBody）。 */
export interface CoverageSignItem {
  uncoveredSourceId: string;   // 与 uncovered 项逐字一致（含 'page:' 前缀）
  statement: string;           // 该页补录说明（1–500 字，已 trim）→ itemMappings[].note
  requirementId: string;       // 挂靠的人工补充 requirement
}

/** 顶层字段（后端 CoverageBody 必填，二者 1–500 字）：整段签署声明与补录原因。
 * 不静默写死文案——由用户在面板填写，父组件原样透传。 */
export interface CoverageSignHeader {
  statement: string;
  reason: string;
}

type Props = {
  versionId: string;
  revision: number;
  uncovered: string[];
  requirements: RequirementView[];
  /** statement=整段签署声明；reason=为何需要补录（均必填 1–500 字）。 */
  onSubmit: (items: CoverageSignItem[], header: CoverageSignHeader) => void | Promise<void>;
  busy?: boolean;
};

/**
 * 品牌人工补充来源说明（manualNote）。
 * 生成类型的 source 未声明具体键，这里做类型安全读取——只认非空字符串，不假设形状。
 */
function manualNoteOf(r: RequirementView): string | null {
  const source = (r as { source?: Record<string, unknown> | null }).source;
  const note = source?.manualNote;
  return typeof note === 'string' && note.trim() ? note.trim() : null;
}

/** 'page:20' → '第 20 页'；非标准项原样显示（不猜格式）。 */
export function uncoveredPageLabel(id: string): string {
  const m = /^page:(\d+)$/.exec(id);
  return m ? `第 ${m[1]} 页` : id;
}

export function CoverageSignPanel({ versionId, revision, uncovered, requirements, onSubmit, busy = false }: Props) {
  const [entries, setEntries] = useState<Record<string, { statement: string; requirementId: string | null }>>({});
  const [header, setHeader] = useState<CoverageSignHeader>({ statement: '', reason: '' });
  const [error, setError] = useState<string | null>(null);

  // 页号去重但保持后端给定顺序（不重排，便于与 uncoveredSourceIds 严格一一对应）
  const pages = useMemo(() => Array.from(new Set(uncovered ?? [])), [uncovered]);
  const candidates = useMemo(
    () => (requirements ?? []).filter((r) => r.confirmation !== 'disabled' && manualNoteOf(r) !== null),
    [requirements],
  );

  const entryOf = (page: string) => entries[page] ?? { statement: '', requirementId: null };
  const setEntry = (page: string, patch: Partial<{ statement: string; requirementId: string | null }>) => {
    setEntries((prev) => {
      const cur = prev[page] ?? { statement: '', requirementId: null };
      return { ...prev, [page]: { ...cur, ...patch } };
    });
  };

  // 每页 note：提交前 trim 后必须 1–500 字（空白不算已签）
  const incomplete = pages.filter((p) => {
    const e = entries[p];
    const note = (e?.statement ?? '').trim();
    return note.length < 1 || note.length > 500 || !e?.requirementId;
  });

  // 顶层必填（后端 CoverageBody）：各自 trim 后 1–500 字
  const headerMissing: string[] = [];
  if (header.statement.trim().length < 1) headerMissing.push('签署声明');
  else if (header.statement.trim().length > 500) headerMissing.push('签署声明（超 500 字）');
  if (header.reason.trim().length < 1) headerMissing.push('补录原因');
  else if (header.reason.trim().length > 500) headerMissing.push('补录原因（超 500 字）');

  const canSubmit = pages.length > 0 && candidates.length > 0
    && incomplete.length === 0 && headerMissing.length === 0 && !busy;

  const submit = () => {
    if (candidates.length === 0) {
      setError('本版本没有可挂靠的品牌人工补充要求：请先在下方「人工补充要求」中创建（来源选"品牌人工补充"），再回来签署。');
      return;
    }
    if (headerMissing.length > 0) {
      setError(`请先填写：${headerMissing.join('、')}`);
      return;
    }
    if (incomplete.length > 0) {
      setError(`还有 ${incomplete.length} 页未完成签署：${incomplete.map(uncoveredPageLabel).join('、')}`);
      return;
    }
    setError(null);
    void onSubmit(pages.map((p) => {
      const e = entries[p]!;
      return {
        uncoveredSourceId: p,
        statement: e.statement.trim(),   // note：trim 后 1–500 字
        requirementId: e.requirementId!,
      };
    }), { statement: header.statement.trim(), reason: header.reason.trim() });
  };

  return (
    <Card
      title={`未覆盖页补录签署 · ${versionId.slice(0, 8)}…（revision ${revision}）`}
      extra={pages.length > 0
        ? <Tag color={incomplete.length === 0 ? 'green' : 'orange'}>
          {pages.length - incomplete.length}/{pages.length} 页已填写
        </Tag>
        : null}
    >
      {pages.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="该源无未覆盖页：文本层已完整纳入审核，无需补录签署。"
        />
      ) : (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`含 ${pages.length} 页未纳入文本审核（图片/扫描页）`}
            description={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                每页需填写补录说明（1–500 字）并挂靠一条品牌人工补充要求；未全部签署不能启用版本。
                文本层可读不代表这些页的规则已被覆盖。
              </Typography.Text>
            }
          />

          {candidates.length === 0 && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="暂无可挂靠的品牌人工补充要求"
              description="请先在下方「人工补充要求」表单中创建（来源类型选「品牌人工补充」并填写说明），再回到此处签署；系统不会自动生成要求。"
            />
          )}

          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Card size="small" title="本次签署声明（必填，1–500 字）">
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Input.TextArea
                  rows={2} maxLength={500} showCount
                  placeholder="整段说明，例如：已人工核对第 3、7 页的图片内容，并按品牌要求补录规则"
                  value={header.statement}
                  onChange={(ev) => setHeader((h) => ({ ...h, statement: ev.target.value }))}
                  disabled={busy}
                />
                <Input.TextArea
                  rows={2} maxLength={500} showCount
                  placeholder="补录原因（必填，1–500 字），例如：PDF 含图片页，文本层未覆盖，需人工补录"
                  value={header.reason}
                  onChange={(ev) => setHeader((h) => ({ ...h, reason: ev.target.value }))}
                  disabled={busy}
                />
              </Space>
            </Card>

            {pages.map((p) => {
              const e = entryOf(p);
              const tooLong = e.statement.length > 500;
              return (
                <Card key={p} size="small" title={uncoveredPageLabel(p)}
                  extra={<Tag>{p}</Tag>}>
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Input.TextArea
                      rows={2}
                      maxLength={500}
                      showCount
                      status={tooLong ? 'error' : undefined}
                      placeholder="补录说明（1–500 字）：该页内容是什么、依据什么补录、是否影响审核结论"
                      value={e.statement}
                      onChange={(ev) => setEntry(p, { statement: ev.target.value })}
                      disabled={busy}
                    />
                    <Select
                      style={{ width: '100%' }}
                      placeholder="挂靠的品牌人工补充要求（同版本、非停用）"
                      value={e.requirementId ?? undefined}
                      onChange={(v: string) => setEntry(p, { requirementId: v })}
                      disabled={busy || candidates.length === 0}
                      options={candidates.map((r) => ({
                        value: r.id,
                        label: `${r.text.slice(0, 30)}${r.text.length > 30 ? '…' : ''} · 补充：${(manualNoteOf(r) ?? '').slice(0, 20)}…`,
                      }))}
                    />
                  </Space>
                </Card>
              );
            })}
          </Space>

          {error && <Alert type="error" showIcon closable style={{ marginTop: 12 }}
            message={error} onClose={() => setError(null)} />}

          <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
            <Button type="primary" onClick={submit} disabled={!canSubmit}>
              签署全部未覆盖页
            </Button>
            {incomplete.length > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                待完成：{incomplete.map(uncoveredPageLabel).join('、')}
              </Typography.Text>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
