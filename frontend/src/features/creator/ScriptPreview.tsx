import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Image, Input, Popover, Space, Tabs, Tag } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { ScriptReview, Workflow } from '../workflow/api';
import { fileContentUrl } from '../../shared/api/client';
import { useQuery } from '@tanstack/react-query';
import { scriptLayoutApi } from './scriptLayoutApi';
import { SpreadsheetPreview } from './SpreadsheetPreview';
import type { ScriptAnnotation } from './scriptAnnotations';
import { AnnotationText } from './AnnotationText';

export function ScriptPreview({ taskId, script, review, locate, annotations = [], active, onSelect = () => {}, reviewing, outdatedReview }: { taskId?: string; script: Workflow['currentScript']; review: ScriptReview | null; locate?: { quote: string; sequence: number }; annotations?: ScriptAnnotation[]; active?: { id: string; sequence: number }; onSelect?: (id: string) => void; reviewing?: boolean; outdatedReview?: boolean }) {
  const [search, setSearch] = useState(''), [tab, setTab] = useState('text');
  const root = useRef<HTMLDivElement>(null);
  const layoutQuery = useQuery({ queryKey: ['script-layout', taskId, script?.scriptVersionId], queryFn: () => scriptLayoutApi.get(taskId!, script!.scriptVersionId), enabled: !!taskId && !!script?.fileId, retry: false, staleTime: Infinity });
  const layout = layoutQuery.data?.data;
  useEffect(() => { setSearch(''); setTab('text'); }, [script?.scriptVersionId]);
  useEffect(() => { if (locate) { setSearch(locate.quote); setTab('text'); } }, [locate]);
  useEffect(() => { if (search.trim()) root.current?.querySelector('mark')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [search, tab]);
  useEffect(() => { if (layout?.supported) setTab('sheet'); }, [layout]);
  useEffect(() => {
    if (!active) return;
    if (layout?.supported) setTab('sheet');
    else {
      setTab('text');
      requestAnimationFrame(() => [...(root.current?.querySelectorAll<HTMLElement>('[data-source-comment]') || [])].find(el => el.dataset.sourceComment === active.id)?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
    }
  }, [active, layout]);
  if (!script) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="提交脚本后，这里显示实际解析内容，方便与 AI 反馈对照" />;
  const matching = review?.scriptVersionId === script.scriptVersionId;
  const media = matching ? review?.snapshot?.multimodal : undefined;
  const previewImages = media?.images || layout?.images || [];
  const blocks = script.sourceMap.length ? script.sourceMap : script.text ? [{ text: script.text, location: '脚本正文' }] : [];
  const term = search.trim();
  const matches = term ? blocks.filter(b => b.text.includes(term)).length : 0;
  const highlight = (text: string) => !term ? text : text.split(term).flatMap((part, i) => i ? [<mark key={i}>{term}</mark>, part] : [part]);
  const warnings = script.parseMeta.warnings || [];
  const imageOnlyWarnings = warnings.filter(w => /图片.*未纳入文本审核/.test(w));
  const otherWarnings = warnings.filter(w => !imageOnlyWarnings.includes(w));
  return <div ref={root} className="script-preview">
    {script.extractionStatus !== 'ready' && <Alert type="error" showIcon title="未能完整解析脚本，请检查原件或更换格式后重新提交。" />}
    {!!media?.uncovered.length && <Alert showIcon type="warning" title="存在未完整读取的图片" description={media.uncovered.join('；')} />}
    {!media && (!!script.parseMeta.uncoveredMedia?.length || !!previewImages.length) && <Alert className="script-image-review-notice" type={reviewing ? 'info' : 'warning'} title={reviewing ? '正在审核脚本文字和内嵌图片' : outdatedReview ? '旧审核已失效，图片需按当前规则重新验收' : '当前规则下尚无有效的图片审核记录'} description={reviewing ? '本次调用完成后自动更新，请勿重复提交。' : `${previewImages.length ? `已从原脚本提取 ${previewImages.length} 张图片。` : ''}${outdatedReview ? '品牌规则或Brief更新后，需要重新验收。' : ''}请点击顶部「按当前规则重新验收」，现有脚本文字和内嵌图片会一起送审，可能计费，无需重新上传。`} />}
    <Tabs activeKey={tab} onChange={setTab} tabBarExtraContent={<Space size={8}><Popover trigger="click" title="解析说明" content={<div className="review-information-popover"><p><Tag>{blocks.length} 个文本块</Tag>{media ? `${media.images.length} 张送审图片` : '图片覆盖待核对'}</p><p>点击原文标记可关联右侧评论。文字预览反映系统实际读取内容，原表布局与桌面Excel效果可能不同。</p>{[...otherWarnings, ...imageOnlyWarnings, ...(layout?.warnings || [])].map((warning, i) => <p key={i}>{warning}</p>)}</div>}><Button size="small" type="text">解析说明{warnings.length ? ` · ${warnings.length}` : ''}</Button></Popover>{script.fileId && <a href={fileContentUrl(script.fileId)} target="_blank" rel="noreferrer">查看原文件 ↗</a>}</Space>} items={[
      ...(layout?.supported && taskId ? [{ key: 'sheet', label: 'Excel 原布局', children: <SpreadsheetPreview taskId={taskId} script={script} layout={layout} annotations={annotations} active={active} onSelect={onSelect} /> }] : []),
      { key: 'text', label: `解析文字 · ${blocks.length}`, children: <>
        <Input aria-label="搜索脚本解析内容" prefix={<SearchOutlined />} allowClear value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索原文，或点击右侧反馈定位" />
        {term && <p className="script-search-result">{matches ? `在 ${matches} 个文本块中找到原文` : '当前解析文本未找到完整引文，请对照原文件核对。'}</p>}
        <div className="script-source-blocks">{blocks.length ? blocks.map((block, i) => <article className={term && block.text.includes(term) ? 'script-source-block matched' : 'script-source-block'} key={block.paragraphId || i}><span className="script-source-location">{block.location || `段落 ${i + 1}`}{annotations.filter(a => a.anchors.some(p => p.location === (block.location || `段落 ${i + 1}`))).map(a => <button type="button" className="source-comment-pin" key={a.id} data-source-comment={a.id} onClick={() => onSelect(a.id)} aria-label={`查看评论 ${a.index}`}>{a.index}</button>)}</span><div>{term ? highlight(block.text) : <AnnotationText text={block.text} location={block.location || `段落 ${i + 1}`} annotations={annotations} activeId={active?.id} onSelect={onSelect} />}</div></article>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未读取到文字，请核对图片或原文件" />}</div>
      </> },
      { key: 'images', label: `图片${media || layout?.supported ? ` · ${previewImages.length}` : ''}`, children: previewImages.length ? <><p className="script-preview-note">{media ? '本次审核采用的图片；判定说明可在右侧「审核详情」查看。' : '以下为原文件已提取的图片预览，尚无当前规则下的有效审核结果；点击顶部「按当前规则重新验收」可送审。'}</p><Image.PreviewGroup><div className="script-preview-images">{previewImages.map(im => <figure key={im.id}><Image src={media ? `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/workflow/script-reviews/${encodeURIComponent(review!.id)}/images/${encodeURIComponent(im.id)}` : scriptLayoutApi.imageUrl(taskId!, script.scriptVersionId, im.id)} alt={`脚本原图 ${im.locations.join('、')}`} /><figcaption><strong>{im.locations.join('、') || '未标注位置'}</strong><span>{im.width} × {im.height} · 点击放大</span></figcaption></figure>)}</div></Image.PreviewGroup></> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={media ? '本次未提取到可送审图片；若原件含图，请检查解析提示' : '当前尚无可展示的图片，请查看原布局或解析说明；送审请使用顶部重新验收按钮'} /> },
    ]} />
    {layoutQuery.isError && <Alert type="warning" title="原表布局读取失败，仍可使用解析文字和原文件核对。" action={<button type="button" onClick={() => void layoutQuery.refetch()}>重试预览</button>} />}
  </div>;
}
