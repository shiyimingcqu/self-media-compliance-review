"""XLSX 真实解析（t-38）：openpyxl 读单元格；WPS DISPIMG 只做锚点识别不执行公式。

- **每个非空 cell 一个块**，location=`sheet!D12`（精确到真实单元格，口播 D12 就指 D12；
  绝不拿行首 A 列冒充整行）。合并区仅以锚点单元格取值，被并入的空格不产出块。
- **公式识别走公式视图**（data_only=False 读同一文件）：data_only=True 取的是缓存值，
  缺缓存时该单元格读出来是 None，光看前导 '=' 会静默丢内容。凡有公式而无可信缓存值
  → 记 uncovered_other 并置 partial，**不执行、不猜值**。
- 行/列超出安全上限 → 明确 partial 警示（不静默截断后报 full）。
- 图片：media 与 cellimages 引用**按目标文件去重**，同一张图不重复计数。
"""

from app.modules.brief_script.parsing.doc import (DocBlock, DocFormatError, ParsedDoc, finalize_media,
                                                 mark_partial, open_zip_guarded, safe_xml)

MAX_ROWS = 5_000
MAX_COLS = 64


def _cellimages_map(zf) -> dict[str, str]:
    """WPS cellimages.xml：cNvPr@name(锚点 ID) → r:embed 关系 ID → rels 目标媒体。"""
    ci = safe_xml(zf, "xl/cellimages.xml")
    rels = safe_xml(zf, "xl/_rels/cellimages.xml.rels")
    if ci is None or rels is None:
        return {}
    rid_target = {}
    for rel in rels:
        rid, target = rel.get("Id"), rel.get("Target")
        if rid and target:
            rid_target[rid] = target.lstrip("/")[3:] if target.startswith("/xl/") else target
    out: dict[str, str] = {}
    R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    for pic in ci.iter():
        if not pic.tag.endswith("pic"):
            continue
        name, embed = None, None
        for el in pic.iter():
            if el.tag.endswith("cNvPr") and el.get("name"):
                name = el.get("name")
            if el.tag.endswith("blip") and el.get(f"{R_NS}embed"):
                embed = el.get(f"{R_NS}embed")
        if name and embed:
            target = rid_target.get(embed, "")
            out[name] = f"xl/{target}" if target and not target.startswith("xl/") else target
    return out


def parse_xlsx(payload: bytes) -> ParsedDoc:
    zf = open_zip_guarded(payload)
    media = [n for n in zf.namelist() if n.startswith("xl/media/") and not n.endswith("/")]
    cellmap = _cellimages_map(zf)
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise DocFormatError("MODULE_NOT_IMPLEMENTED", "XLSX 解析依赖未安装（openpyxl/defusedxml）") from None

    import io

    bio = io.BytesIO(payload)
    try:
        # 公式视图：只用来"发现公式"，不取值；值视图拿缓存值
        wb_f = load_workbook(io.BytesIO(bio.getvalue()), read_only=False, data_only=False)
        wb_v = load_workbook(io.BytesIO(bio.getvalue()), read_only=False, data_only=True)
    except Exception:
        raise DocFormatError("EXTRACTION_FAILED", "工作簿结构无法解析") from None

    doc = ParsedDoc()
    formula_without_cache: list[str] = []
    truncated: list[str] = []
    for ws_f, ws_v in zip(wb_f.worksheets, wb_v.worksheets):
        merged = [str(r) for r in ws_f.merged_cells.ranges]
        if merged:
            doc.warnings.append(f"工作表 {ws_f.title} 含 {len(merged)} 处合并区，按锚点单元格取值")
        max_row = ws_f.max_row or 0
        max_col = ws_f.max_column or 0
        if max_row > MAX_ROWS or max_col > MAX_COLS:
            truncated.append(f"{ws_f.title}({max_row}x{max_col})")
        for row in ws_f.iter_rows(min_row=1, max_row=min(max_row, MAX_ROWS),
                                  max_col=min(max_col, MAX_COLS)):
            for c in row:
                coord = f"{ws_f.title}!{c.coordinate}"
                # 公式判定：openpyxl 在 data_only=False 下把公式存为带 '=' 前缀的字符串
                raw_f = c.value
                is_formula = isinstance(raw_f, str) and raw_f.startswith("=")
                cached = ws_v[c.coordinate].value if c.row <= (ws_v.max_row or 0) and c.column <= (ws_v.max_column or 0) else None
                if is_formula:
                    if cached is None or (isinstance(cached, str) and not cached.strip()):
                        # WPS image formulas are handled as media, not executable formulas.
                        if 'DISPIMG(' in raw_f.upper() and any(name in raw_f and target in media for name, target in cellmap.items()):
                            continue
                        # 无缓存：不执行、不猜，如实标未覆盖
                        formula_without_cache.append(coord)
                        continue
                    text = str(cached).strip()
                else:
                    if raw_f is None:
                        continue
                    text = str(raw_f).strip()
                if not text:
                    continue
                doc.blocks.append(DocBlock(paragraph_id=f"p{len(doc.blocks) + 1}", ordinal=len(doc.blocks),
                                           text=text, location=coord))
    wb_f.close()
    wb_v.close()

    if not doc.blocks and not media:
        raise DocFormatError("EXTRACTION_FAILED", "未从工作簿提取到任何文本内容")
    if formula_without_cache:
        mark_partial(doc, f"含 {len(formula_without_cache)} 个无缓存值的公式单元格未纳入文本"
                          f"（不执行、不猜值）：" + ", ".join(formula_without_cache[:5]))
        doc.warnings.append("存在无缓存公式值，单元格内容未知，按未覆盖处理")
    if truncated:
        mark_partial(doc, f"工作表超出安全解析上限（行≤{MAX_ROWS}/列≤{MAX_COLS}）已截断："
                          + ", ".join(truncated))
        doc.warnings.append("存在超限工作表，超出部分未纳入文本")
    # 未纳入文本的图片：同一媒体只记一次，附带引用锚点
    refs_by_media: dict[str, list[str]] = {}
    for anchor, target in cellmap.items():
        refs_by_media.setdefault(target, []).append(anchor)
    for m in media:
        anchors = refs_by_media.get(m, [])
        suffix = f"（引用锚点 {anchors[0]}）" if anchors else ""
        doc.uncovered_media.append(f"media:{m}{suffix}")
    for target, anchors in refs_by_media.items():
        if target and target not in media:
            doc.uncovered_media.append(f"missing-media:{target}（引用锚点 {anchors[0]}）")
    if doc.uncovered_media:
        doc.warnings.append(
            f"工作簿含 {len(doc.uncovered_media)} 处图片资源未纳入文本审核（WPS DISPIMG 仅识别锚点，不执行公式）")
    finalize_media(doc)
    return doc
