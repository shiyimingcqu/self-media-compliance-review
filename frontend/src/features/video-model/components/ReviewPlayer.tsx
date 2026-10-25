import { useEffect, useRef, useState, type RefObject } from 'react';
import * as Tooltip from '@radix-ui/react-tooltip';
import { Play, Pause, SkipBack, SkipForward, Volume2, VolumeX, Film } from 'lucide-react';
import { Button } from '../../../components/ui/button';
import { formatVideoTime } from '../../../shared/lib/format';
import { highlightRanges } from '../reviewPresentation';
export type EvidenceAnchor = { id: string; key?: string; endMs?: number; label: string; ms: number; status: string; thumbnail?: string };
export function ReviewPlayer({ src, player, anchors, durationHint = 0, fps, onSeek, hoverId, filter = 'all' }: { src: string; player: RefObject<HTMLVideoElement | null>; anchors: EvidenceAnchor[]; durationHint?: number; fps?: number; onSeek: (ms: number, issueId?: string) => void; hoverId?: string; filter?: string }) {
  const [time, setTime] = useState(0), [duration, setDuration] = useState(durationHint), [playing, setPlaying] = useState(false), [muted, setMuted] = useState(false), [error, setError] = useState('');
  // 悬停只放大"那一条"：同一条规则可能有多处定位，取离当前播放头最近的一个。
  const [raisedKey, setRaisedKey] = useState<string>();
  const timeRef = useRef(0);
  useEffect(() => { timeRef.current = time; }, [time]);
  useEffect(() => {
    if (!hoverId) { setRaisedKey(undefined); return; }
    const group = anchors.filter(anchor => anchor.id === hoverId);
    if (!group.length) { setRaisedKey(undefined); return; }
    const now = timeRef.current;
    const nearest = group.reduce((best, anchor) => Math.abs(anchor.ms / 1000 - now) < Math.abs(best.ms / 1000 - now) ? anchor : best);
    setRaisedKey(nearest.key || nearest.id);
    // time 故意不进依赖：悬停期间播放头走动不应该让放大的标记跳来跳去。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoverId, anchors]);
  useEffect(() => { setTime(0); setDuration(durationHint); setPlaying(false); setError(''); }, [src, durationHint]);
  const full = Math.max(duration, durationHint);
  // 悬停时按"一段一段"高亮：同一条规则的定位点先合并成连续区段，再在轨道上铺半透明色带。
  const bands = hoverId ? highlightRanges(anchors.filter(anchor => anchor.id === hoverId)) : [];
  const bandMs = (value: number) => Math.min(99.5, Math.max(0, full ? value / 1000 / full * 100 : 0));
  const legends = { fail: '未通过定位', uncertain: '待确定定位', pass: '通过定位', advisory: '建议项定位' };
  // 图例跟着右侧分类走，避免"只显示红条、图例却写着待确定"的错位。
  const legend = filter === 'all' ? Object.entries(legends).map(([status, label]) => ({ status, label }))
    : filter === 'failed' ? [{ status: 'fail', label: legends.fail }]
    : filter === 'uncertain' ? [{ status: 'uncertain', label: legends.uncertain }]
    : filter === 'passed' ? [{ status: 'pass', label: legends.pass }]
    : filter === 'advisory' ? [{ status: 'advisory', label: legends.advisory }]
    : Object.entries(legends).map(([status, label]) => ({ status, label }));
  function seek(seconds: number) { const next = Math.min(full || Infinity, Math.max(0, seconds)); onSeek(next * 1000); setTime(next); }
  function toggle() { if (!player.current || !src) return; if (player.current.paused) void player.current.play().catch(() => setError('当前素材暂时无法播放，请核对原文件。')); else player.current.pause(); }
  return <section className="desk-player" tabIndex={0} aria-label="视频播放器与证据时间轴" onKeyDown={event => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLButtonElement) return;
    if (['j','k','l','ArrowLeft','ArrowRight',' '].includes(event.key)) event.preventDefault();
    if (event.key === 'k') player.current?.pause(); else if (event.key === 'l') { if (player.current?.paused) toggle(); } else if (event.key === 'j') seek(time - 5);
    else if (event.key === 'ArrowLeft' && fps) seek(time - 1 / fps); else if (event.key === 'ArrowRight' && fps) seek(time + 1 / fps); else if (event.key === ' ') toggle();
  }}>
    <div className="desk-player-frame"><span className="player-corner-label">ORIGINAL / 原片</span>{src ? <video className="review-original-video" ref={player} preload="metadata" playsInline src={src} onLoadedMetadata={event => { const value = event.currentTarget.duration; if (Number.isFinite(value)) setDuration(value); }} onTimeUpdate={event => setTime(event.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setError('视频预览不可用，可重新选择完整 MP4 文件。')} /> : <div className="player-empty"><Film size={24} strokeWidth={1} /><span>等待完整视频</span></div>}</div>
    <div className="desk-player-controls"><Button variant="ghost" size="icon" aria-label={playing ? '暂停视频' : '播放视频'} disabled={!src} onClick={toggle}>{playing ? <Pause size={14} /> : <Play size={14} />}</Button><Button variant="ghost" size="icon" aria-label="后退一帧" disabled={!src || !fps} title={fps ? `${fps} fps` : '素材帧率暂不可用'} onClick={() => seek(time - 1 / fps!)}><SkipBack size={13} /></Button><Button variant="ghost" size="icon" aria-label="前进一帧" disabled={!src || !fps} onClick={() => seek(time + 1 / fps!)}><SkipForward size={13} /></Button><span className="player-time mono">{formatVideoTime(time * 1000)} <span>/ {full ? formatVideoTime(full * 1000) : '—:—'}</span></span><select aria-label="播放倍速" defaultValue="1" disabled={!src} onChange={event => { if (player.current) player.current.playbackRate = Number(event.target.value); }}><option value="0.5">0.5×</option><option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select><Button variant="ghost" size="icon" aria-label={muted ? '开启声音' : '静音'} disabled={!src} onClick={() => { if (player.current) player.current.muted = !muted; setMuted(!muted); }}>{muted ? <VolumeX size={14} /> : <Volume2 size={14} />}</Button></div>
    <Tooltip.Provider delayDuration={200}><div className="desk-timeline"><div className="timeline-played" style={{ width: `${full ? Math.min(100, time / full * 100) : 0}%` }} />{bands.map(([start, end], index) => <span key={`band-${index}`} className={`timeline-highlight state-${anchors.find(anchor => anchor.id === hoverId)?.status || 'uncertain'}`} style={{ left: `${bandMs(start)}%`, width: `${Math.max(0.4, bandMs(end) - bandMs(start))}%` }} />)}<input type="range" aria-label="视频播放进度" min="0" max={full || 1} step="0.001" value={Math.min(time, full || 1)} disabled={!src || !full} onChange={event => seek(Number(event.target.value))} />{full > 0 && anchors.map(anchor => <Tooltip.Root key={anchor.key || anchor.id}><Tooltip.Trigger asChild><button className={`timeline-anchor state-${anchor.status}${raisedKey && (anchor.key || anchor.id) === raisedKey ? ' is-raised' : ''}`} style={{ left: `${Math.min(98, anchor.ms / 1000 / full * 100)}%` }} aria-label={`${anchor.label}，${formatVideoTime(anchor.ms)}`} onClick={() => { onSeek(anchor.ms, anchor.id); setTime(anchor.ms / 1000); }} /></Tooltip.Trigger><Tooltip.Portal><Tooltip.Content className="timeline-preview" sideOffset={8}>{anchor.thumbnail && <img src={anchor.thumbnail} alt="证据时间点预览" />}<span className="mono">{formatVideoTime(anchor.ms)}</span><small>{anchor.label}</small><Tooltip.Arrow className="timeline-tooltip-arrow" /></Tooltip.Content></Tooltip.Portal></Tooltip.Root>)}<span className="timeline-playhead" style={{ left: `${full ? Math.min(99, time / full * 100) : 0}%` }} /></div></Tooltip.Provider>
    <div className="timeline-ruler mono">{[0,.25,.5,.75,1].map(value => <span key={value}>{full ? formatVideoTime(value * full * 1000) : '—:—'}</span>)}</div><div className="timeline-legend">{legend.map(item => <span key={item.status} className={`state-${item.status}`}>● {item.label}</span>)}</div>{error && <p className="player-error">{error}</p>}
  </section>;
}
