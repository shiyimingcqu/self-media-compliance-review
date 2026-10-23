// D t-29 收尾版：任务创建抽屉（真实候选链路；服务端为最终权威）。
// 门闩设计（A 02:45 修正——不留死锁）：
// - busyRef 同步占用互斥「提交中」与「确认弹窗打开中」两个阶段
// - submit() 在 finally 释放提交锁【之后】再开重复确认框（待确认 payload
//   作为执行结果返回，不在 onError 内嵌开框——避免 submitting 占用期
//   调 confirmDialog 被吞）；onError 只做错误呈现
// - afterClose 按本次弹窗 token 释放：旧弹窗关闭不清新弹窗的锁
// - generation 守卫：卸载后旧回调最多刷新原 project query，不触新项目 UI；
//   卸载销毁本组件确认弹窗
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App, Alert, Drawer, Form, Input, Modal, Select, Skeleton } from 'antd';
import type { FormInstance } from 'antd';
import { tasksApi, candidateVersions, compatibilityCheck, payloadSignature } from './tasksApi';
import type { TaskCreate } from './tasksApi';
import type { Platform, ProjectDetail } from '../../shared/api/types';
import { ApiError } from '../../shared/api/client';
import { platformNames } from '../../shared/lib/format';
import { PublishTaskDrawer } from '../workflow/PublishTaskDrawer';
import { AddCreatorDrawer } from './AddCreatorDrawer';

const PHASE_OPTIONS = [
  { value: 'warmup', label: '预热期' },
  { value: 'seeding', label: '种草期' },
  { value: 'general', label: '通用' },
];
const PHASE_LABELS: Record<string, string> = { warmup: '预热期', seeding: '种草期', general: '通用' };

interface KeyEntry { signature: string; key: string }
type ModalToken = object;  // 每次弹窗的独立 token

/** 统一确认弹窗执行器（导出供测试驱动真实逻辑，非复制语义）。
 * onOk 收到本框 token：提交型回调把它传给 submit（由 submit 唯一完成
 * modal→submitting 原子转换）；非提交型（放弃编辑）用完按 token 释放。 */
export interface ConfirmRequest {
  title: string;
  content: React.ReactNode;
  okText: string;
  onOk: (token: ModalToken) => Promise<void> | void;
}

export function useTaskCreateFlow(project: ProjectDetail, form: FormInstance<TaskCreate>,
                                  onClose: () => void) {
  const { message, modal } = App.useApp();
  const cache = useQueryClient();
  const keyEntry = useRef<KeyEntry | null>(null);
  const busyRef = useRef<'modal' | 'submitting' | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const generationRef = useRef(0);
  const myGeneration = generationRef.current;
  const modalDestroyRef = useRef<(() => void) | null>(null);
  const modalTokenRef = useRef<ModalToken | null>(null);

  useEffect(() => () => {
    generationRef.current += 1;
    modalDestroyRef.current?.();
    modalDestroyRef.current = null;
    modalTokenRef.current = null;
    busyRef.current = null;
  }, []);

  const currentKey = useCallback((payload: TaskCreate): string => {
    const sig = payloadSignature(payload);
    if (!keyEntry.current || keyEntry.current.signature !== sig) {
      keyEntry.current = { signature: sig, key: crypto.randomUUID() };
    }
    return keyEntry.current.key;
  }, []);

  /** 开确认弹窗（同步占用；afterClose 按本弹窗 token 释放）。
   * onOk 只把本框 token 传给回调——modal→submitting 的原子转换【只在】
   * submit 内完成（A 11:02 指定唯一转换者），openConfirm 不预先转换。 */
  const openConfirm = useCallback((req: ConfirmRequest) => {
    if (busyRef.current) return;  // 同步闸门：占用期不开第二个
    const token: ModalToken = {};
    modalTokenRef.current = token;
    busyRef.current = 'modal';
    const releaseIfMine = () => {
      if (modalTokenRef.current === token) {  // 只有本弹窗能清自己的锁
        modalTokenRef.current = null;
        if (busyRef.current === 'modal') busyRef.current = null;
      }
    };
    const instance = modal.confirm({
      title: req.title, content: req.content, okText: req.okText, cancelText: '返回修改',
      onOk: async () => { await req.onOk(token); },  // 传 token；转换由 submit 决定
      onCancel: releaseIfMine,
      afterClose: releaseIfMine,  // 按句柄 token：旧弹窗关闭不清新弹窗锁
    });
    modalDestroyRef.current = instance.destroy;
  }, [modal]);

  const create = useMutation({
    mutationFn: (payload: TaskCreate) => tasksApi.create(project.id, payload, currentKey(payload)),
    onSuccess: async () => {
      if (generationRef.current !== myGeneration) {
        await cache.invalidateQueries({ queryKey: ['project-tasks', project.id] });
        return;
      }
      message.success('任务已创建');
      form.resetFields();
      keyEntry.current = null;
      await Promise.all([
        cache.invalidateQueries({ queryKey: ['project-tasks', project.id] }),
        cache.invalidateQueries({ queryKey: ['project', project.id] }),
      ]);
      onClose();
    },
    onError: (error: unknown, payload: TaskCreate) => {
      if (generationRef.current !== myGeneration) return;
      if (error instanceof ApiError) {
        if (error.code === 'RULES_NOT_ENABLED' || error.code === 'RULE_SCOPE_MISMATCH') {
          message.error('所选规则版本已不可用，请重新选择');
          void cache.invalidateQueries({ queryKey: ['project', project.id] });
          void cache.invalidateQueries({ queryKey: ['rule-version-detail'] });
          form.setFields([{ name: 'ruleVersionId' as keyof TaskCreate, errors: ['规则版本不可用，请重新选择'] }]);
          return;
        }
        if (error.fieldErrors) {
          form.setFields(Object.entries(error.fieldErrors).map(([name, text]) =>
            ({ name: name as keyof TaskCreate, errors: [text] })));
        }
      }
      message.error(error instanceof Error ? error.message : '创建失败，请重试');
    },
  });

  /** 提交执行器（modal→submitting 的唯一原子转换者；返回待重复确认 payload）。
   * - 无 token 调用（表单直提/重复确认重入）：busyRef 必须 null 才进入
   * - 携 token 调用（确认框 onOk）：busyRef 必须仍为 'modal' 且 token 匹配
   *   本框——持有者原子转换（清 modalTokenRef、busyRef→'submitting'）；
   *   外部并发/旧框迟到（token 不匹配或 busy 已变）一律拒绝
   * - finally 仅释放本次提交持有的锁（busyRef==='submitting' 才清）；
   *   409 后的重复确认在 finally 之后开新框（新 token 新锁） */
  const submit = useCallback(async (payload: TaskCreate,
                                    token?: ModalToken): Promise<TaskCreate | null> => {
    if (token === undefined) {
      if (busyRef.current) return null;  // 空闲占用检查（modal/submitting 均拒）
      busyRef.current = 'submitting';
    } else {
      // 持有者转换：仅当本框 token 仍持有 modal 锁时原子转换
      if (busyRef.current !== 'modal' || modalTokenRef.current !== token) return null;
      modalTokenRef.current = null;   // 清本框 token（旧框迟到释放将不匹配）
      busyRef.current = 'submitting';
    }
    setSubmitting(true);
    let needsDuplicateConfirm: TaskCreate | null = null;
    try {
      try {
        await create.mutateAsync({ ...payload });
      } catch (error) {
        if (error instanceof ApiError && error.code === 'DUPLICATE_SUSPECTED'
            && generationRef.current === myGeneration) {
          needsDuplicateConfirm = { ...payload };  // 释放锁后再开确认（A 02:45）
        }
        // 其他错误：onError 已呈现；此处吞掉让 promise 正常 settle
      }
    } finally {
      if (busyRef.current === 'submitting') busyRef.current = null;  // 只清自己的锁
      setSubmitting(false);
    }
    if (needsDuplicateConfirm) {
      const toConfirm = needsDuplicateConfirm;
      openConfirm({
        title: '确认创建重复的达人任务？',
        content: '同项目同平台已存在同名达人任务。确认后将创建独立任务，不会被合并。',
        okText: '确认创建',
        onOk: async (dupToken) => { await submit({ ...toConfirm, acknowledgeDuplicate: true }, dupToken); },
      });
    }
    return needsDuplicateConfirm;
  }, [create, openConfirm, myGeneration]);

  return { submit, openConfirm, busyRef, submitting, create, generationRef, keyEntry };
}

export function LegacyCreateTaskDrawer({ project, open, onClose }:
  { project: ProjectDetail; open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<TaskCreate>();
  const flow = useTaskCreateFlow(project, form, onClose);
  const { openConfirm, busyRef } = flow;

  const platform: Platform | undefined = Form.useWatch('platform', form);
  const campaignPhase = Form.useWatch('campaignPhase', form);
  const ruleVersionId = Form.useWatch('ruleVersionId', form);

  useEffect(() => {
    form.setFieldValue('ruleVersionId', undefined);
  }, [platform, campaignPhase]);  // eslint-disable-line react-hooks/exhaustive-deps

  const candidates = useMemo(
    () => candidateVersions(project.briefs, platform ?? '', campaignPhase ?? 'general'),
    [project.briefs, platform, campaignPhase]);

  const detail = useQuery({
    queryKey: ['rule-version-detail', ruleVersionId],
    queryFn: ({ signal }) => tasksApi.ruleVersionDetail(ruleVersionId ?? '', signal),
    enabled: Boolean(ruleVersionId),
    staleTime: 30_000,
  });
  const compat = useMemo(
    () => detail.data && platform && campaignPhase && detail.data.data.version.id === ruleVersionId
      ? compatibilityCheck(detail.data.data, platform, campaignPhase)
      : null,
    [detail.data, platform, campaignPhase, ruleVersionId]);

  const gateBlocked = !ruleVersionId || detail.isPending || detail.isError || !compat || !compat.ok;

  function close() {
    if (busyRef.current) return;
    const discard = () => { form.resetFields(); flow.keyEntry.current = null; onClose(); };
    if (form.isFieldsTouched()) {
      openConfirm({ title: '放弃未保存的任务？', content: '关闭后将清除本次填写的内容。',
        okText: '放弃',
        // 非提交型：不做 modal→submitting 转换——执行动作后按 token 由
        // afterClose 释放本框锁（A 11:02：不走 submit）。
        onOk: async () => { discard(); } });
    } else discard();
  }

  return (
    <Drawer title="新建达人任务" open={open} onClose={close} width={480}>
      {project.status === 'archived' ? (
        <Alert type="warning" showIcon title="项目已归档" description="归档项目为只读，不能创建任务。" />
      ) : candidates.length === 0 ? (
        <Alert type="info" showIcon title="暂无可绑定的规则版本"
          description="该项目还没有已启用的规则版本。请先在项目内完成 Brief 上传与规则确认启用，再创建达人任务。" />
      ) : (
        <Form form={form} layout="vertical"
          initialValues={{ campaignPhase: 'general', acknowledgeDuplicate: false }}
          onFinish={values => {
            const selected = candidates.find(c => c.id === values.ruleVersionId);
            openConfirm({
              title: '确认创建任务',
              content: <>达人 <strong>{values.creatorName}</strong> · 平台{' '}
                <strong>{platformNames[values.platform as Platform]}</strong> · 活动阶段{' '}
                <strong>{PHASE_LABELS[values.campaignPhase]}</strong> · 规则版本{' '}
                <strong>{selected?.versionLabel}</strong></>,
              okText: '创建',
              onOk: async (token) => { await flow.submit(values, token); },
            });
          }}>
          <Form.Item name="creatorName" label="达人名称"
            rules={[{ required: true, whitespace: true, message: '请输入达人名称' }, { max: 50, message: '最多 50 字' }]}>
            <Input maxLength={50} showCount placeholder="例如：石头哥哥" />
          </Form.Item>
          <Form.Item name="platform" label="投放平台"
            rules={[{ required: true, message: '请选择平台（仅项目已选平台）' }]}>
            <Select placeholder="选择平台" options={project.platforms.map((p: Platform) =>
              ({ value: p, label: platformNames[p] }))} />
          </Form.Item>
          <Form.Item name="campaignPhase" label="活动阶段"
            rules={[{ required: true, message: '请选择活动阶段' }]}>
            <Select options={PHASE_OPTIONS} />
          </Form.Item>
          <Form.Item name="ruleVersionId" label="绑定规则版本"
            rules={[{ required: true, message: '请选择已启用的规则版本' }]}>
            <Select placeholder="选择规则版本" options={candidates.map((c: { id: string; versionLabel: string; briefTitle: string }) =>
              ({ value: c.id, label: `${c.briefTitle} · ${c.versionLabel}` }))} />
          </Form.Item>
          {ruleVersionId && detail.isPending && <Skeleton active paragraph={{ rows: 2 }} />}
          {ruleVersionId && detail.isError && (
            <Alert type="error" showIcon title="规则详情加载失败"
              description="无法验证兼容性，暂不能提交。请重试或重新选择版本。" />)}
          {compat && compat.ok && (
            <Alert type="success" showIcon title={`匹配 ${compat.matchedCount} 条已确认必须项`}
              description="兼容性以前端候选过滤为参考，最终以服务端校验为准。" />)}
          {compat && !compat.ok && compat.blockedReason && (
            <Alert type="warning" showIcon title={compat.blockedReason}
              description="该版本当前不可绑定；请先处理规则或更换版本。" />)}
          {compat && !compat.ok && !compat.blockedReason && (
            <Alert type="warning" showIcon title="无兼容的已确认必须项"
              description="该版本没有匹配当前平台/阶段/视频形式的必须项，请更换版本。" />)}
          <Form.Item style={{ marginBottom: 0 }}>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="ant-btn ant-btn-default" onClick={close}>取消</button>
              <button type="submit" className="ant-btn ant-btn-primary"
                disabled={flow.submitting || gateBlocked}>{flow.submitting ? '创建中…' : '创建任务'}</button>
            </div>
          </Form.Item>
        </Form>
      )}
    </Drawer>
  );
}


export function CreateTaskDrawer(props: { project: ProjectDetail; open: boolean; onClose: () => void }) {
  return <AddCreatorDrawer {...props} />;
}
