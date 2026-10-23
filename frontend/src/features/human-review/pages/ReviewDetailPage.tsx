import { Link, useParams } from 'react-router-dom';
import { Alert, Button, Card, Result, Skeleton } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { humanReviewApi, classifyDetailSuccess, classifyReviewsError } from '../api';
import { ErrorState, PageHeader } from '../../../shared/ui';

// 人工复核详情页（HR-02 骨架接入）：真实调用 GET /human-reviews/:id。
// 后端当前 501 未接入 —— 如实呈现；200 结构未知时不编造业务字段。
export function ReviewDetailPage() {
  const { reviewId } = useParams();
  const review = useQuery({
    queryKey: ['human-review', reviewId],
    queryFn: ({ signal }) => humanReviewApi.detail(reviewId ?? '', signal),
    enabled: Boolean(reviewId),
  });

  const state = review.isError ? classifyReviewsError(review.error)
    : review.isSuccess ? classifyDetailSuccess(review.data) : null;

  return <>
    <PageHeader title="人工复核详情" description="查看提交版本、AI 证据与裁决入口；只有当前有效版本才能获得最终决定。" />
    <div className="panel-padding">
      <p><Link to="/reviews">← 返回人工待办</Link></p>
    </div>
    <Card title={`复核 ${reviewId ?? '—'}`}>
      {review.isPending && <Skeleton active paragraph={{ rows: 6 }} />}
      {state?.kind === 'module-not-implemented' && (
        <Alert type="info" showIcon title="复核详情模块待接入"
          description="后端已明确返回“未接入”。模块接入后此处将呈现视频/脚本证据、逐条裁决与最终决定表单；当前不伪造任何裁决或结论。" />
      )}
      {state?.kind === 'contract-pending' && (
        <Alert type="info" showIcon title="数据契约尚未接入，暂不能展示详情"
          description="该复核记录存在，但详情字段契约尚未由后端提供；为避免编造数据，待契约接入后再渲染详情。" />
      )}
      {state?.kind === 'not-found' && (
        <Result status="404" title="复核记录不存在"
          subTitle="该待办不存在或已不可访问，请返回列表重新选择。"
          extra={<Link to="/reviews"><Button type="primary">返回人工待办</Button></Link>} />
      )}
      {state?.kind === 'error' && <ErrorState error={new Error(state.message)} retry={() => review.refetch()} />}
    </Card>
  </>;
}
