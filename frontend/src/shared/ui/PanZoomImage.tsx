import { useRef, useState } from 'react';
import { Alert, Button, Modal } from 'antd';
import { PreviewZoomControl, usePreviewPan, usePreviewWidth } from './PreviewNavigation';

type Props={src:string;alt:string;onError?:()=>void;zoomLabel?:string;className?:string;expanded?:boolean};

export function PanZoomImage(props:Props){return <ImageCanvas key={props.src} {...props} />;}

function ImageCanvas({src,alt,onError,zoomLabel='图片预览缩放',className='',expanded=false}:Props){
  const [zoom,setZoom]=useState(0),[natural,setNatural]=useState(0),[failed,setFailed]=useState(false),[enlarged,setEnlarged]=useState(false);
  const viewport=useRef<HTMLDivElement>(null);usePreviewPan(viewport);const available=usePreviewWidth(viewport);
  const fit=natural?Math.min(1,Math.max(1,available-24)/natural):1;
  return <div className={`preview-image-navigator ${className}`}>
    {!expanded&&<div className="preview-image-toolbar"><Button size="small" onClick={()=>setEnlarged(true)}>放大查看</Button></div>}
    <PreviewZoomControl value={zoom} onChange={setZoom} fitPercent={fit*100} label={zoomLabel} />
    <div ref={viewport} className="preview-image-viewport" aria-label={`${alt}预览画布`}>
      {failed?<Alert type="warning" title="图片无法读取" />:<img src={src} alt={alt} draggable={false} onLoad={event=>setNatural(event.currentTarget.naturalWidth)} onError={()=>{setFailed(true);onError?.();}} style={{width:natural?natural*(zoom?zoom/100:fit):'100%',maxWidth:'none',maxHeight:'none',height:'auto'}} />}
    </div>
    {!expanded&&<Modal title="图片预览" className="preview-image-modal" open={enlarged} onCancel={()=>setEnlarged(false)} width="calc(100vw - 48px)" style={{top:24,maxWidth:1600}} footer={null} destroyOnHidden><ImageCanvas src={src} alt={alt} zoomLabel={zoomLabel} expanded /></Modal>}
  </div>;
}
