import { useRef, useState } from 'react';
import { Alert, Button } from 'antd';
import { fileContentUrl } from '../../shared/api/client';
import { scriptLayoutApi, type OriginalLayout } from './scriptLayoutApi';
import './document-preview.css';
import { WordDocumentPreview } from './WordDocumentPreview';
import { PreviewZoomControl, usePreviewPan, usePreviewWidth } from '../../shared/ui/PreviewNavigation';
import type { WordReviewProps } from './wordAnnotations';

function TextDocumentPreview({text}:{text:string}) {
  const [zoom,setZoom]=useState(0);const viewport=useRef<HTMLDivElement>(null);
  usePreviewPan(viewport);const width=usePreviewWidth(viewport);
  return <><PreviewZoomControl value={zoom} onChange={setZoom} label="原布局缩放" /><div ref={viewport} className="document-original-scroll" aria-label="文档原布局预览"><pre className="document-original-text" aria-label="TXT 原文预览" style={{width:width?Math.max(1,width-32):'auto',zoom:(zoom||100)/100,maxHeight:'none',overflow:'visible'}}>{text}</pre></div></>;
}

export function DocumentPreview({ taskId, versionId, fileId, layout,...review }: { taskId: string; versionId: string; fileId: string; layout: OriginalLayout } & WordReviewProps) {
  const [zoom, setZoom] = useState(0);
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const [retry, setRetry] = useState(0);
  const viewport=useRef<HTMLDivElement>(null);usePreviewPan(viewport);const available=usePreviewWidth(viewport);
  if (layout.kind === 'text') return <TextDocumentPreview text={layout.text} />;
  if (layout.kind === 'docx') return <WordDocumentPreview taskId={taskId} versionId={versionId} fileId={fileId} format={layout.format} {...review} />;
  return <div className="document-original-preview">
    <div className="document-original-toolbar"><span>{layout.format.toUpperCase()} · {layout.pages.length} 页</span></div>
    <PreviewZoomControl value={zoom} onChange={setZoom} fitPercent={available&&layout.pages[0]?Math.min(100,(available-32)/(layout.pages[0].width*96/72)*100):100} label="原布局缩放" />
    <div ref={viewport} className="document-original-scroll" aria-label="文档原布局预览">{layout.pages.map(page => <figure className="document-original-page" key={page.page} style={{ width: zoom ? page.width * 96 / 72 * zoom/100 : available?Math.min(page.width*96/72,Math.max(1,available-32)):'100%' }}>
      {failed[page.page] ? <Alert type="warning" title={`第 ${page.page} 页加载失败`} action={<Button size="small" aria-label={`重新加载第 ${page.page} 页`} onClick={() => { setFailed(old => ({ ...old, [page.page]: false })); setRetry(v => v + 1); }}>重试</Button>} description={<a href={fileContentUrl(fileId)} target="_blank" rel="noreferrer">下载原文件核对</a>} /> : <img key={retry} src={`${scriptLayoutApi.pageUrl(taskId, versionId, page.page)}?v=${retry}`} alt={`原文件第 ${page.page} 页`} loading="lazy" decoding="async" width={page.width} height={page.height} onError={() => setFailed(old => ({ ...old, [page.page]: true }))} />}
      <figcaption>第 {page.page} / {layout.pages.length} 页</figcaption>
    </figure>)}</div>
  </div>;
}
