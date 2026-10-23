import { Link } from 'react-router-dom';
import { FileTextOutlined, PictureOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { useDemoRole } from '../../app/DemoRole';
import './content-review.css';

export function ContentReviewNav({ projectId, taskId, active, compact = false }: { projectId: string; taskId: string; active: 'script' | 'video' | 'image'; compact?: boolean }) {
  const role = useDemoRole();
  const base = `${role === 'creator' ? '/creator' : ''}/projects/${projectId}/tasks/${taskId}`;
  return <nav className={`content-review-nav${compact ? ' is-compact' : ''}`} aria-label="合作验收类型">
    {([
      { key: 'script', title: '脚本验收', detail: '内容与镜头安排', icon: <FileTextOutlined /> },
      { key: 'video', title: '视频验收', detail: '完整成片与音轨', icon: <VideoCameraOutlined /> },
      { key: 'image', title: '图文验收', detail: '封面、配图与文案', icon: <PictureOutlined /> },
    ] as const).map(item => <Link key={item.key} aria-label={compact ? item.title : `${item.title} ${item.detail}`} aria-current={active === item.key ? 'page' : undefined} className={active === item.key ? 'is-active' : ''} to={`${base}${item.key === 'script' && role === 'creator' ? '' : '/' + item.key}`}>
      {item.icon}<span><strong>{item.title}</strong><small>{item.detail}</small></span><span className="content-nav-indicator" />
    </Link>)}
  </nav>;
}
