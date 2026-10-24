import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Spin } from 'antd';
import { scriptLayoutApi } from './scriptLayoutApi';
import { fileContentUrl } from '../../shared/api/client';
import { PreviewZoomControl, usePreviewPan } from '../../shared/ui/PreviewNavigation';
import { annotateWord, WORD_ANNOTATION_STYLE, type WordReviewProps, type WordSourceBlock } from './wordAnnotations';
import type { ScriptAnnotation } from './scriptAnnotations';

const EMPTY_BLOCKS:WordSourceBlock[]=[],EMPTY_NOTES:ScriptAnnotation[]=[];
const noop=()=>{};

export function WordDocumentPreview({ taskId, versionId, fileId, format, expanded = false,sourceMap=EMPTY_BLOCKS,annotations=EMPTY_NOTES,active,onSelect=noop,onLocateUnavailable }: { taskId: string; versionId: string; fileId: string; format: string; expanded?: boolean } & WordReviewProps) {
  const host = useRef<HTMLDivElement>(null);
  const sourceRoot=useRef<HTMLDivElement|null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  usePreviewPan(viewport);
  const [zoom, setZoom] = useState(0), [fitPercent,setFitPercent] = useState(100), [attempt, setAttempt] = useState(0), [enlarged, setEnlarged] = useState(false);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [pages, setPages] = useState(0);
  const [renderEpoch,setRenderEpoch]=useState(0);
  const resize = useRef<(() => void) | null>(null);
  const selectedZoom = useRef(zoom);selectedZoom.current = zoom;
  useEffect(() => {
    const element = host.current!;
    // Scope document fonts, tables and paragraph styles away from the application.
    const shadow = element.shadowRoot || element.attachShadow({ mode: 'open' });
    shadow.replaceChildren();
    const body = document.createElement('div'), styles = document.createElement('div');
    sourceRoot.current=body;
    shadow.append(styles, body);
    const abort = new AbortController();let cancelled = false;
    setLoading(true);setError('');
    const fit = () => {
      const widths = [...body.querySelectorAll<HTMLElement>('section.review-word')].map(page => page.offsetWidth);
      const width = Math.max(1,...widths);
      const automatic=Math.min(1, element.clientWidth / width);
      setFitPercent(Math.round(automatic*1000)/10);
      body.style.width=`${width}px`;
      body.style.margin='0 auto';
      body.style.zoom = String(selectedZoom.current ? selectedZoom.current/100 : automatic);
      element.dispatchEvent(new Event('review-source-change',{bubbles:true}));
    };
    const observer = new ResizeObserver(fit);observer.observe(element);resize.current = fit;
    void (async () => {
      try {
        const response = await fetch(scriptLayoutApi.documentUrl(taskId, versionId), { signal: abort.signal });
        if (!response.ok) throw new Error('原文件读取失败，请重试或下载原文件。');
        const data = await response.arrayBuffer();
        const { renderAsync } = await import('docx-preview');
        if (cancelled) return;
        await renderAsync(data, body, styles, { className: 'review-word', inWrapper: true, breakPages: true, ignoreLastRenderedPageBreak: false, useBase64URL: true, renderAltChunks: false });
        if (cancelled) return;
        const localStyle = document.createElement('style');
        localStyle.textContent = '.review-word-wrapper{padding:0;background:transparent;align-items:center}section.review-word{margin:0 auto 16px;box-shadow:0 2px 9px #17243b16}'+WORD_ANNOTATION_STYLE;
        shadow.append(localStyle);
        // Links are displayed as source content, never used as application navigation.
        body.querySelectorAll('a').forEach(link => { link.removeAttribute('href'); });
        setPages(body.querySelectorAll('section.review-word').length);fit();
        setRenderEpoch(value=>value+1);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Word 原布局无法读取');
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true;abort.abort();observer.disconnect();resize.current = null; };
  }, [taskId,versionId,attempt]);
  useEffect(() => { resize.current?.(); }, [zoom]);
  useEffect(()=>{
    const body=sourceRoot.current;if(!body||!renderEpoch||loading)return;
    const linked=annotateWord(body,fileId,sourceMap,annotations,active?.id,onSelect);
    host.current?.dispatchEvent(new Event('review-source-change',{bubbles:true}));
    if(!active)return;
    if(!linked.has(active.id)){
      if(annotations.find(note=>note.id===active.id)?.anchors.length)onLocateUnavailable?.();
      return;
    }
    const frame=requestAnimationFrame(()=>{
      const marker=[...body.querySelectorAll<HTMLElement>('[data-source-comment]')].find(node=>node.dataset.sourceComment===active.id);
      const view=viewport.current;if(!marker||!view)return;
      const box=marker.getBoundingClientRect(),clip=view.getBoundingClientRect();
      view.scrollTo({top:view.scrollTop+box.top-clip.top-(clip.height-box.height)/2,left:view.scrollLeft+box.left-clip.left-(clip.width-box.width)/2,behavior:'smooth'});
    });
    return ()=>cancelAnimationFrame(frame);
  },[renderEpoch,loading,fileId,sourceMap,annotations,active,onSelect,onLocateUnavailable]);
  return <div className="document-original-preview">
    <div className="document-original-toolbar"><span>{format.toUpperCase()}{pages ? ` · ${pages} 页` : ''}</span>{!expanded && <Button size="small" onClick={() => setEnlarged(true)}>放大查看</Button>}</div>
    <PreviewZoomControl value={zoom} onChange={setZoom} fitPercent={fitPercent} label="原布局缩放" />
    {loading && <Spin description="正在读取 Word 原布局" ><div style={{ height: 90 }} /></Spin>}
    {error && <Alert type="warning" title="Word 原布局暂时无法显示" description={<>{error} <a href={fileContentUrl(fileId)} target="_blank" rel="noreferrer">下载原文件</a></>} action={<Button onClick={() => setAttempt(v => v+1)}>重试</Button>} />}
    <div ref={viewport} className="document-original-scroll" aria-label="文档原布局预览"><div ref={host} data-word-preview-host /></div>
    {!expanded && <Modal title="Word 原布局" className="document-preview-modal" open={enlarged} onCancel={() => setEnlarged(false)} width="calc(100vw - 48px)" style={{ top: 24, maxWidth: 1600 }} footer={null} destroyOnHidden>
      <WordDocumentPreview taskId={taskId} versionId={versionId} fileId={fileId} format={format} expanded sourceMap={sourceMap} annotations={annotations} active={active} onSelect={id=>{setEnlarged(false);onSelect(id);}} onLocateUnavailable={onLocateUnavailable} />
    </Modal>}
  </div>;
}
