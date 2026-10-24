import { useLayoutEffect, useState } from 'react';
import type { RefObject } from 'react';

export function ReviewConnections({ container, activeId, revision }: { container: RefObject<HTMLDivElement | null>; activeId?: string; revision?: string }) {
  const [path, setPath] = useState('');
  useLayoutEffect(() => {
    const root = container.current; if (!root) return;
    let frame = 0;
    function measure() {
      frame = 0;
      const sources = [...root!.querySelectorAll<HTMLElement>('[data-source-comment]')];
      root!.querySelectorAll('[data-word-preview-host]').forEach(host => sources.push(...(host.shadowRoot?.querySelectorAll<HTMLElement>('[data-source-comment]') || [])));
      const comment = [...root!.querySelectorAll<HTMLElement>('[data-comment-target]')].find(el => el.dataset.commentTarget === activeId);
      if (!comment || window.innerWidth <= 1000) { setPath(''); return; }
      const parent = (node: HTMLElement): HTMLElement | null => node.parentElement || ((node.getRootNode() as ShadowRoot).host as HTMLElement | undefined) || null;
      const visible = (el: HTMLElement, rect: DOMRect) => {
        if (getComputedStyle(el).opacity === '0') return false;
        for (let node = parent(el); node && node !== root; node = parent(node)) {
          if (['auto', 'scroll', 'hidden'].includes(getComputedStyle(node).overflowY) || ['auto', 'scroll', 'hidden'].includes(getComputedStyle(node).overflowX)) {
            const clip = node.getBoundingClientRect();
            if (rect.bottom < clip.top || rect.top > clip.bottom || rect.right < clip.left || rect.left > clip.right) return false;
          }
        }
        return rect.width > 0 && rect.height > 0;
      };
      const source = sources.find(el => el.dataset.sourceComment === activeId && visible(el, el.getBoundingClientRect()));
      if (!source) { setPath(''); return; }
      const s = source.getBoundingClientRect(), t = comment.getBoundingClientRect(), base = root!.getBoundingClientRect();
      if (!visible(source, s) || !visible(comment, t)) { setPath(''); return; }
      const x1 = s.right - base.left, y1 = s.top + s.height / 2 - base.top, x2 = t.left - base.left, y2 = t.top + t.height / 2 - base.top;
      const bend = Math.max(20, (x2 - x1) * .45);
      setPath(`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const observer = new ResizeObserver(schedule);
    observer.observe(root);
    root.querySelectorAll('.ant-card-body,.sheet-canvas,.review-comment').forEach(el => observer.observe(el));
    const mutation = new MutationObserver(schedule); mutation.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
    root.addEventListener('scroll', schedule, true); root.addEventListener('load', schedule, true); window.addEventListener('resize', schedule);
    root.addEventListener('review-source-change', schedule);
    schedule();
    return () => { observer.disconnect(); mutation.disconnect(); root.removeEventListener('scroll', schedule, true); root.removeEventListener('load', schedule, true); root.removeEventListener('review-source-change', schedule); window.removeEventListener('resize', schedule); cancelAnimationFrame(frame); };
  }, [container, activeId, revision]);
  return <svg className="review-comment-connections" aria-hidden="true">{path && <><path d={path} className="comment-connector-halo" /><path d={path} className="comment-connector" /></>}</svg>;
}
