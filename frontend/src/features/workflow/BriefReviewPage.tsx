import { BriefDirectSummary } from './BriefDirectSummary';
import { BriefPageTranscript } from './BriefPageTranscript';
import { briefProgressLabel } from './briefProgress';
import { PanZoomImage } from '../../shared/ui/PanZoomImage';
import { AiElapsed } from '../../shared/ui/AiElapsed';
import { useEffect, useState } from 'react';
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Drawer, Input, InputNumber, Select, Skeleton, Tabs, Tag } from 'antd';
import { ArrowLeft, ChevronLeft, ChevronRight, ExternalLink, FileText, Search } from 'lucide-react';
import { projectApi } from '../projects/api';
import { projectBriefApi, type FactItem, type FactDecision, type PageCheck } from './projectBriefApi';
import { useDemoRole } from '../../app/DemoRole';
import { ErrorState } from '../../shared/ui';
import './brief-review.css';

const groupNames: Record<string,string> = { brands:'品牌候选', products:'产品全名', competitors:'指定竞品', sellingPoints:'核心卖点', requiredTerms:'必提词句', forbiddenTerms:'禁用词句', publishRules:'发布规则', thresholds:'数值要求', freeform:'其他要求' };

export function BriefReviewPage() {
  const { projectId = '' } = useParams(), role = useDemoRole();
  const cache=useQueryClient(), {message}=App.useApp();
  const [reviewing,setReviewing]=useState(false),[aliasTarget,setAliasTarget]=useState('');
  const [edit,setEdit]=useState<{index:number;baseRunId:string;value:string;quote:string;pageNo:number;source:'text'|'visual'}>();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(''), [group, setGroup] = useState('all'), [selected, setSelected] = useState('');
  const [imageFailed, setImageFailed] = useState(false);
  const project = useQuery({ queryKey:['project',projectId], queryFn:()=>projectApi.detail(projectId), enabled:role==='brand' });
  const query = useQuery({ queryKey:['project-brief',projectId], queryFn:()=>projectBriefApi.get(projectId), enabled:role==='brand', refetchInterval:q=>['queued','vision','facts'].includes(q.state.data?.data.brief?.processing?.status || '') || ['queued','processing'].includes(q.state.data?.data.brief?.visualRun?.status || '') ? 2000 : false });
  const factsQuery = useQuery({ queryKey:['project-brief-facts',projectId], queryFn:()=>projectBriefApi.facts(projectId), enabled:role==='brand', refetchInterval:query.data?.data.brief?.processing?.status==='vision' || query.data?.data.brief?.processing?.status==='facts' ? 2000 : false });
  const brief = query.data?.data.brief;
  const processingActive = ['queued','processing'].includes(brief?.visualRun?.status || '') || ['queued','vision','facts'].includes(brief?.processing?.status || '');
  const processingMatchesVisual = !brief?.visualRun || brief.processing?.visionRunId === brief.visualRun.id || (brief.processing?.createdAt || '') >= (brief.visualRun.createdAt || '');
  const processingError = processingActive ? undefined : (brief?.visualRun?.status === 'failed' ? brief.visualRun.errorMessage : undefined) || factsQuery.data?.data.lastRun?.errorMessage || (processingMatchesVisual ? brief?.processing?.errorMessage : undefined);
  const facts = factsQuery.data?.data.sourceVersionId === brief?.sourceVersionId ? factsQuery.data?.data.facts : undefined;
  const pending = factsQuery.data?.data.sourceVersionId === brief?.sourceVersionId ? factsQuery.data?.data.lastRun?.result?.pendingFacts || [] : [];
  const pageChecks = brief?.pageChecks || [];
  const pendingCount = pending.length + pageChecks.length;
  const filteredPageChecks = pageChecks.filter(check => (group === 'all' || group === 'page_review') && (`第 ${check.pageNo} 页 ${check.reasons.join(' ')}`).toLowerCase().includes(search.toLowerCase()));
  const tab = params.get('tab') === 'pending' ? 'pending' : params.get('tab') === 'text' ? 'text' : params.get('tab') === 'rules' ? 'rules' : 'facts';
  const totalPages = brief?.pageCount || 1;
  const requestedPage = Number(params.get('page') || 1);
  const page = Number.isInteger(requestedPage) && requestedPage > 0 ? Math.min(requestedPage,totalPages) : 1;
  const rendered = brief?.visualRun?.snapshot.pages.find(p=>p.pageNo===page);
  const pageImage = rendered && brief?.visualRun ? `/api/v1/projects/${projectId}/brief/vision/${brief.visualRun.id}/pages/${page}` : brief?.pageCount ? `/api/v1/files/${brief.fileId}/preview/pages/${page}` : undefined;
  useEffect(()=>{ setImageFailed(false); },[pageImage]);
  const setPage = (next:number) => setParams(old=>{ const value=new URLSearchParams(old); value.set('page',String(Math.min(totalPages,Math.max(1,next)))); return value; },{replace:true});
  const setTab = (next:string) => { setSelected(''); setGroup('all'); setParams(old=>{const value=new URLSearchParams(old);value.set('tab',next);return value;},{replace:true}); };
  function pageFor(item:FactItem) {
    return brief?.blocks.find(block=>block.id===item.blockId)?.pageNo || Number(item.blockId.match(/:(\d+):(text|visual)$/)?.[1]) || undefined;
  }
  const groups:{key:string;label:string;items:FactItem[]}[] = facts ? [
    {key:'brand',label:'品牌名称',items:facts.brand?.value?[facts.brand]:[]},
    ...Object.entries(groupNames).filter(([key])=>key!=='brands').map(([key,label])=>({key,label,items:((facts as unknown as Record<string,unknown>)[key] || []) as FactItem[]})),
  ] : [];
  const rows = groups.flatMap(group=>group.items.map((item,index)=>({...item,key:`${group.key}-${index}`,group:group.key,label:group.label,pageNo:pageFor(item)})));
  const filteredRows = rows.filter(item=>(group==='all'||item.group===group) && (item.value+' '+item.quote).toLowerCase().includes(search.toLowerCase()));
  const factSections = groups.map(item=>({...item,rows:filteredRows.filter(row=>row.group===item.key)})).filter(item=>item.rows.length);
  const filteredPending = pending.map((item,index)=>({...item,key:`pending-${index}`,pendingIndex:index})).filter(item=>(group==='all'||item.group===group) && (item.value+' '+item.quote).toLowerCase().includes(search.toLowerCase()));
  const selectRow = (key:string,pageNo?:number) => { setSelected(key); if(pageNo && pageNo<=totalPages) setPage(pageNo); };
  const pageBlocks = brief?.blocks.filter(block=>(!brief.pageCount || block.pageNo===page) && (!brief.directSummary || block.origin!=='ai-brief-summary')) || [];
  async function editSummary(group: string, index: number, text: string) {
    if (!brief?.visionRunId || !brief.summaryUpdatedAt || reviewing || !text.trim()) return;
    setReviewing(true);
    try {
      await projectBriefApi.editSummary(projectId, { sourceVersionId: brief.sourceVersionId, runId: brief.visionRunId, expectedUpdatedAt: brief.summaryUpdatedAt, group, index, text });
      await Promise.all([query.refetch(), factsQuery.refetch()]);
      message.success('修改已保存');
    } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); await query.refetch(); }
    finally { setReviewing(false); }
  }
  async function resolvePage(check: PageCheck, action: 'confirmed' | 'irrelevant') {
    if (reviewing || !brief) return;
    setReviewing(true);
    try {
      await projectBriefApi.reviewPage(projectId, { sourceVersionId: brief.sourceVersionId, checkId: check.id, action }, crypto.randomUUID());
      await cache.invalidateQueries({ queryKey: ['project-brief', projectId] });
      setSelected(''); message.success('已处理，该页提示已移除');
    } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); await query.refetch(); }
    finally { setReviewing(false); }
  }
  async function decide(decision:FactDecision, baseRunId?:string) {
    const run=factsQuery.data?.data.lastRun;
    if(reviewing||!brief||!run)return;
    setReviewing(true);
    try {
      await projectBriefApi.reviewFacts(projectId,{sourceVersionId:brief.sourceVersionId,baseRunId:baseRunId||run.id,decisions:[decision]},crypto.randomUUID());
      await Promise.all([cache.invalidateQueries({queryKey:['project-brief-facts',projectId]}),cache.invalidateQueries({queryKey:['project-brief',projectId]})]);
      setEdit(undefined);message.success('确认已保存，后续规则生成使用新事实卡');
    } catch(error) {if(error&&typeof error==='object'&&'code' in error&&error.code==='REVISION_CONFLICT')setEdit(undefined);message.error(error instanceof Error?error.message:'保存失败');await factsQuery.refetch();}
    finally {setReviewing(false);}
  }
  const entities=facts?.brandEntities || (facts?.brand?.value?[{name:facts.brand,aliases:[],role:'primary' as const}]:[]);
  const readOnly=reviewing||project.data?.data.status==='archived';
  if(role!=='brand') return <Navigate to="/creator" replace />;
  if(project.isError || query.isError) return <ErrorState error={project.error || query.error!} retry={()=>{void project.refetch();void query.refetch();}} />;
  if(project.isPending || query.isPending) return <Skeleton active paragraph={{rows:8}} />;
  if(!brief) return <div className="quiet-empty"><FileText size={24}/><p>项目尚未上传 Brief</p><Link to={`/projects/${projectId}`}>返回项目上传资料</Link></div>;
  const fileUrl=`/api/v1/files/${brief.fileId}/content`;
  return <div className="brief-review-workspace">
    <header className="brief-review-commandbar"><div><Link to={`/projects/${projectId}`} className="brief-back"><ArrowLeft size={14}/>返回项目与达人</Link><strong title={brief.title}>{brief.title}</strong><Tag>Brief 对照 · V{brief.revision}</Tag></div><div><Link to={`/projects/${projectId}/rules?type=script`}>编辑审核规则</Link><a href={fileUrl} target="_blank" rel="noreferrer">打开原文件<ExternalLink size={12}/></a></div></header>
    <div className="brief-review-context"><span>{project.data.data.name} · {brief.pageCount ? `${brief.pageCount} 页` : '文本 Brief'} · AI 识读结果请品牌核对</span><span role={briefProgressLabel(brief.visualRun) ? 'status' : undefined}>{briefProgressLabel(brief.visualRun) || '点击事实或待核对项，左侧定位原页'}</span><AiElapsed run={brief.processing || brief.visualRun} /></div>
    <div className="brief-review-grid">
      <section className="brief-source-panel" aria-label="Brief原文对照">
        <header><strong>原文{brief.pageCount?'页面':'文字'}</strong><div className="brief-page-controls"><Button size="small" aria-label="上一页Brief" disabled={page<=1} onClick={()=>setPage(page-1)}><ChevronLeft size={14}/></Button><Select size="small" aria-label="核对Brief页码" value={page} onChange={setPage} options={Array.from({length:totalPages},(_,i)=>({value:i+1,label:`第 ${i+1} 页 / ${totalPages}`}))}/><Button size="small" aria-label="下一页Brief" disabled={page>=totalPages} onClick={()=>setPage(page+1)}><ChevronRight size={14}/></Button></div></header>
        {brief.pageCount ? <><div className="brief-source-toolbar"><span>第 {page} 页 · 原始页面</span></div><div className="brief-page-canvas" key={`${brief.sourceVersionId}-${page}`}>{pageImage&&!imageFailed ? <PanZoomImage src={pageImage} alt={`Brief第${page}页`} zoomLabel="原文图片缩放" onError={()=>setImageFailed(true)}/> : <iframe key={`${fileUrl}-${page}`} title={`Brief原文件第${page}页`} src={`${fileUrl}#page=${page}&view=FitH`} />}</div>{(!pageImage||imageFailed)&&<small className="brief-source-footnote">当前使用原始 PDF 预览，浏览器未显示时可<a href={`${fileUrl}#page=${page}`} target="_blank" rel="noreferrer">打开对应原页</a>。</small>}</> : <div className="brief-text-source">{brief.blocks.map(block=><p key={block.id}>{block.text}</p>)}</div>}
      </section>
      <section className="brief-facts-panel" aria-label="事实卡与待核对事项">
        <header><strong>{brief.directSummary ? '产品事实与规则' : '事实卡与待核对事项'}</strong>{!brief.directSummary&&<span className="mono">{rows.length} 条有效 · {pendingCount} 条待核对</span>}</header>
        <Tabs activeKey={tab} onChange={setTab} items={brief.directSummary ? [{key:'facts',label:'产品事实板'},{key:'rules',label:'四组规则'},{key:'text',label:'PDF文字层'}] : [{key:'facts',label:`事实卡 ${rows.length}`},{key:'pending',label:`待核对 ${pendingCount}`},{key:'text',label:'识读原文'}]}/>
        {tab!=='text'&&!brief.directSummary&&<div className="brief-fact-filters"><Input size="small" aria-label="搜索Brief事实" value={search} onChange={e=>setSearch(e.target.value)} prefix={<Search size={13}/>} placeholder="搜索事实或引用文字"/><Select size="small" aria-label="事实分组筛选" value={group} onChange={setGroup} options={[{value:'all',label:'全部分组'},...[...new Set(tab==='facts'?groups.map(g=>g.key):[...pending.map(p=>p.group),...(pageChecks.length?['page_review']:[])])].map(key=>({value:key,label:key==='page_review'?'页面识读提示':groups.find(g=>g.key===key)?.label||groupNames[key]||key}))]}/></div>}
        <div className="brief-facts-scroll">
          {processingError && <Alert type="warning" title="资料处理未完成" description={processingError} style={{ marginBottom: 12 }} />}

          {brief.directSummary && tab!=='text' ? <BriefDirectSummary summary={brief.directSummary} rules={tab==='rules'} search={search} onPage={setPage} disabled={readOnly} onEdit={(group,index,text)=>void editSummary(group,index,text)}/> : brief.directSummary && tab==='text' ? <div className="brief-text-source"><p>本次由全部原图直接提炼，不生成 OCR 全文。以下仅显示 PDF 自带文字层，请以左侧原图核对布局和内容。</p>{pageBlocks.map(block=><p key={block.id}>{block.text}</p>)}</div> : factsQuery.isPending ? <Skeleton active paragraph={{rows:7}}/> : factsQuery.isError ? <ErrorState error={factsQuery.error} retry={()=>factsQuery.refetch()}/> : tab==='facts' ? <>
            {!!entities.length&&<div className="brief-brand-entities">{entities.map(entity=><section key={entity.name.value}><header><Tag>{entity.role==='primary'?'主推品牌':'关联品牌'}</Tag><button onClick={()=>selectRow('entity-'+entity.name.value,pageFor(entity.name))}>{entity.name.value}</button></header>{entity.aliases.length>0&&<div><span>别名 / 英文名</span>{entity.aliases.map(alias=><button key={alias.value} onClick={()=>selectRow('alias-'+alias.value,pageFor(alias))}>{alias.value}</button>)}</div>}</section>)}</div>}
            {!!facts?.warnings.length&&<details className="brief-fact-warnings"><summary>资料提示 {facts.warnings.length} 项</summary>{facts.warnings.map((warning,index)=><p key={index}>{warning}</p>)}</details>}
            {factSections.map(section=><section key={section.key} className="brief-fact-group" aria-label={section.label}><header><strong>{section.label}</strong><span>{section.rows.length} 项</span></header><p>{section.rows.map((row,index)=><span key={row.key}>{index>0&&<span aria-hidden="true">、</span>}<button type="button" aria-pressed={selected===row.key} title={row.pageNo?`第 ${row.pageNo} 页`:'文字出处'} className={`brief-fact-term${selected===row.key?' is-selected':''}`} onClick={()=>selectRow(row.key,row.pageNo)}>{row.value}</button></span>)}<span aria-hidden="true">。</span></p>{section.rows.some(row=>row.key===selected)&&<blockquote>{section.rows.filter(row=>row.key===selected).map(row=><span key={row.key} className="brief-fact-source">原文出处 · {row.pageNo?`第 ${row.pageNo} 页`:'文字片段'}</span>)}{section.rows.find(row=>row.key===selected)?.quote}</blockquote>}</section>)}
            {!factSections.length&&<div className="quiet-empty"><p>{rows.length?'没有匹配的事实':'尚无有效事实卡'}</p><small>{rows.length?'调整搜索或分组查看其他事实。':'自动处理完成后会显示；可返回项目查看处理状态。'}</small></div>}
          </> : tab==='pending' ? <><div className="brief-pending-explainer">对照左侧原页处理，完成后会从待核对中移除。</div>{filteredPageChecks.map(check=><article key={check.id} className={`brief-review-fact is-pending${selected===check.id?' is-selected':''}`}><button onClick={()=>selectRow(check.id,check.pageNo)}><span className="brief-fact-meta"><span>页面识读提示</span><span>第 {check.pageNo} 页</span></span><strong>第 {check.pageNo} 页有局部疑点</strong>{check.reasons.map((reason,index)=><p key={index}>{reason}</p>)}<small>对照原页 <ChevronRight size={12}/></small></button><div className="brief-fact-actions"><Button size="small" disabled={readOnly} onClick={()=>void resolvePage(check,'confirmed')}>已核对</Button><Button size="small" disabled={readOnly} onClick={()=>void resolvePage(check,'irrelevant')}>不影响规则</Button></div></article>)}{filteredPending.map(item=><article key={item.key} className={`brief-review-fact is-pending${selected===item.key?' is-selected':''}`}><button onClick={()=>selectRow(item.key,item.pageNo)}><span className="brief-fact-meta"><span>{groupNames[item.group]||item.group}</span><span>第 {item.pageNo} 页</span></span><strong>{item.value}</strong><p>{item.reason}</p><small>对照原页 <ChevronRight size={12}/></small></button>{selected===item.key&&<blockquote>{item.quote}</blockquote>}<div className="brief-fact-actions">{item.group==='brands'?<><Button size="small" disabled={readOnly} onClick={()=>void decide({itemIndex:item.pendingIndex,action:'brand_primary'})}>设为主品牌</Button><Button size="small" disabled={readOnly} onClick={()=>void decide({itemIndex:item.pendingIndex,action:'brand_related'})}>标为关联品牌</Button><Select size="small" aria-label={`别名归属品牌-${item.pendingIndex}`} placeholder="选择别名归属" value={aliasTarget||facts?.brand?.value||undefined} onChange={setAliasTarget} disabled={readOnly||!entities.length} options={entities.map(entity=>({value:entity.name.value,label:entity.name.value}))}/><Button size="small" disabled={readOnly||!entities.length||!(aliasTarget||facts?.brand?.value)} onClick={()=>void decide({itemIndex:item.pendingIndex,action:'brand_alias',targetBrand:aliasTarget||facts?.brand?.value})}>归为别名</Button></>:<><Button size="small" disabled={readOnly} onClick={()=>void decide({itemIndex:item.pendingIndex,action:'accept'})}>确认采用</Button><Button size="small" disabled={readOnly} onClick={()=>{selectRow(item.key,item.pageNo);setEdit({index:item.pendingIndex,baseRunId:factsQuery.data?.data.lastRun?.id||'',value:item.value,quote:item.quote,pageNo:item.pageNo,source:item.source==='visual'?'visual':'text'});}}>编辑后采用</Button></>}<Button size="small" disabled={readOnly} onClick={()=>void decide({itemIndex:item.pendingIndex,action:'ignore'})}>忽略</Button></div></article>)}{!filteredPending.length&&!filteredPageChecks.length&&<div className="quiet-empty"><p>{pendingCount?'没有匹配的待核对条目':'暂无待核对条目'}</p></div>}</> : <BriefPageTranscript pageNo={page} blocks={pageBlocks} recognized={brief.recognizedPages?.find(p=>p.pageNo===page) || (brief.visualRun?.status==='succeeded' ? brief.visualRun.result?.report?.pages.find(p=>p.pageNo===page) : undefined)} />}
        </div>
        <footer>事实卡用于生成候选规则，最终以品牌保存的规则为准。</footer>
      </section>
    </div>
    <Drawer title="核对并修正事实" open={!!edit} onClose={()=>setEdit(undefined)} size={440} mask={false} footer={<Button type="primary" disabled={reviewing||!edit?.value.trim()||!edit?.quote.trim()} loading={reviewing} onClick={()=>edit&&void decide({itemIndex:edit.index,action:'accept',value:edit.value,quote:edit.quote,pageNo:edit.pageNo,source:edit.source},edit.baseRunId)}>确认采用并保存</Button>}><div className="brief-fact-edit">{edit&&<><label>事实内容<Input.TextArea aria-label="修正事实内容" rows={3} value={edit.value} onChange={e=>setEdit({...edit,value:e.target.value})}/></label><label>出处页码<InputNumber aria-label="修正出处页码" min={1} max={totalPages} value={edit.pageNo} onChange={value=>{if(value){setEdit({...edit,pageNo:value});setPage(value);}}}/></label><label>出处类型<Select aria-label="修正出处类型" value={edit.source} onChange={value=>setEdit({...edit,source:value})} options={[{value:'text',label:'识读文字'},{value:'visual',label:'画面描述'}]}/></label><label>引用原文<Input.TextArea aria-label="修正事实引文" rows={6} value={edit.quote} onChange={e=>setEdit({...edit,quote:e.target.value})}/></label><small>对照左侧原页修正，引用须能在对应页的识读文字或画面描述中连续核对。不会调用模型。</small></>}</div></Drawer>
  </div>;
}
