import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App, Button, Checkbox, Empty, Form, Input, Modal, Select, Skeleton, Space, Table, Tag } from 'antd';
import { FolderOpenOutlined, PlusOutlined, SearchOutlined, FileSearchOutlined, UserOutlined, ArrowRightOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { projectApi } from '../api';
import { creatorApi } from '../../creator/api';
import { ApiError } from '../../../shared/api/client';
import type { Project, ProjectCreate } from '../../../shared/api/types';
import { ErrorState, PageHeader, PlatformBadge, StatCard, StatusTag } from '../../../shared/ui';
import { formatDate, platformNames } from '../../../shared/lib/format';
import { BriefProjectImport } from '../BriefProjectImport';

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get('q') || '');
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<ProjectCreate>();
  const requestKey = useRef<string | null>(null);
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const queryString = params.toString();
  const projects = useQuery({ queryKey: ['projects', queryString], queryFn: ({ signal }) => projectApi.list(queryString, signal) });
  const creatorTasks = useQuery({ queryKey: ['creator-tasks'], queryFn: creatorApi.tasks });
  const stats = useQuery({ queryKey: ['dashboard'], queryFn: projectApi.dashboard });
  const brands = useQuery({ queryKey: ['brands'], queryFn: projectApi.brands });

  function update(key: string, value?: string) {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value); else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });
  }

  useEffect(() => { setSearch(params.get('q') || ''); }, [params.get('q')]);
  useEffect(() => {
    const timer = window.setTimeout(() => { if (search !== (params.get('q') || '')) update('q', search); }, 300);
    return () => window.clearTimeout(timer);
  }, [search, params]);

  const create = useMutation({
    mutationFn: (payload: ProjectCreate) => projectApi.create(payload, requestKey.current ||= crypto.randomUUID()),
    onSuccess: async result => {
      setOpen(false); form.resetFields(); requestKey.current = null;
      await Promise.all([cache.invalidateQueries({ queryKey: ['projects'] }), cache.invalidateQueries({ queryKey: ['dashboard'] }), cache.invalidateQueries({ queryKey: ['brands'] })]);
      message.success('项目已创建'); navigate(`/projects/${result.data.id}`);
    },
    onError: error => {
      if (error instanceof ApiError && error.fieldErrors) {
        const fields = ['name', 'brandName', 'brandId', 'description', 'platforms'];
        form.setFields(Object.entries(error.fieldErrors).filter(([name]) => fields.includes(name)).map(([name, text]) => ({ name: name as keyof ProjectCreate, errors: [text] })));
      }
      message.error(error.message);
    },
  });

  function close() {
    if (create.isPending) return;
    const discard = () => { setOpen(false); form.resetFields(); requestKey.current = null; };
    if (form.isFieldsTouched()) modal.confirm({ title: '放弃未保存的项目？', content: '关闭后将清除本次填写的内容。', okText: '放弃', cancelText: '继续编辑', onOk: discard });
    else discard();
  }

  const columns: ColumnsType<Project> = [
    { title: '项目名称', dataIndex: 'name', key: 'name', width: 320, render: (_, p) => <div className="project-cell"><span className="project-art"><FolderOpenOutlined /></span><div><Link to={`/projects/${p.id}`} className="project-name">{p.name}</Link><div className="cell-subtitle">{p.description || p.productNames?.join('、') || '尚未填写项目说明'}</div></div></div> },
    { title: '品牌', key: 'brand', render: (_, p) => p.brand.name },
    { title: '投放平台', key: 'platforms', width: 180, render: (_, p) => <Space size={2} wrap>{p.platforms.map(platform => <PlatformBadge key={platform} platform={platform} />)}</Space> },
    { title: '达人任务', dataIndex: 'taskCount', width: 90 },
    { title: '审核进度', key: 'progress', width: 135, render: (_, p) => p.taskCount === 0 ? <span className="muted">尚无任务</span> : `${p.completedTaskCount} / ${p.taskCount}` },
    { title: '更新时间', dataIndex: 'updatedAt', width: 170, render: value => <span className="date-text">{formatDate(value)}</span> },
    { title: '状态', dataIndex: 'status', render: status => <StatusTag status={status} /> },
    { title: '', key: 'action', width: 55, render: (_, p) => <Link aria-label={`打开项目 ${p.name}`} to={`/projects/${p.id}`}><ArrowRightOutlined /></Link> },
  ];

  const isFiltered = ['q', 'brandId', 'platform', 'status'].some(key => params.has(key));
  return <>
    <PageHeader title="项目列表" description="管理品牌营销项目，让每一次内容审核有据可循。" action={<Space wrap><BriefProjectImport /><Button aria-label="新建项目" size="large" icon={<PlusOutlined aria-hidden />} onClick={() => setOpen(true)}>新建项目</Button></Space>} />
    <div className="section-label">工作空间概览 <span>全部项目统计</span></div>
    <div className="stats-grid">
      <StatCard label="进行中项目" value={stats.data?.data.activeProjects ?? '—'} icon={<FolderOpenOutlined />} hint={stats.isError ? '统计连接失败' : '当前演示工作区的本地项目'} />
      <StatCard label="待达人完善脚本" value={creatorTasks.data?.data.filter(t => !t.brandApproved && (!t.scriptAcceptance.passed || t.brandDecision?.decision === 'returned')).length ?? '—'} icon={<FileSearchOutlined />} hint="提交或修改脚本后，由 AI 验收必须项" />
      <StatCard label="待品牌审脚本" value={creatorTasks.data?.data.filter(t => t.scriptAcceptance.passed && !t.brandApproved && t.brandDecision?.decision !== 'returned').length ?? '—'} icon={<UserOutlined />} hint="必须项已通过，等待品牌复核" tone="green" />
    </div>
    <div className="panel project-panel">
      <div className="filter-bar">
        <Input aria-label="搜索项目名称" prefix={<SearchOutlined />} placeholder="搜索项目名称" value={search} onChange={e => setSearch(e.target.value)} allowClear className="search-input" />
        <Select aria-label="品牌筛选" placeholder="全部品牌" allowClear value={params.get('brandId') || undefined} onChange={v => update('brandId', v)} options={brands.data?.data.map(b => ({ label: b.name, value: b.id }))} />
        <Select aria-label="平台筛选" placeholder="全部平台" allowClear value={params.get('platform') || undefined} onChange={v => update('platform', v)} options={Object.entries(platformNames).map(([value, label]) => ({ value, label }))} />
        <Select aria-label="状态筛选" placeholder="全部状态" allowClear value={params.get('status') || undefined} onChange={v => update('status', v)} options={[{ value: 'active', label: '进行中' }, { value: 'completed', label: '审核完成' }, { value: 'archived', label: '已归档' }]} />
        {isFiltered && <Button type="text" onClick={() => { setParams({}); setSearch(''); }}>重置</Button>}
      </div>
      {projects.isError ? <div className="panel-padding"><ErrorState error={projects.error} retry={() => projects.refetch()} /></div> : projects.isPending ? <div className="panel-padding"><Skeleton active paragraph={{ rows: 6 }} /></div> :
        <Table<Project> rowKey="id" columns={columns} loading={false} dataSource={projects.data?.data} scroll={{ x: 1080 }}
          pagination={{ current: projects.data?.page || 1, pageSize: projects.data?.pageSize || 10, total: projects.data?.total || 0, showSizeChanger: true, pageSizeOptions: [10, 20, 50], showTotal: total => `共 ${total} 个项目`, onChange: (page, size) => { const next = new URLSearchParams(params); next.set('page', String(size !== (projects.data?.pageSize || 10) ? 1 : page)); next.set('pageSize', String(size)); setParams(next); } }}
          locale={{ emptyText: <div className="project-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><h3>{isFiltered ? '没有找到匹配的项目' : '从第一个营销项目开始'}</h3><p>{isFiltered ? '试试调整名称、品牌或平台筛选。' : '创建项目后，按平台管理 Brief、达人脚本和完整视频。'}</p></>} /><Button type="primary" icon={isFiltered ? undefined : <PlusOutlined />} onClick={() => isFiltered ? (setParams({}), setSearch('')) : setOpen(true)}>{isFiltered ? '清除筛选' : '创建第一个项目'}</Button></div> }} />}
    </div>
    <div className="workspace-note"><Tag>演示版</Tag> 上传 Brief 可自动识别品牌、产品与平台并创建项目；规则由品牌核对后启用。</div>
    <Modal title="新建项目" open={open} onCancel={close} onOk={() => form.submit()} confirmLoading={create.isPending} okText="创建项目" cancelText="取消" width={620} mask={{ closable: false }}>
      <Form form={form} layout="vertical" onFinish={values => create.mutate(values)} onValuesChange={() => { requestKey.current = null; }}>
        <div className="form-grid"><Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true, message: '请输入项目名称' }, { max: 50 }]}><Input maxLength={50} showCount placeholder="例如：麦咖啡芝士浓奶铁抖音项目" /></Form.Item><Form.Item name="brandName" label="品牌" rules={[{ required: true, whitespace: true, message: '请输入品牌名称' }, { max: 50 }]}><Input maxLength={50} placeholder="例如：麦咖啡" /></Form.Item></div>
        <Form.Item name="description" label="项目说明"><Input.TextArea rows={3} maxLength={200} showCount placeholder="说明项目目标、产品与合作背景" /></Form.Item>
        <Form.Item name="productNames" label="产品名称"><Select mode="tags" placeholder="输入产品名称后按回车" /></Form.Item>
        <Form.Item name="platforms" label="投放平台" rules={[{ required: true, type: 'array', min: 1, message: '请至少选择一个平台' }]}><Checkbox.Group options={Object.entries(platformNames).map(([value, label]) => ({ value, label }))} /></Form.Item>
      </Form>
    </Modal>
  </>;
}
