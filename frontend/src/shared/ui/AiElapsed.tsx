import { useCallback, useEffect, useState } from 'react';
import './ai-elapsed.css';

export type AiTimedRun = { id?: string; status: string; createdAt?: string | null; updatedAt?: string | null; durationMs?: number | null; result?: unknown };
const runningStates = new Set(['queued', 'processing', 'vision', 'facts']);

export function elapsedLabel(run: AiTimedRun, now: number): string | null {
  const running = runningStates.has(run.status);
  const start = Date.parse(run.createdAt || '');
  const end = running ? now : Date.parse(run.updatedAt || '');
  const result = run.result && typeof run.result === 'object' ? run.result as Record<string, unknown> : {};
  const stored = typeof result.durationMs === 'number' ? result.durationMs : typeof result.elapsedMs === 'number' ? result.elapsedMs : run.durationMs;
  const milliseconds = !running && stored != null ? stored : end - start;
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null;
  const seconds = Math.max(0, running ? Math.floor(milliseconds / 1000) : Math.ceil(milliseconds / 1000));
  return running ? `已用时 ${seconds} 秒` : ['succeeded', 'needs_input'].includes(run.status) ? `${seconds} 秒完成`
    : run.status === 'interrupted' ? `${seconds} 秒 · 已中断` : ['blocked', 'superseded'].includes(run.status) ? `${seconds} 秒 · 已停止` : `${seconds} 秒 · 未完成`;
}

export function AiElapsed({ run }: { run?: AiTimedRun | null }) {
  const [clock, setClock] = useState(Date.now());
  const running = !!run && runningStates.has(run.status);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, run?.id, run?.createdAt]);
  const label = run ? elapsedLabel(run, Math.max(clock, Date.now())) : null;
  if (!label) return null;
  return <span className={`ai-elapsed${running ? ' is-running' : ''}`} role="timer" aria-label={`AI 处理用时：${label}`} title="处理用时包含排队、传输与结果校验，不代表模型纯推理时间。"><i aria-hidden />{label}</span>;
}

/** Local requests start immediately; stored server timestamps take over after navigation. */
export function useAiCallTiming(scope: string) {
  const [value, setValue] = useState<(AiTimedRun & { scope: string })>();
  const start = useCallback(() => {
    setValue({ scope, id: crypto.randomUUID(), status: 'processing', createdAt: new Date().toISOString() });
  }, [scope]);
  const finish = useCallback((status = 'succeeded', recorded?: AiTimedRun | null) => {
    setValue(previous => previous?.scope === scope && runningStates.has(previous.status) ? {
      ...previous, ...recorded, scope, status,
      createdAt: recorded?.createdAt || previous.createdAt,
      updatedAt: recorded?.updatedAt || new Date().toISOString(),
    } : previous);
  }, [scope]);
  const reset = useCallback(() => setValue(undefined), []);
  return { run: value?.scope === scope ? value : undefined, start, finish, reset };
}
