"""DOCX 真实解析（t-38）：python-docx 读段落与表格，保留**精确**结构化定位与**原 body 顺序**。

- 跨段落/表格顺序：用库公共 `iter_inner_content()` 按 body 原始顺序输出，
  **不**先全段落再全表格（那样会把"段落-表格-段落"重排成"段落-段落-表格"）。
- 段落：location=`paragraph:N`（N=段落在该 body 顺序中的序号）。
- 表格：**每个非空单元格一个块**，location=`table:T:rRcC`（表序/行/列俱全，不把整行拼成一段）。
- 段落数超上限 → 明确 partial 警示，不静默截断后报 full。
- 图片：word/media/* 存在而文本未纳入 → uncoveredMedia（按媒体去重）+ partial。
- 不执行宏/外链/嵌入对象；OCX/OLE 嵌入存在即拒绝（不尝试打开）。
"""

from app.modules.brief_script.parsing.doc import (DocBlock, DocFormatError, ParsedDoc, finalize_media,
                                                 mark_partial, open_zip_guarded)

MAX_PARAGRAPHS = 5_000


def parse_docx(payload: bytes) -> ParsedDoc:
    zf = open_zip_guarded(payload)
    names = zf.namelist()
    if any(n.lower().endswith(".bin") for n in names):
        raise DocFormatError("MODULE_NOT_IMPLEMENTED", "含 OLE 嵌入对象的文档暂不支持（不执行嵌入对象）")
    media = [n for n in names if n.startswith("word/media/") and not n.endswith("/")]
    try:
        from docx import Document
        from docx.table import Table
    except ImportError:
        raise DocFormatError("MODULE_NOT_IMPLEMENTED", "DOCX 解析依赖未安装（python-docx）") from None

    import io

    try:
        d = Document(io.BytesIO(payload))
    except Exception:
        raise DocFormatError("EXTRACTION_FAILED", "文档结构无法解析") from None

    doc = ParsedDoc()

    def add(text: str, location: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        doc.blocks.append(DocBlock(paragraph_id=f"p{len(doc.blocks) + 1}", ordinal=len(doc.blocks),
                                   text=text, location=location))

    para_idx = 0
    table_idx = 0
    body_pos = 0      # body 内真实位置（段落与表格共用一个序号），使 location 反映原始位置
    overflow = False
    # 按 body 原始顺序遍历段落与表格（库公共 API，不自研 XML 排版）
    for item in d.iter_inner_content():
        if isinstance(item, Table):
            for r_i, row in enumerate(item.rows):
                for c_i, cell in enumerate(row.cells):
                    add(cell.text, f"table:{table_idx}:r{r_i}c{c_i}")
            table_idx += 1
        else:
            if para_idx >= MAX_PARAGRAPHS:
                overflow = True
            else:
                add(item.text, f"paragraph:{body_pos}")
            para_idx += 1
        body_pos += 1

    if not doc.blocks:
        raise DocFormatError("EXTRACTION_FAILED", "未从文档提取到任何文本内容")
    if overflow:
        mark_partial(doc, f"段落数超过安全上限 {MAX_PARAGRAPHS}，超出部分未纳入文本")
        doc.warnings.append("存在超限段落，未全部解析")
    for m in media:
        doc.uncovered_media.append(f"media:{m}")
    if doc.uncovered_media:
        doc.warnings.append(f"文档含 {len(doc.uncovered_media)} 处图片资源未纳入文本审核")
    finalize_media(doc)
    return doc
