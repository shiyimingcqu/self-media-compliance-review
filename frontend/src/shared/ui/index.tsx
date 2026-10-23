import type { ReactNode } from 'react';
import { Alert, Button, Empty, Steps, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { Platform } from '../api/types';
import { platformNames } from '../lib/format';

export function PageHeader({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <header className="page-header"><div><h1>{title}</h1><p>{description}</p></div>{action}</header>;
}

export function StatCard({ label, value, icon, hint, tone = 'blue' }: { label: string; value: ReactNode; icon: ReactNode; hint: string; tone?: 'blue' | 'green' | 'orange' }) {
  return <div className={`stat-card ${tone}`}><span className="stat-icon">{icon}</span><div><span className="stat-label">{label}</span><strong>{value}</strong><small>{hint}</small></div></div>;
}

export function PlatformBadge({ platform }: { platform: Platform }) {
  return <Tag className={`platform-tag ${platform}`}>{platformNames[platform]}</Tag>;
}

export function StatusTag({ status }: { status: string }) {
  const labels: Record<string, [string, string]> = { active: ['进行中', 'blue'], completed: ['审核完成', 'green'], archived: ['已归档', 'default'], not_implemented: ['待接入', 'default'] };
  const [text, color] = labels[status] || [status, 'default'];
  return <Tag color={color}>{text}</Tag>;
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return <Alert type="error" showIcon title="加载失败" description={error.message} action={retry && <Button size="small" icon={<ReloadOutlined />} onClick={retry}>重试</Button>} />;
}

export function WorkflowStepper({ current }: { current: number }) {
  return <Steps className="workflow" current={current} items={['要求确认', '脚本审核', '视频审核', '人工确认'].map(title => ({ title }))} />;
}

export function ModuleNotice({ owner, title, description, children }: { owner: string; title: string; description: string; children?: ReactNode }) {
  return <div className="module-notice"><div className="module-label">模块 {owner} · 开发接入点</div><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><h3>{title}</h3><p>{description}</p></>} />{children}<Alert type="info" showIcon title="当前为项目骨架" description="尚未执行 AI 分析，也没有生成审核结论。该模块接入后沿用统一版本、错误及状态契约。" /></div>;
}
