import { Typography } from 'antd';
import type { DirectSummary } from './projectBriefApi';

export function BriefDirectSummary({ summary, rules, search, onPage, onEdit, disabled }: { summary: DirectSummary; rules: boolean; search: string; onPage: (page: number) => void; onEdit: (group: string, index: number, text: string) => void; disabled: boolean }) {
  const labels: Record<string, string> = { brand: '品牌', product: '产品信息', sellingPoint: '核心卖点', common: '通用规则', video: '视频规则', image: '图片规则', copy: '文案规则' };
  return <>
    <p className="brief-pending-explainer">{rules ? '以下为模型提炼的规则，可直接载入审核规则编辑器修改。' : '产品事实用于核对信息准确性，不代表每一项都必须口播。'}点击内容查看原图，鼠标悬停显示页码。</p>
    {Object.entries(rules ? summary.rules : summary.productFacts).map(([key, entries]) => {
      const items = entries.filter(item => `${item.text} ${item.quote}`.toLowerCase().includes(search.toLowerCase()));
      return <section key={key} className="brief-fact-group" aria-label={labels[key]}>
        <header><strong>{labels[key]}</strong><span>{items.length} 项</span></header>
        {items.map((item, index) => <article className="brief-direct-item" key={index} title={`第 ${item.pageNo} 页`}>
          <Typography.Paragraph editable={disabled ? false : { onChange: text => onEdit(key, entries.indexOf(item), text), tooltip: '修改内容', maxLength: 1000 }} onClick={() => onPage(item.pageNo)}>{item.text}</Typography.Paragraph>
        </article>)}
        {!items.length && <p>未返回相关条目</p>}
      </section>;
    })}
  </>;
}
