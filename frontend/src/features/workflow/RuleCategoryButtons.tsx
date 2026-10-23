import { FileTextOutlined, PictureOutlined, VideoCameraOutlined, ArrowRightOutlined } from '@ant-design/icons';
import './project-rules.css';

export type RuleCategory = 'script' | 'image' | 'video';
export function RuleCategoryButtons({ active, counts, disabled, onSelect }: { active?: RuleCategory; counts: Record<RuleCategory, number>; disabled?: boolean; onSelect: (value: RuleCategory) => void }) {
  return <div className="rule-category-buttons" role="group" aria-label="项目规则分类">
    {([
      { key: 'script', title: '脚本规则', description: '品牌信息、卖点与内容安排', icon: <FileTextOutlined /> },
      { key: 'image', title: '图文规则', description: '封面、配图与文案表达', icon: <PictureOutlined /> },
      { key: 'video', title: '视频规则', description: '产品露出、口播与工具取证', icon: <VideoCameraOutlined /> },
    ] as const).map(item => <button key={item.key} type="button" className={`rule-category-button category-${item.key}${active === item.key ? ' is-active' : ''}`} aria-label={item.title} aria-pressed={active === undefined ? undefined : active === item.key} disabled={disabled} onClick={() => onSelect(item.key)}>
      <span className="rule-category-icon">{item.icon}</span><span className="rule-category-copy"><strong>{item.title}</strong><span>{item.description}</span><small>{counts[item.key] ? `${counts[item.key]} 条规则` : '配置规则'}</small></span><ArrowRightOutlined className="rule-category-arrow" />
    </button>)}
  </div>;
}
