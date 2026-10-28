import { Alert } from 'antd';
import type { ProjectBrief, VisualPage } from './projectBriefApi';

const roles = { requirement: '要求与禁令', reference: '参考示例', context: '背景信息' };

export function BriefPageTranscript({ pageNo, recognized, blocks, latestError }: {
  pageNo: number;
  recognized?: VisualPage;
  blocks: ProjectBrief['blocks'];
  latestError?: string;
}) {
  const regions = recognized?.regions || [];
  // Preserve VLM cell membership, including shared/merged rows. Never infer
  // material scope from the order of flattened legacy text.
  const rows = [...new Set(regions.map(region => region.rowHeader || '未标明分区'))];
  return <>
    <div className="brief-pending-explainer">第 {pageNo} 页 · AI 识读结果，需对照原页核对</div>
    {latestError && <Alert type="warning" title="最近一次识别未完成，当前保留上次成功结果" description={latestError} />}
    {!regions.length ? recognized?.structureComplete ? <div className="brief-pending-explainer">本页按顺序识读，无需分区裁图。</div> : <Alert type="info" title="当前结果没有区域明细" description="下方为文字转录与画面概述，尚未区分表格行列。重新图文识别成功后，才能查看各区域的逐条内容。" /> : <>
      {!recognized?.structureComplete && <Alert type="warning" title="本页分区尚未完整，请对照原图核对缺项" />}
      {rows.map(row => <section className="brief-transcript-region-row" key={row} aria-label={`${row}分区`}>
        <h3>{row}</h3>
        <div className="brief-transcript-regions">{regions.filter(region => (region.rowHeader || '未标明分区') === row).map(region => <article className="brief-transcript-region" key={region.id} aria-label={`${row} · ${region.columnHeader || roles[region.role]}`}>
          <header><strong>{region.columnHeader || '未标明列标题'}</strong><span>{roles[region.role]}</span></header>
          {region.text ? <p>{region.text}</p> : region.items.length ? <ul>{region.items.map((item, index) => <li key={index}>{item.quote}</li>)}</ul> : <p>该区域没有返回条目，请核对原图。</p>}
          {region.applicability?.region && <small>{region.applicability.region}</small>}
        </article>)}</div>
      </section>)}
    </>}
    <details className="brief-transcript-original" open={!regions.length}>
      <summary>文字转录与画面概述</summary>
      {recognized ? <>
        <article className="brief-transcript-block"><span>文字转录</span><p>{recognized.text}</p></article>
        <article className="brief-transcript-block"><span>画面概述 · 不能代替逐条要求</span><p>{recognized.visualDescription}</p></article>
      </> : blocks.map(block => <article className="brief-transcript-block" key={block.id}><span>{!block.origin ? 'PDF 原生文字' : block.id.endsWith(':visual') ? '画面概述 · 不能代替逐条要求' : '文字转录'}</span><p>{block.text}</p></article>)}
      {!recognized && !blocks.length && <div className="quiet-empty">该页暂无可用识读文字，请直接核对左侧原图。</div>}
    </details>
  </>;
}
