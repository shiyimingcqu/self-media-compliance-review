/** 平台自己追加的提示带这个前缀；模型写的 Brief 疑点没有前缀。 */
const SYSTEM_PREFIX = '【系统提示】';
// 历史记录里系统提示没有前缀，按已知措辞兜底识别，避免老记录两组错位。
const legacySystemPatterns = [/^第\d+条(引文|引用|省略引文)/, /^本次仅重新校验/, /^Brief仍有\d+项/,
  /^Brief有\d+项未解析/, /^模型返回了视频分层字段/];

export function splitWarnings(warnings: string[] = []): { content: string[]; system: string[] } {
  const content: string[] = [], system: string[] = [];
  for (const raw of warnings) {
    const text = String(raw ?? '').trim();
    if (!text) continue;
    if (text.startsWith(SYSTEM_PREFIX)) system.push(text.slice(SYSTEM_PREFIX.length));
    else if (legacySystemPatterns.some(pattern => pattern.test(text))) system.push(text);
    else content.push(text);
  }
  return { content, system };
}

const listStyle = { margin: 0, paddingInlineStart: 18 } as const;
const itemStyle = { marginBottom: 4, lineHeight: 1.7 } as const;

function ItemList({ items }: { items: string[] }) {
  return <ul style={listStyle}>{items.map((item, index) => <li key={index} style={itemStyle}>{item}</li>)}</ul>;
}

function SystemNotes({ items }: { items: string[] }) {
  if (!items.length) return null;
  return <details style={{ marginTop: 8 }}>
    <summary style={{ cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 12 }}>系统提示 · {items.length} 项</summary>
    <div style={{ marginTop: 8, color: 'var(--text-secondary)', fontSize: 12 }}><ItemList items={items} /></div>
  </details>;
}

/** 直接嵌在既有 Alert 里用：只出列表和折叠区，不再套一层 Alert。 */
export function WarningList({ warnings, empty = '暂无需核对的疑点。' }: { warnings: string[]; empty?: string }) {
  const { content, system } = splitWarnings(warnings);
  return <>
    {content.length ? <ItemList items={content} /> : <p style={{ margin: 0 }}>{empty}</p>}
    <SystemNotes items={system} />
  </>;
}

/** 辅助提示默认收起，品牌以最终规则为主要确认对象。 */
export function RuleWarnings({ warnings, title = '生成提示' }: { warnings: string[]; title?: string }) {
  const { content, system } = splitWarnings(warnings);
  if (!content.length && !system.length) return null;
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
    {!!content.length && <details style={{ marginBlock: 8 }}>
      <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 12 }}>{title} · {content.length} 项</summary>
      <div style={{ marginTop: 8, color: 'var(--text-secondary)', fontSize: 12 }}><ItemList items={content} /></div>
    </details>}
    <SystemNotes items={system} />
  </div>;
}
