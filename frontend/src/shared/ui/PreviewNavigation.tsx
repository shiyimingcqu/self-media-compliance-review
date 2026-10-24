import { useEffect, useState, type RefObject } from 'react';
import { Select, Slider } from 'antd';
import './preview-navigation.css';

/** Zero is the automatic fit-to-width mode; other values are percentages. */
export function PreviewZoomControl({ value, onChange, fitPercent = 100, label = '预览缩放' }: { value: number; onChange: (value: number) => void; fitPercent?: number; label?: string }) {
  const levels = [...new Set([50,75,100,125,150,200,300,...(value ? [value] : [])])].sort((a,b) => a-b);
  return <div className="preview-zoom-control">
    <Slider min={1} max={300} step={1} value={Math.max(1,Math.min(300,value || fitPercent))} onChange={onChange} ariaLabelForHandle={`${label}滑块`} tooltip={{formatter: number => `${number}%`}} />
    <Select aria-label={label} size="small" value={value} onChange={onChange} options={[{value:0,label:'适应宽度'},...levels.map(level=>({value:level,label:`${level}%`}))]} />
  </div>;
}

export function usePreviewWidth(ref: RefObject<HTMLElement | null>) {
  const [width,setWidth] = useState(0);
  useEffect(() => {
    const element=ref.current;if (!element) return;
    const measure=()=>setWidth(element.clientWidth);
    const observer=new ResizeObserver(measure);observer.observe(element);measure();
    return ()=>observer.disconnect();
  },[ref]);
  return width;
}

/** Mouse panning leaves touch scrolling and source-comment controls intact. */
export function usePreviewPan(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const element=ref.current;if (!element) return;
    element.dataset.previewPan='true';
    let start: {x:number;y:number;left:number;top:number;pointer:number;dragged:boolean} | undefined;
    let suppressClick=false, timer: ReturnType<typeof setTimeout> | undefined;
    const down=(event:PointerEvent)=>{
      if(event.pointerType!=='mouse'||event.button!==0)return;
      if(event.composedPath().some(node=>node instanceof Element && node.matches('button,a,input,select,textarea,[role="button"],[role="slider"],video,audio,[contenteditable="true"]')))return;
      start={x:event.clientX,y:event.clientY,left:element.scrollLeft,top:element.scrollTop,pointer:event.pointerId,dragged:false};
    };
    const move=(event:PointerEvent)=>{
      if(!start||event.pointerId!==start.pointer)return;
      if(!(event.buttons&1)){end();return;}
      if(!start.dragged && Math.hypot(event.clientX-start.x,event.clientY-start.y)<4)return;
      if(!start.dragged){start.dragged=true;element.classList.add('is-preview-dragging');element.setPointerCapture(event.pointerId);window.getSelection()?.removeAllRanges();}
      event.preventDefault();element.scrollLeft=start.left-(event.clientX-start.x);element.scrollTop=start.top-(event.clientY-start.y);
    };
    const end=()=>{
      if(!start)return;
      suppressClick=start.dragged;
      if(element.hasPointerCapture(start.pointer))element.releasePointerCapture(start.pointer);
      start=undefined;element.classList.remove('is-preview-dragging');
      clearTimeout(timer);timer=setTimeout(()=>{suppressClick=false;},0);
    };
    const click=(event:MouseEvent)=>{if(suppressClick){event.preventDefault();event.stopImmediatePropagation();}};
    const nativeDrag=(event:DragEvent)=>event.preventDefault();
    element.addEventListener('pointerdown',down);element.addEventListener('pointermove',move);
    element.addEventListener('pointerup',end);element.addEventListener('pointercancel',end);element.addEventListener('lostpointercapture',end);
    element.addEventListener('click',click,true);element.addEventListener('dragstart',nativeDrag);
    return ()=>{
      clearTimeout(timer);element.removeEventListener('pointerdown',down);element.removeEventListener('pointermove',move);
      element.removeEventListener('pointerup',end);element.removeEventListener('pointercancel',end);element.removeEventListener('lostpointercapture',end);
      element.removeEventListener('click',click,true);element.removeEventListener('dragstart',nativeDrag);delete element.dataset.previewPan;element.classList.remove('is-preview-dragging');
    };
  },[ref]);
}
