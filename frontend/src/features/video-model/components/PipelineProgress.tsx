import { Check, Film, ScanLine } from 'lucide-react';
import type { EvidenceRun } from '../api';

const labels: Record<string, string> = { localizing: '整片定位', preparing: '裁片准备', refining: '片段精判', finalizing: '合并结果', completed: '完成' };
const verdicts: Record<string, string> = { compliant: '通过', non_compliant: '不符合', needs_confirmation: '待确认', not_applicable: '不适用' };
const stageOrder = ['localizing', 'preparing', 'refining'] as const;
type Pipeline = { stage?: string; completedBatches?: number; totalBatches?: number; decisions?: { ruleId: string; initialVerdict: string; finalVerdict: string; refined: boolean }[]; batches?: { id: string; startMs: number; endMs: number; status: string; error?: string }[] };

export function PipelineProgress({ run }: { run: EvidenceRun }) {
  const result = run.result as { pipeline?: Pipeline; stageCalls?: { id: string; stage: string; status: string; durationMs?: number; requestId?: string }[]; modelResponses?: { requestId?: string; response: string }[] } | null;
  const pipeline = result?.pipeline;
  if (!pipeline) return null;
  const paused = ['interrupted', 'failed'].includes(run.status);
  const current = pipeline.stage === 'finalizing' ? 'refining' : pipeline.stage || 'localizing';
  const finished = pipeline.stage === 'completed';
  const done = (step: string) => finished || pipeline.stage === 'finalizing' || stageOrder.indexOf(step as typeof stageOrder[number]) < stageOrder.indexOf(current as typeof stageOrder[number]);
  const active = (step: string) => !finished && current === step;
  const rules = (run.snapshot.rules || []) as { id: string; text?: string; ruleNumber?: number }[];
  const ruleLabel = (id: string) => { const rule = rules.find(item => item.id === id); return rule ? `${rule.ruleNumber ? `第 ${rule.ruleNumber} 条 · ` : ''}${rule.text || id}` : id; };
  return <div className="video-pipeline-progress" aria-label="两阶段审核进度">
    <div className="video-pipeline-stages" role="status">
      {stageOrder.map((step, index) => {
        const isDone = done(step), isActive = active(step);
        return <span key={step} className={`pipeline-stage${isActive ? ' is-active' : ''}${isDone ? ' is-done' : ''}`}>
          {index > 0 && <b className="pipeline-arrow" aria-hidden>→</b>}
          {isDone ? <Check size={12} strokeWidth={2.5} /> : step === 'localizing' ? <ScanLine size={12} className={isActive ? 'pipeline-scan' : undefined} />
            : step === 'preparing' ? <Film size={12} /> : <span className={`pipeline-pulse${isActive ? '' : ' is-idle'}`} aria-hidden />}
          <span>{labels[step]}</span>
          {step === 'preparing' && !isDone && <span className="pipeline-frames" aria-hidden><i /><i /><i /></span>}
          {step === 'refining' && <span className="pipeline-count">{pipeline.completedBatches || 0}/{pipeline.totalBatches || 0}</span>}
        </span>;
      })}
      {finished && <span className="pipeline-stage is-done"><b className="pipeline-arrow" aria-hidden>→</b><Check size={12} strokeWidth={2.5} /><span>{labels.completed}</span></span>}
      {paused && <small className="pipeline-note">已停止，未自动重试</small>}
    </div>
    <details><summary>查看定位与精判诊断</summary>
      {pipeline.decisions?.map(decision => <p key={decision.ruleId}>{ruleLabel(decision.ruleId)}：{verdicts[decision.initialVerdict] || decision.initialVerdict} → {verdicts[decision.finalVerdict] || decision.finalVerdict} · {decision.refined ? '已精判' : '未经过片段精判'}</p>)}
      {pipeline.batches?.map(batch => <p key={batch.id}>{(batch.startMs / 1000).toFixed(1)}–{(batch.endMs / 1000).toFixed(1)} 秒 · {({ queued: '等待', preparing: '裁片中', processing: '精判中', succeeded: '已完成', partial: '部分待确认', failed: '失败', interrupted: '已中断', skipped: '已跳过' } as Record<string, string>)[batch.status] || batch.status}{batch.error && ` · ${batch.error}`}</p>)}
      {result?.stageCalls?.map(call => <p key={call.id}>{call.stage === 'localization' ? '整片定位' : call.stage === 'component' ? '组件精判' : '片段精判'} · {call.durationMs != null ? `${(call.durationMs / 1000).toFixed(1)} 秒` : '耗时未记录'} · {call.requestId || (call.status === 'submission_unknown' ? '提交状态待核实，未重试' : '请求ID未返回')}</p>)}
      {!!result?.modelResponses?.length && <details><summary>查看各次模型原始返回</summary>{result.modelResponses.map((response, index) => <pre key={index} className="provider-diagnostic">{response.requestId}{'\n'}{response.response}</pre>)}</details>}
    </details>
  </div>;
}