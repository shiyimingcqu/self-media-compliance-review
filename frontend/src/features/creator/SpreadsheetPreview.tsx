import { useEffect, useRef, useState } from 'react';
import { Button, Image, Select, Space, Tag } from 'antd';
import type { ScriptLayout } from './scriptLayoutApi';
import { scriptLayoutApi } from './scriptLayoutApi';
import type { ScriptAnnotation } from './scriptAnnotations';
import { AnnotationText } from './AnnotationText';
import type { Workflow } from '../workflow/api';

const letter = (n: number): string => n > 26 ? letter(Math.floor((n - 1) / 26)) + String.fromCharCode(65 + (n - 1) % 26) : String.fromCharCode(64 + n);

export function SpreadsheetPreview({ taskId, script, layout, annotations, active, onSelect }: { taskId: string; script: NonNullable<Workflow['currentScript']>; layout: ScriptLayout; annotations: ScriptAnnotation[]; active?: { id: string; sequence: number }; onSelect: (id: string) => void }) {
  const [sheetName, setSheetName] = useState(layout.sheets[0]?.name), [zoom, setZoom] = useState<number | 'fit'>('fit'), [available, setAvailable] = useState(600);
  const viewport = useRef<HTMLDivElement>(null);
  const sheet = layout.sheets.find(s => s.name === sheetName) || layout.sheets[0];
  useEffect(() => { if (!viewport.current) return; const observer = new ResizeObserver(entries => setAvailable(entries[0].contentRect.width)); observer.observe(viewport.current); return () => observer.disconnect(); }, []);
  useEffect(() => {
    const annotation = annotations.find(a => a.id === active?.id), location = annotation?.anchors[0]?.location;
    const match = layout.sheets.find(s => location?.startsWith(s.name + '!'));
    if (match) setSheetName(match.name);
  }, [active, annotations, layout]);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const marker = [...(viewport.current?.querySelectorAll<HTMLElement>('[data-source-comment]') || [])].find(el => el.dataset.sourceComment === active?.id);
      if (marker && viewport.current) {
        const box = marker.getBoundingClientRect(), clip = viewport.current.getBoundingClientRect();
        viewport.current.scrollTo({ top: viewport.current.scrollTop + box.top + box.height / 2 - clip.top - viewport.current.clientHeight / 2, left: viewport.current.scrollLeft + box.left + box.width / 2 - clip.left - viewport.current.clientWidth / 2, behavior: 'smooth' });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [active, sheetName, zoom, available]);
  if (!sheet) return <p>当前文件没有可预览的工作表。</p>;
  const width = sheet.columns.reduce((a, b) => a + b, 0), height = sheet.rows.reduce((a, b) => a + b, 0);
  const scale = zoom === 'fit' ? Math.min(1, Math.max(.2, (available - 36) / width)) : zoom / 100;
  const lookup = new Map(sheet.cells.map(c => [c.address, c]));
  const positions = (lengths: number[]) => lengths.map((_, i) => lengths.slice(0, i).reduce((a, b) => a + b, 0));
  const xs = positions(sheet.columns), ys = positions(sheet.rows);
  const activeLocations = annotations.find(a => a.id === active?.id)?.anchors.map(a => a.location) || [];
  const origin = new Map(script.sourceMap.map(b => [b.location, b.text]));
  const pinned = (location: string) => annotations.filter(a => a.anchors.some(p => p.location === location && p.kind === 'text'));
  const rendered: React.ReactNode[] = [];
  for (let row = 1; row <= sheet.rowCount; row++) {
    for (let col = 1; col <= sheet.colCount; col++) {
      const merged = sheet.merges.find(m => m.r1 <= row && row <= m.r2 && m.c1 <= col && col <= m.c2);
      if (merged && (merged.r1 !== row || merged.c1 !== col)) continue;
      const address = letter(col) + row, location = sheet.name + '!' + address, cell = lookup.get(address), notes = pinned(location);
      const cellWidth = sheet.columns.slice(col - 1, merged?.c2 || col).reduce((a, b) => a + b, 0), cellHeight = sheet.rows.slice(row - 1, merged?.r2 || row).reduce((a, b) => a + b, 0);
      const hasImage = sheet.images.some(im => im.cell === address);
      const text = cell?.formula?.toUpperCase().includes('DISPIMG(') && hasImage ? '' : cell?.text || '';
      rendered.push(<div key={address} role="cell" aria-label={location} data-cell-location={location} className={`sheet-cell ${notes.length ? 'has-comments' : ''} ${activeLocations.includes(location) ? 'active-comment-cell' : ''} ${sheet.hiddenRows.includes(row) || sheet.hiddenColumns.includes(col) ? 'originally-hidden' : ''}`} style={{ ...cell?.style, position: 'absolute', left: xs[col - 1], top: ys[row - 1], width: cellWidth, height: cellHeight }} title={cell?.formula ? `原公式（不执行）：${cell.formula}` : location}>
        <div className="sheet-cell-content" style={{ alignItems: cell?.style.verticalAlign === 'middle' ? 'center' : cell?.style.verticalAlign === 'top' ? 'flex-start' : 'flex-end' }}><div><AnnotationText text={text} sourceText={origin.get(location)} location={location} annotations={annotations} activeId={active?.id} onSelect={onSelect} />{cell?.uncached && !hasImage && <span className="uncached-formula">公式无缓存值</span>}</div></div>
        {!!notes.length && <span className="sheet-comment-pins">{notes.map(note => <button key={note.id} type="button" className={`source-comment-pin ${note.id === active?.id ? 'active' : ''}`} data-source-comment={note.id} aria-label={`查看评论 ${note.index}，${location}`} onClick={() => onSelect(note.id)}>{note.index}</button>)}</span>}
      </div>);
    }
  }
  return <div className="spreadsheet-preview">
    <div className="sheet-preview-toolbar"><Select aria-label="选择脚本工作表" value={sheet.name} onChange={setSheetName} options={layout.sheets.map(s => ({ value: s.name, label: s.name + (s.hidden ? '（原表隐藏）' : '') }))} /><Space size={6}><Tag>{sheet.rowCount} 行 × {sheet.colCount} 列</Tag><Select aria-label="表格预览缩放" value={zoom} onChange={setZoom} options={[{ value: 'fit', label: `适应宽度 ${Math.round(scale * 100)}%` }, ...[50, 75, 100, 125, 150].map(n => ({ value: n, label: `${n}%` }))]} /></Space></div>
    <div ref={viewport} className="sheet-preview-scroll" aria-label="Excel脚本原布局预览">
      <div style={{ position: 'relative', width: width * scale + 32, height: height * scale + 24 }}>
        <div className="sheet-column-labels" style={{ width: width * scale, left: 32 }}>{sheet.columns.map((w, i) => <span key={i} style={{ width: w * scale }}>{letter(i + 1)}</span>)}</div>
        <div className="sheet-row-labels" style={{ top: 24 }}>{sheet.rows.map((h, i) => <span key={i} style={{ height: h * scale }}>{i + 1}</span>)}</div>
        <div role="table" aria-label={`${sheet.name}原始表格布局`} className="sheet-canvas" style={{ width, height, transform: `scale(${scale})`, transformOrigin: 'top left', position: 'absolute', left: 32, top: 24 }}>
          {rendered}
          <Image.PreviewGroup>{sheet.images.map((im, i) => {
            const notes = annotations.filter(a => a.anchors.some(p => p.imageId === im.id && p.location === sheet.name + '!' + im.cell));
            return <div className={`sheet-drawing ${notes.some(a => a.id === active?.id) ? 'active-comment-image' : ''}`} key={`${im.id}-${i}`} style={{ left: im.x, top: im.y, width: im.width, height: im.height }}><Image src={scriptLayoutApi.imageUrl(taskId, script.scriptVersionId, im.id)} alt={`表格内嵌图片 ${sheet.name}!${im.cell}`} width="100%" height="100%" style={{ objectFit: 'contain' }} /><span className="sheet-comment-pins">{notes.map(note => <button type="button" className={`source-comment-pin ${note.id === active?.id ? 'active' : ''}`} key={note.id} data-source-comment={note.id} onClick={() => onSelect(note.id)} aria-label={`查看图片评论 ${note.index}，${sheet.name}!${im.cell}`}>{note.index}</button>)}</span></div>;
          })}</Image.PreviewGroup>
        </div>
      </div>
    </div>
    <div className="sheet-bottom-status"><span>{sheet.name}</span><span>只读预览 · 点击彩色标记查看评论</span><Button size="small" type="text" onClick={() => setZoom(100)}>原始比例</Button></div>
  </div>;
}
