import { useEffect, useRef, useState } from 'react';
import { Navigate, Outlet, useLocation, useNavigate, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button as AntButton, Select } from 'antd';
import * as Dialog from '@radix-ui/react-dialog';
import { Inbox, Folder, BookOpen, MessageSquare, ChartNoAxesCombined, Settings2, PanelLeftClose, PanelLeftOpen, Search, Bell, ArrowLeftRight, X, ChevronRight, CornerDownLeft } from 'lucide-react';
import { api } from '../shared/api/client';
import { projectApi } from '../features/projects/api';
import type { Context, Envelope } from '../shared/api/types';
import { DemoRoleContext, type DemoRole } from './DemoRole';
import { CreatorIdentityContext } from './CreatorIdentity';
import { creatorApi } from '../features/creator/api';
import { Button } from '../components/ui/button';
import './shell.css';

const navigation = [{ path: '/reviews', title: '审核队列', icon: Inbox }, { path: '/projects', title: '活动管理', icon: Folder }, { path: '/rules', title: '规则库', icon: BookOpen }, { path: '/feedback', title: '达人反馈', icon: MessageSquare }, { path: '/dashboard', title: '数据看板', icon: ChartNoAxesCombined }, { path: '/settings/models', title: '设置', icon: Settings2 }];
export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  // --mode brand / --mode creator 起的端口把角色钉死；未指定时沿用原来的手动切换。
  const pinnedRole: DemoRole | '' = __APP_ROLE__;
  const [role, setRole] = useState<DemoRole>(() => pinnedRole || (localStorage.getItem('review-demo-role') === 'creator' ? 'creator' : 'brand'));
  const [searchOpen, setSearchOpen] = useState(false), [search, setSearch] = useState('');
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const context = useQuery({ queryKey: ['context'], queryFn: () => api<Envelope<Context>>('/context') });
  const queue = useQuery({ queryKey: ['shell-review-count'], enabled: role === 'brand', queryFn: () => api<{ data: unknown[] }>('/workflow/review-queue'), refetchInterval: 30000 });
  const disputes = useQuery({ queryKey: ['review-disputes-count'], enabled: role === 'brand', queryFn: () => api<{ data: { total: number } }>('/workflow/review-disputes?limit=1'), refetchInterval: 30000 });
  const creatorTasks = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks, enabled: role === 'creator' });
  const creators = [...new Set((creatorTasks.data?.data || []).map(task => task.creator_name))];
  const [creator, setCreatorName] = useState<string>(() => localStorage.getItem('review-creator-name') || '');
  const activeCreator = creators.includes(creator) ? creator : creators[0] || '';
  const chooseCreator = (name: string) => { localStorage.setItem('review-creator-name', name); setCreatorName(name); if (/\/tasks\//.test(pathname)) navigate('/creator'); };
  const results = useQuery({ queryKey: ['global-project-search', search], enabled: searchOpen && role === 'brand', queryFn: () => projectApi.list(new URLSearchParams({ q: search, pageSize: '10' }).toString()) });
  const switchTarget = useRef<string | null>(null);
  useEffect(() => { if ((role === 'creator') === pathname.startsWith('/creator')) switchTarget.current = null; }, [pathname, role]);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(value => !value); } };
    document.addEventListener('keydown', listener); return () => document.removeEventListener('keydown', listener);
  }, []);
  const selected = role === 'creator' ? '/creator' : pathname.includes('/review-disputes/') ? '/feedback' : pathname.includes('/rules') ? '/rules' : navigation.find(item => pathname.startsWith(item.path))?.path || '/projects';
  const title = role === 'creator' ? '我的合作' : navigation.find(item => item.path === selected)?.title || '活动管理';
  function switchRole() {
    const next = role === 'brand' ? 'creator' : 'brand';
    const task = pathname.match(/(?:\/creator)?\/projects\/([^/]+)\/tasks\/([^/]+)/);
    const scope = pathname.endsWith('/image') ? 'image' : pathname.endsWith('/video') ? 'video' : 'script';
    const target = task ? next === 'creator' ? `/creator/projects/${task[1]}/tasks/${task[2]}${scope === 'script' ? '' : '/' + scope}` : `/projects/${task[1]}/tasks/${task[2]}/${scope}` : next === 'creator' ? '/creator' : '/projects';
    switchTarget.current = target; localStorage.setItem('review-demo-role', next); setRole(next); navigate(target);
  }
  const creatorPath = pathname.startsWith('/creator');
  return <DemoRoleContext.Provider value={role}><CreatorIdentityContext.Provider value={{ creators, creator: activeCreator, setCreator: chooseCreator }}><div className={`app-layout desk-shell role-${role}${collapsed ? ' is-collapsed' : ''}`}>
    <aside className="app-sidebar desk-sidebar" aria-label="主导航">
      <Link to={role === 'brand' ? '/projects' : '/creator'} className="desk-wordmark" aria-label="达人营销智能审核平台"><span className="wordmark-emblem" aria-hidden="true">R</span><span className="wordmark-line">REVIEW<span className="wordmark-dot">.</span>DESK</span>{!collapsed && <span className="wordmark-sub">达人投放 · 内容审核系统</span>}</Link>
      {!collapsed && <div className="workspace-switch"><span className="workspace-avatar">{role === 'brand' ? '品' : '达'}</span><span>{role === 'brand' ? '品牌工作空间' : '达人工作空间'}<small>内容审核工作台</small></span><ChevronRight size={13} /></div>}
      <div className="sidebar-section-label">{collapsed ? '—' : '工作空间'}</div>
      <nav className="desk-nav">{(role === 'creator' ? [{ path: '/creator', title: '我的合作', icon: Folder }] : navigation).map(item => <Link key={item.path} to={item.path} aria-current={selected === item.path ? 'page' : undefined} className={selected === item.path ? 'is-selected' : ''} title={collapsed ? item.title : undefined}><item.icon size={18} strokeWidth={1.5} />{!collapsed && <><span>{item.title}</span>{item.path === '/reviews' && queue.data && <small className="nav-count">{queue.data.data.length}</small>}{item.path === '/feedback' && !!disputes.data?.data.total && <small className="nav-count">{disputes.data.data.total}</small>}</>}</Link>)}</nav>
      <div className="desk-sidebar-bottom">{!collapsed && <><div className="desk-system-status"><i />{context.isSuccess ? '工作空间已连接' : context.isError ? '服务连接中断' : '正在连接工作空间'}</div><small>AI 预审 · 品牌最终确认</small></>}<Button variant="ghost" size="icon" aria-label={collapsed ? '展开导航' : '收起导航'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</Button></div>
    </aside>
    <div className="main-layout desk-main">
      <header className="topbar desk-topbar"><div className="desk-breadcrumb"><button className="mobile-nav-trigger" aria-label="切换导航" onClick={() => setCollapsed(!collapsed)}><PanelLeftOpen size={16} /></button><span>{role === 'brand' ? '工作空间' : '达人工作台'}</span><ChevronRight size={12} /><span>{title}</span></div><div className="topbar-right">{role === 'creator' && creators.length > 0 && <Select size="small" aria-label="切换达人" value={activeCreator} onChange={chooseCreator} options={creators.map(name => ({ value: name, label: name }))} style={{ minWidth: 150 }} />}<Button variant="ghost" size="icon" aria-label="全局搜索" onClick={() => setSearchOpen(true)}><Search size={16} /></Button><Button variant="ghost" size="icon" aria-label="通知：查看待审核队列" onClick={() => navigate(role === 'brand' ? disputes.data?.data.total ? '/feedback?tab=disputes' : '/reviews' : '/creator')}><Bell size={16} />{!!(queue.data?.data.length || disputes.data?.data.total) && role === 'brand' && <span className="notification-indicator" />}</Button><span className="toolbar-divider" />{!pinnedRole && <Button variant="ghost" size="sm" onClick={switchRole} aria-label={role === 'creator' ? '切换到品牌端' : '切换到达人端'}><ArrowLeftRight size={13} /><span>{role === 'creator' ? '切换到品牌端' : '切换到达人端'}</span></Button>}<span className="desk-user-avatar" title={role === 'brand' ? '品牌审核员' : '内容创作者'}>{role === 'brand' ? '审' : '达'}</span></div></header>
      <main className="page-content">
        {context.isError && <Alert className="context-alert" showIcon type="warning" title="后端未连接" action={<AntButton size="small" onClick={() => context.refetch()}>重新连接</AntButton>} />}
        {switchTarget.current && (role === 'creator') !== creatorPath ? <div className="quiet-empty">正在切换工作台…</div> : role === 'creator' && !creatorPath ? <Navigate to="/creator" replace /> : role === 'brand' && creatorPath ? <Navigate to="/projects" replace /> : <Outlet />}
      </main>
    </div>
    <Dialog.Root open={searchOpen} onOpenChange={setSearchOpen}><Dialog.Portal><Dialog.Overlay className="desk-dialog-overlay" /><Dialog.Content className="desk-search-dialog" onKeyDown={event => { if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return; event.preventDefault(); const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('.desk-search-results button')); if (!buttons.length) return; const index = buttons.indexOf(document.activeElement as HTMLButtonElement); buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus(); }}><Dialog.Title className="sr-only">全局搜索</Dialog.Title><Dialog.Description className="sr-only">按活动名称搜索项目，或跳转工作空间页面。</Dialog.Description><div className="desk-search-input"><Search size={18} /><input autoFocus placeholder="搜索活动名称或工作空间页面…" aria-label="全局搜索活动" value={search} onChange={event => setSearch(event.target.value)} /><Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="关闭搜索"><X size={16} /></Button></Dialog.Close></div><div className="desk-search-results">{navigation.filter(item => item.title.includes(search)).map(item => <button key={item.path} onClick={() => { navigate(role === 'brand' ? item.path : '/creator'); setSearchOpen(false); }}><item.icon size={16} /><span>{item.title}</span><CornerDownLeft size={12} /></button>)}{role === 'brand' && <><small>活动</small>{results.isPending ? <div className="desk-search-skeleton" /> : results.isError ? <p>活动暂时读取失败，请稍后重试。</p> : results.data?.data.length ? results.data.data.map(project => <button key={project.id} onClick={() => { navigate(`/projects/${project.id}`); setSearchOpen(false); }}><Folder size={16} /><span>{project.name}<small>{project.brand.name}</small></span><CornerDownLeft size={12} /></button>) : <p>没有找到匹配活动</p>}</>}</div><footer><kbd>↑ ↓</kbd> 浏览 <kbd>Enter</kbd> 打开 <kbd>Esc</kbd> 关闭</footer></Dialog.Content></Dialog.Portal></Dialog.Root>
  </div></CreatorIdentityContext.Provider></DemoRoleContext.Provider>;
}
