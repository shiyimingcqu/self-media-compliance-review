import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Drawer, Input, Select, Space } from 'antd';
import type { Platform, ProjectDetail } from '../../shared/api/types';
import { platformNames } from '../../shared/lib/format';
import { standardsApi } from '../workflow/standardsApi';

export function AddCreatorDrawer({ project, open, onClose }: { project: ProjectDetail; open: boolean; onClose: () => void }) {
  const [name, setName] = useState(''), [platform, setPlatform] = useState<Platform>(project.platforms[0]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>();
  const lock = useRef(false), operation = useRef<{ signature: string; key: string } | undefined>(undefined);
  const { message, modal } = App.useApp(), cache = useQueryClient();
  const standards = useQuery({ queryKey: ['project-standards', project.id], queryFn: () => standardsApi.projectStandards(project.id), enabled: open });
  async function save(acknowledgeDuplicate = false) {
    if (lock.current || !name.trim() || !standards.data?.data.ready) return;
    lock.current = true; setBusy(true); setError(undefined); let duplicate = false;
    const body = { creatorName: name.trim(), platform, acknowledgeDuplicate }, signature = JSON.stringify(body);
    if (operation.current?.signature !== signature) operation.current = { signature, key: crypto.randomUUID() };
    try {
      await standardsApi.addCreator(project.id, body, operation.current!.key);
      await Promise.all([cache.invalidateQueries({ queryKey: ['project-tasks', project.id] }), cache.invalidateQueries({ queryKey: ['creator-tasks'] }), cache.invalidateQueries({ queryKey: ['project', project.id] }), cache.invalidateQueries({ queryKey: ['projects'] })]);
      message.success('达人已加入项目，使用项目统一规则'); operation.current = undefined; setName(''); onClose();
    } catch (e) { if (e && typeof e === 'object' && 'code' in e && e.code === 'DUPLICATE_SUSPECTED') duplicate = true; else setError(e instanceof Error ? e.message : '添加失败'); }
    finally { lock.current = false; setBusy(false); }
    if (duplicate) modal.confirm({ title: '项目内已有同名达人，继续添加独立提交任务？', onOk: () => save(true) });
  }
  return <Drawer title={`添加达人 · ${project.name}`} open={open} onClose={() => { if (!lock.current) onClose(); }} footer={<Button type="primary" loading={busy} disabled={!name.trim() || !standards.data?.data.ready || project.status === 'archived'} onClick={() => void save()}>添加达人</Button>}>
    <Alert type="info" title="自动使用项目统一审核规则" description={standards.data?.data.ready ? `当前项目规则 V${standards.data.data.revision}；规则更新后，本项目全部达人同步使用新版本。` : '请返回项目详情，先配置项目规则。'} style={{ marginBottom: 20 }} />
    <Space orientation="vertical" style={{ width: '100%' }}><label>达人名称<Input aria-label="达人名称" value={name} maxLength={50} disabled={busy} onChange={e => setName(e.target.value)} /></label><label>平台<Select aria-label="达人平台" value={platform} disabled={busy} onChange={setPlatform} style={{ width: '100%', minWidth: 220 }} options={project.platforms.map(p => ({ value: p, label: platformNames[p] }))} /></label></Space>
    {error && <Alert type="error" title={error} style={{ marginTop: 16 }} />}
  </Drawer>;
}
