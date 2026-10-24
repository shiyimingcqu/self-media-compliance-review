import type { ScriptAnnotation } from './scriptAnnotations';

export type WordSourceBlock={location?:string|null;text:string};
export type WordReviewProps={sourceMap?:WordSourceBlock[];annotations?:ScriptAnnotation[];active?:{id:string;sequence:number};onSelect?:(id:string)=>void;onLocateUnavailable?:()=>void};
export const isWordLocation=(location:string)=>/^(paragraph:\d+|table:\d+:r\d+c\d+)$/.test(location);
export const wordBookmarkId=(fileId:string,location:string)=>`crv_${fileId.slice(0,8)}_${location.replaceAll(':','_')}`;

type Interval={start:number;end:number;annotation:ScriptAnnotation};
type HostGroup={host:HTMLElement;chars:{node:Text;offset:number}[];text:string;intervals:Map<Text,Interval[]>;notes:Map<string,ScriptAnnotation>};

function textIn(host:HTMLElement){
  const paragraphs=host.tagName==='P'?[host]:Array.from(host.children).filter((node):node is HTMLElement=>node instanceof HTMLElement&&node.tagName==='P');
  const chars:{node:Text;offset:number}[]=[];
  for(const paragraph of paragraphs){
    const walker=document.createTreeWalker(paragraph,NodeFilter.SHOW_TEXT);
    for(let item=walker.nextNode();item;item=walker.nextNode()){
      if(item.parentElement?.closest('p')!==paragraph)continue;
      const node=item as Text;
      for(let offset=0;offset<node.data.length;offset++)if(!/\s/.test(node.data[offset]))chars.push({node,offset});
    }
  }
  return {chars,text:chars.map(char=>char.node.data[char.offset]).join('')};
}

export function clearWordAnnotations(body:HTMLElement){
  body.querySelectorAll('[data-word-comment-pin]').forEach(node=>node.remove());
  const parents=new Set<Node>();
  body.querySelectorAll<HTMLElement>('[data-word-highlight]').forEach(mark=>{if(mark.parentNode)parents.add(mark.parentNode);mark.replaceWith(...mark.childNodes);});
  parents.forEach(parent=>parent.normalize());
  body.querySelectorAll('[data-word-reviewed-host]').forEach(node=>{node.classList.remove('word-reviewed-host','word-active-source','word-image-source');node.removeAttribute('data-word-reviewed-host');});
}

/** Exact structural bookmarks + a verified text map, never a whole-document first-match search. */
export function annotateWord(body:HTMLElement,fileId:string,sourceMap:WordSourceBlock[],annotations:ScriptAnnotation[],activeId:string|undefined,onSelect:(id:string)=>void){
  clearWordAnnotations(body);
  const groups=new Map<HTMLElement,HostGroup>();const linked=new Set<string>();
  for(const annotation of annotations){
    for(const anchor of annotation.anchors){
      if(!isWordLocation(anchor.location))continue;
      const bookmark=body.querySelector<HTMLElement>(`#${wordBookmarkId(fileId,anchor.location)}`);
      const host=bookmark?.closest<HTMLElement>(anchor.location.startsWith('table:')?'td':'p');
      if(!host)continue;
      let group=groups.get(host);
      if(!group){group={host,...textIn(host),intervals:new Map(),notes:new Map()};groups.set(host,group);}
      if(anchor.kind==='image'){
        if(!host.querySelector('img'))continue;
        host.classList.add('word-image-source');group.notes.set(annotation.id,annotation);linked.add(annotation.id);continue;
      }
      const block=sourceMap.find(block=>block.location===anchor.location);
      if(!block||block.text.replace(/\s/g,'')!==group.text)continue;
      const start=block.text.slice(0,anchor.start).replace(/\s/g,'').length,end=block.text.slice(0,anchor.end).replace(/\s/g,'').length;
      if(start>=end||end>group.chars.length)continue;
      let cursor=start;
      while(cursor<end){
        const first=group.chars[cursor];let last=first;
        while(cursor+1<end&&group.chars[cursor+1].node===first.node)last=group.chars[++cursor];
        const ranges=group.intervals.get(first.node)||[];
        ranges.push({start:first.offset,end:last.offset+1,annotation});group.intervals.set(first.node,ranges);cursor++;
      }
      group.notes.set(annotation.id,annotation);linked.add(annotation.id);
    }
  }
  for(const group of groups.values()){
    if(!group.notes.size)continue;
    group.host.classList.add('word-reviewed-host');group.host.dataset.wordReviewedHost='true';
    if(activeId&&group.notes.has(activeId))group.host.classList.add('word-active-source');
    for(const [node,intervals] of group.intervals){
      const boundaries=[...new Set([0,node.data.length,...intervals.flatMap(range=>[range.start,range.end])])].sort((a,b)=>a-b);
      const fragment=document.createDocumentFragment();
      for(let index=0;index<boundaries.length-1;index++){
        const start=boundaries[index],end=boundaries[index+1],hits=intervals.filter(range=>range.start<end&&range.end>start);
        if(!hits.length){fragment.append(node.data.slice(start,end));continue;}
        const selected=hits.find(range=>range.annotation.id===activeId)||hits[0];const note=selected.annotation;
        const mark=document.createElement('mark');mark.dataset.wordHighlight='true';mark.dataset.sourceComment=note.id;
        mark.className=`word-review-highlight ${note.finding.verdict}${note.id===activeId?' active':''}`;
        mark.textContent=node.data.slice(start,end);mark.setAttribute('role','button');mark.tabIndex=0;
        mark.setAttribute('aria-label',`原文引用 · 评论 ${note.index}`);
        mark.addEventListener('click',()=>onSelect(note.id));
        mark.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect(note.id);}});
        fragment.append(mark);
      }
      node.replaceWith(fragment);
    }
    [...group.notes.values()].forEach((note,index)=>{
      const pin=document.createElement('button');pin.type='button';pin.dataset.wordCommentPin='true';pin.dataset.sourceComment=note.id;
      pin.className=`word-comment-pin${note.id===activeId?' active':''}`;pin.textContent=String(note.index);pin.style.right=`${2+index*20}px`;
      pin.setAttribute('aria-label',`查看原布局评论 ${note.index}`);pin.addEventListener('click',()=>onSelect(note.id));group.host.append(pin);
    });
  }
  return linked;
}

export const WORD_ANNOTATION_STYLE=`
.word-reviewed-host{position:relative}
.word-review-highlight{color:inherit;background:#fff0cc;cursor:pointer;padding:0;line-height:inherit}
.word-review-highlight.non_compliant{background:#ffe4e8}.word-review-highlight.compliant{background:#e4f5ec}
.word-review-highlight.active{outline:1px solid #bc9654;background:#f8df9e}
.word-image-source{outline:1px solid #d1ad68}.word-active-source{outline:2px solid #bc9654;outline-offset:-1px}
.word-comment-pin{position:absolute;top:1px;width:18px;height:18px;border:1px solid #bc9654;border-radius:50%;background:#fff5dd;color:#634313;font:11px/16px sans-serif;padding:0;cursor:pointer;z-index:2;opacity:0}
.word-reviewed-host:hover>.word-comment-pin,.word-comment-pin:focus-visible,.word-comment-pin.active{opacity:1}
.word-comment-pin.active{background:#e9c878}
`;
