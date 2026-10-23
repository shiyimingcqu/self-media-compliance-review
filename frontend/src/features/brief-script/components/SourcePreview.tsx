// 源预览（t-30）：持久 preview 为唯一验证事实；原登记缓存仅同源加速（preview 到达即被替换），
// 各异常态如实显式，不空显不伪恢复。
import { Alert, Card, Empty, List, Space, Tag, Typography } from 'antd';
import type { PreviewBlock, TextBlock } from '../api';

type Props = {
  blocks: PreviewBlock[] | TextBlock[] | null; // 持久 preview 命中后的块
  state: 'ready' | 'loading' | 'no-source' | 'not-ready' | `not-ready:${string}` | 'mismatch' | 'error';
  /** 同一源的原登记加速缓存：仅 state=loading 时临时渲染，不称持久验证。 */
  registeredCache?: PreviewBlock[] | TextBlock[] | null;
  /** t-40：来自 PreviewResult 的持久分页元数据（pageCount / 页级未覆盖），刷新后仍可显示。 */
  meta?: { pageCount?: number | null; uncoveredSources?: string[] | null } | null;
};

export function SourcePreview({ blocks, state, registeredCache, meta }: Props) {
  if (state === 'no-source') {
    return (
      <Card title="原文预览">
        <Alert type="warning" showIcon message="该版本源指针异常"
          description="投影中此版本无有效 source（不借 latest/其他 Brief 数据），引用与预览不可用；请派生或重新登记。" />
      </Card>
    );
  }
  if (state === 'mismatch') {
    return (
      <Card title="原文预览">
        <Alert type="error" showIcon message="预览响应与所选版本源不匹配"
          description="已拒绝注入，不冒用其他来源内容。" />
      </Card>
    );
  }
  if (state.startsWith('not-ready')) {
    const status = state.includes(':') ? state.slice('not-ready:'.length) : null;
    return (
      <Card title="原文预览">
        <Alert type="warning" showIcon
          message={status ? `该源解析未就绪（状态 ${status}）` : '该源未就绪（服务端未回传具体状态，界面不猜测）'}
          description="持久投影只读不重解析；修复原件后重新登记。" />
      </Card>
    );
  }
  if (state === 'error') {
    return <Card title="原文预览"><Alert type="error" showIcon message="来源读取失败" description="预览接口异常，不做本地乐观展示" /></Card>;
  }
  if (state === 'loading' && registeredCache?.length) {
    return <Card title="原文预览 · 段落来源映射" extra={<Tag color="blue">登记缓存加速显示（待持久验证）</Tag>}>
      <List size="small" dataSource={registeredCache} renderItem={(b) => (
        <List.Item>
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Typography.Text strong>{b.paragraphId}</Typography.Text>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{b.text}</Typography.Paragraph>
          </Space>
        </List.Item>
      )} />
    </Card>;
  }
  if (!blocks || blocks.length === 0) {
    return <Card title="原文预览"><Empty description="该源无可用段落（就绪但无持久块=数据异常，后端应已 500）" /></Card>;
  }
  const uncovered = meta?.uncoveredSources ?? [];
  return (
    <Card title="原文预览 · 段落来源映射"
      extra={<Space size={4}>
        {meta?.pageCount != null && <Tag>共 {meta.pageCount} 页</Tag>}
        {uncovered.length > 0 && <Tag color="orange">{uncovered.length} 页未覆盖</Tag>}
        <Tag color="green">持久投影已验证</Tag>
      </Space>}>
      {/* t-40：页级未覆盖必须醒目——文本层可读 ≠ 图片/扫描页已覆盖 */}
      {uncovered.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
          message={`含 ${uncovered.length} 页未纳入文本审核（图片/扫描页）`}
          description={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
            未覆盖页：{uncovered.map((s) => s.replace('page:', 'p')).join('、')}
            {' — '}这些页的内容未被审核，需人工补录或在覆盖确认中签署。
          </Typography.Text>} />)}
      <List size="small" dataSource={blocks}
        renderItem={(b) => (
          <List.Item>
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Space size={6}>
                <Typography.Text strong>{b.paragraphId}</Typography.Text>
                {(b as { pageNo?: number | null }).pageNo != null && (
                  <Tag color="blue">p{(b as { pageNo?: number | null }).pageNo}</Tag>)}
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {(b as { pageNo?: number | null }).pageNo != null
                  ? '按物理页定位'
                  : `偏移 ${b.startOffset}–${b.endOffset}`}
              </Typography.Text>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{b.text}</Typography.Paragraph>
            </Space>
          </List.Item>
        )} />
    </Card>
  );
}
