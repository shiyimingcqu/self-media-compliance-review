import { useLayoutEffect, useState } from 'react';
import type { RefObject } from 'react';

export function ReviewConnections({ container, activeId, revision }: { container: RefObject<HTMLDivElement | null>; activeId?: string; revision?: string }) {
  const [path, setPath] = useState('');
  useLayoutEffect(() => {
    const root = container.current; if (!root) return;
    let frame = 0;
    function measure() {
      frame = 0;
      const source = [...root!.querySelectorAll<HTMLElement>('[data-source-comment]')].find(el => el.dataset.sourceComment === activeId);
      const comment = [...root!.querySelectorAll<HTMLElement>('[data-comment-target]')].find(el => el.dataset.commentTarget === activeId);
      if (!source || !comment || window.innerWidth <= 1000) { setPath(''); return; }
      const s = source.getBoundingClientRect(), t = comment.getBoundingClientRect(), base = root!.getBoundingClientRect();
      const visible = (el: HTMLElement, rect: DOMRect) => {
        for (let node = el.parentElement; node && node !== root; node = node.parentElement) {
          if (['auto', 'scroll', 'hidden'].includes(getComputedStyle(node).overflowY) || ['auto', 'scroll', 'hidden'].includes(getComputedStyle(node).overflowX)) {
            const clip = node.getBoundingClientRect();
            if (rect.bottom < clip.top || rect.top > clip.bottom || rect.right < clip.left || rect.left > clip.right) return false;
          }
        }
        return rect.width > 0 && rect.height > 0;
      };
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
    schedule();
    return () => { observer.disconnect(); mutation.disconnect(); root.removeEventListener('scroll', schedule, true); root.removeEventListener('load', schedule, true); window.removeEventListener('resize', schedule); cancelAnimationFrame(frame); };
  }, [container, activeId, revision]);
  return <svg className="review-comment-connections" aria-hidden="true">{path && <><path d={path} className="comment-connector-halo" /><path d={path} className="comment-connector" /></>}</svg>;
}
