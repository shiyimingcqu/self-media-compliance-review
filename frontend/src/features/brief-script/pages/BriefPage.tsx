// Brief 页编排（t-26 二轮）：路由 params.briefId 为唯一 ID 事实；登记成功后导航真实路由并在
// ['brief-blocks', briefId] 缓存本次 blocks（刷新即缺，显式声明不伪恢复）。
// 版本选择：URL ?version 优先，其次界面选择，均须命中该 Brief 真实列表（pickActiveVersion），
// 切 briefId 重置暂态选择；版本/来源上下文取 detail 真值（resolveSourceContext），不拿 latest 源冒用。
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Breadcrumb, Button, Card, Empty, Result, Select, Skeleton, Space, Tag, Typography } from 'antd';
import { projectApi } from '../../projects/api';
import { briefApi, newKey, KeyedOp, OperationRunner, pickActiveVersion, previewGuard, versionSourceOf, versionSyncKeys, classifyBriefError, coverageBodyOf, ruleApi, fileApi, enableBlockerReasons, type SourceContext } from '../api';
import type { BriefBlocksCache } from '../api';
import { BriefUploadPanel } from '../components/BriefUploadPanel';
import { SourcePreview } from '../components/SourcePreview';
import { RuleDraftPanel } from '../components/RuleDraftPanel';
import { CoverageSignPanel, type CoverageSignHeader, type CoverageSignItem } from '../components/CoverageSignPanel';

export function BriefPage() {
  const { projectId = '', briefId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const coverageRunnerRef = useRef<OperationRunner | null>(null);
  coverageRunnerRef.current ??= new OperationRunner(() => newKey());
  const coverageRunner = coverageRunnerRef.current;

  const urlVersion = searchParams.get('version');
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const creatingRef = useRef(false);
  const draftKeyRef = useRef<KeyedOp | null>(null);
  draftKeyRef.current ??= new KeyedOp(() => newKey());

  useEffect(() => { setSelectedVersionId(null); setDraftError(null); }, [briefId]); // 切 Brief 重置暂态

  const projectQuery = useQuery({
    queryKey: ['project', projectId], queryFn: () => projectApi.detail(projectId), enabled: !!projectId,
  });
  const project = projectQuery.data?.data;
  const briefSummary = useMemo(() => (project?.briefs ?? []).find((b) => b.id === briefId) ?? null,
    [project, briefId]);
  const versionsQuery = useQuery({
    queryKey: ['brief-rule-versions', briefSummary?.id],
    queryFn: () => briefApi.listVersions(briefSummary!.id).then((r) => r.data),
    enabled: !!briefSummary,
  });
  const versions = versionsQuery.data ?? [];
  const activeVersionId = pickActiveVersion(versions, urlVersion, selectedVersionId);
  const detailQuery = useQuery({
    queryKey: ['rule-version', activeVersionId],
    queryFn: () => ruleApi.detail(activeVersionId!).then((r) => r.data),
    enabled: !!activeVersionId,
  });
  // t-30：来源唯一事实=ProjectDetail 投影中该版本自带 source（禁 latest 回退、禁跨 Brief）。
  const sourceContext: SourceContext | null = versionSourceOf(briefSummary?.versions, activeVersionId);
  // 同一源的原登记缓存只能作加速展示，不能替代持久 preview 验证；key 含 fileId+sourceVersionId。
  const previewQuery = useQuery({
    queryKey: ['brief-preview', sourceContext?.fileId, sourceContext?.sourceVersionId],
    queryFn: ({ signal }) => fileApi.preview(sourceContext!.fileId, sourceContext!.sourceVersionId, signal)
      .then((r) => r.data),
    enabled: !!sourceContext,
  });
  const guarded = previewQuery.data && sourceContext
    ? (previewGuard(sourceContext, previewQuery.data).ok ? previewQuery.data : null)
    : null;
  const previewMismatch = previewQuery.data && sourceContext
    ? !previewGuard(sourceContext, previewQuery.data).ok : false;
  const blocksCache = queryClient.getQueryData<BriefBlocksCache>(['brief-blocks', briefId]) ?? null;
  const previewBlocks = guarded?.blocks ?? null;
  const notReady = previewQuery.isError
    && classifyBriefError(previewQuery.error).kind === 'conflict'; // 409 PREVIEW_NOT_READY
  const notReadyStatus = notReady && 'details' in (previewQuery.error as object)
    ? (previewQuery.error as { details?: { extractionStatus?: string } }).details?.extractionStatus
    : undefined; // client ApiError 目前不透传 details：缺失时下方文案如实"服务端未回传状态"，不猜 invalid

  const breadcrumb = (leaf: string) => (
    <Breadcrumb items={[{ title: <Link to="/projects">项目列表</Link> },
      { title: <Link to={`/projects/${projectId}`}>项目详情</Link> }, { title: leaf }]} />
  );

  if (projectId && projectQuery.isPending) return <Skeleton active paragraph={{ rows: 8 }} />;
  if (projectId && (projectQuery.isError || !project)) {
    return <>{breadcrumb('Brief')}{projectQuery.isError
      ? <Alert type="error" showIcon message="项目读取失败" description="真值读取异常，不做本地乐观展示" />
      : <Alert type="error" showIcon message="项目不存在或不属于当前模式" />}</>;
  }
  if (!briefId) {
    return <>{breadcrumb('登记 Brief')}<div style={{ marginTop: 16 }}><BriefRouteHost /></div></>;
  }
  if (!briefSummary) {
    return <>
      {breadcrumb('Brief')}
      <Result status="warning" title="Brief 不存在或不属于此项目"
        subTitle="路由 briefId 未命中真实投影（不伪造数据、不使用占位 ID）"
        extra={<Link to={`/projects/${projectId}/briefs`}><Button type="primary">去登记真实 Brief</Button></Link>} />
    </>;
  }
  if (versionsQuery.isPending) return <>{breadcrumb(briefSummary.title)}<Skeleton active paragraph={{ rows: 6 }} /></>;

  const pickVersion = (id: string | null) => {
    setSelectedVersionId(id);
    const next = new URLSearchParams(searchParams);
    if (id) next.set('version', id); else next.delete('version');
    setSearchParams(next, { replace: true });
  };

  // t-41 补录签署：提交既有 coverage-confirmation；成功仅凭真值刷新（详情+项目投影），失败保留输入。

  const submitCoverage = async (items: CoverageSignItem[], header: CoverageSignHeader,
                                versionId: string, revision: number) => {
    // 请求体组装走 api 纯函数（顶层 statement/reason 必填；note 为每页说明）
    const body = coverageBodyOf(items, header);
    await coverageRunner.run('coverage', (key) =>
      ruleApi.coverage(versionId, body, key, revision).then((r) => r.data), {
      signature: `${versionId}:${revision}:${JSON.stringify(body)}`,
      onRefresh: async () => {
        // 仅真值刷新：拿新 coverage 的 detail + 项目投影；不本地乐观写入
        const keys = versionSyncKeys(projectId, briefId);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['rule-version', versionId] }),
          queryClient.invalidateQueries({ queryKey: keys.versions }),
          queryClient.invalidateQueries({ queryKey: keys.project }),
        ]);
      },
      onError: (_kind, msg) => setDraftError(msg),
    });
  };

  const createFirstDraft = async () => {
    if (creatingRef.current || !briefSummary.latestSourceVersionId) return;
    creatingRef.current = true;
    setDraftBusy(true);
    setDraftError(null);
    try {
      // 稳定 key（t-26 约束不回退）：同 brief+同绑定源=同一逻辑操作，失败重试复用；成功 reset。
      const draftOp = draftKeyRef.current ??= new KeyedOp(() => newKey());
      const key = draftOp.ensure(`${briefSummary.id}:${briefSummary.latestSourceVersionId}`);
      const child = await briefApi.createVersion(briefSummary.id,
        { sourceVersionId: briefSummary.latestSourceVersionId }, key);
      draftOp.reset();
      // 新版本必须让 project 投影（versions[].source）与列表同步，否则 preview 假性不可用。
      const keys = versionSyncKeys(projectId, briefSummary.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.versions }),
        queryClient.invalidateQueries({ queryKey: keys.project }),
      ]);
      pickVersion(child.data.id);
    } catch (e) {
      setDraftError(classifyBriefError(e).message); // 失败：key 不 reset，重试同 key
    } finally {
      creatingRef.current = false;
      setDraftBusy(false);
    }
  };

  return <>
    {breadcrumb(briefSummary.title)}
    <Card size="small" style={{ margin: '16px 0' }}>
      <Space wrap>
        <Typography.Text strong>{briefSummary.title}</Typography.Text>
        <Tag>{briefSummary.platforms.length ? briefSummary.platforms.join('/') : '平台未设置（旧登记，需重新登记补范围）'}</Tag>
        <Tag color={briefSummary.extractionStatus === 'ready' ? 'green' : 'orange'}>
          源解析：{briefSummary.extractionStatus ?? '无可用源'}
        </Tag>
        {versions.length > 0 && (
          <Select size="small" style={{ minWidth: 240 }} value={activeVersionId ?? undefined}
            options={versions.map((v) => ({ value: v.id, label: `${v.versionLabel}（${v.state}，r${v.revision}）` }))}
            onChange={(v: string) => pickVersion(v)} />
        )}
        {versions.length === 0 && (
          <Button size="small" type="primary" loading={draftBusy}
            disabled={!briefSummary.latestSourceVersionId} onClick={() => void createFirstDraft()}>
            {briefSummary.latestSourceVersionId ? '创建首个规则草稿' : '无解析就绪源，无法建草稿'}
          </Button>
        )}
      </Space>
      {draftError && <Alert type="error" showIcon style={{ marginTop: 8 }} message={draftError} />}
      {versions.length > 0 && !activeVersionId && (
        <Alert type="warning" showIcon style={{ marginTop: 8 }} message="URL version 不属于该 Brief，已回落真实列表" />)}
    </Card>
    <div className="review-grid">
      <SourcePreview
        blocks={previewBlocks}
        state={sourceContext === null ? 'no-source'
          : previewMismatch ? 'mismatch'
          : notReady ? (notReadyStatus ? `not-ready:${notReadyStatus}` : 'not-ready')
          : previewQuery.isFetching && !previewBlocks ? 'loading'
          : previewBlocks ? 'ready' : 'error'}
        registeredCache={blocksCache?.blocks ?? null}
        meta={guarded ? { pageCount: guarded.pageCount, uncoveredSources: guarded.uncoveredSources } : null} />
      {activeVersionId && detailQuery.data ? (
        <>
          <RuleDraftPanel detail={detailQuery.data} versionId={activeVersionId}
            briefPlatforms={briefSummary.platforms}
            blocks={previewBlocks}
            sourceContext={sourceContext}
            uncovered={guarded?.uncoveredSources ?? []}
            onVersionCreated={async (id: string) => {
              const keys = versionSyncKeys(projectId, briefSummary.id);
              await Promise.all([
                queryClient.invalidateQueries({ queryKey: keys.versions }),
                queryClient.invalidateQueries({ queryKey: keys.project }),
              ]);
              pickVersion(id);
            }} />
          {/* t-41：未覆盖页逐页补录签署（受控版本 + 未全部签署则 enable 门禁显示缺页） */}
          {(() => {
            const uncovered = guarded?.uncoveredSources ?? [];
            const reasons = enableBlockerReasons(detailQuery.data, uncovered);
            return <>
              {reasons.length > 0 && (
                <Alert type="warning" showIcon style={{ marginTop: 12 }}
                  message="「确认并启用」当前被阻止"
                  description={<ul style={{ margin: 0, paddingLeft: 18 }}>
                    {reasons.map((r) => <li key={r}>{r}</li>)}
                  </ul>} />)}
              <div style={{ marginTop: 12 }}>
                <CoverageSignPanel
                  versionId={activeVersionId}
                  revision={detailQuery.data.version.revision}
                  uncovered={uncovered}
                  requirements={detailQuery.data.requirements ?? []}
                  busy={coverageRunner.anyBusy()}
                  onSubmit={(items, header) => submitCoverage(
                    items, header, activeVersionId, detailQuery.data!.version.revision)} />
              </div>
            </>;
          })()}
        </>
      ) : detailQuery.isError ? (
        <Card title="审核要求"><Alert type="error" showIcon message="规则版本读取失败" description="真值接口异常，禁止本地乐观操作" /></Card>
      ) : activeVersionId ? (
        <Card title="审核要求"><Typography.Text type="secondary">加载中…</Typography.Text></Card>
      ) : (
        <Empty description="该 Brief 暂无规则版本" />
      )}
    </div>
  </>;
}

// 登记路由宿主：/projects/:projectId/briefs 无 :briefId 时呈现上传面板（真实投影提供平台子集）。
function BriefRouteHost() {
  const { projectId = '' } = useParams();
  const projectQuery = useQuery({ queryKey: ['project', projectId], queryFn: () => projectApi.detail(projectId) });
  return <BriefUploadPanel projectId={projectId} projectPlatforms={projectQuery.data?.data.platforms ?? []} />;
}
