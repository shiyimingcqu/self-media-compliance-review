"""DOCX 真实解析（t-38）：python-docx 读段落与表格，保留**精确**结构化定位与**原 body 顺序**。

- 跨段落/表格顺序：用库公共 `iter_inner_content()` 按 body 原始顺序输出，
  **不**先全段落再全表格（那样会把"段落-表格-段落"重排成"段落-段落-表格"）。
- 段落：location=`paragraph:N`（N=段落在该 body 顺序中的序号）。
- 表格：**每个非空单元格一个块**，location=`table:T:rRcC`（表序/行/列俱全，不把整行拼成一段）。
- 段落数超上限 → 明确 partial 警示，不静默截断后报 full。
- 图片：word/media/* 存在而文本未纳入 → uncoveredMedia（按媒体去重）+ partial；
  验收时由 `docx_images()` 与文字一起作为图片送模型，定位与本文块同一套坐标。
- 不执行宏/外链/嵌入对象；OCX/OLE 嵌入存在即拒绝（不尝试打开）。
"""

import io
import zipfile

from app.modules.brief_script.parsing.doc import (DocBlock, DocFormatError, ParsedDoc, finalize_media,
                                                 mark_partial, normalize_strict_ooxml, open_zip_guarded)

MAX_PARAGRAPHS = 5_000

# r:embed / r:link 属性名带命名空间，Strict 变体用的是 purl.oclc.org 那一套；
# 两种都认，否则 Strict 文档里的图片会全部退化成"位置未知"。
_EMBED_ATTRS = ('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed',
                '{http://purl.oclc.org/ooxml/officeDocument/relationships}embed')


def _open_document(payload: bytes):
    """打开 docx 并处理 OOXML Strict 变体；失败抛 DocFormatError。"""
    from docx import Document

    try:
        return Document(io.BytesIO(_normalized(payload)))
    except Exception:
        raise DocFormatError("DOCX_STRUCTURE_UNSUPPORTED",
                             "文档结构无法解析：文件可能已损坏，或使用了暂不支持的 Word 文档变体"
                             "（如含宏、加密或另存为其他 OOXML 方言的 .docx）。"
                             "请在 Word 中重新另存为普通 .docx 后提交；仍失败请改用 TXT 或 Excel 格式") from None


def _normalized(payload: bytes) -> bytes:
    """OOXML Strict → Transitional；不含 Strict 时原样返回（幂等，可重复调用）。"""
    normalized, rewritten = normalize_strict_ooxml(open_zip_guarded(payload))
    return normalized if rewritten else payload


def iter_body(document):
    """按 body 原始顺序产出 (定位前缀, 段落或表格)。

    段落 → `paragraph:N`；表格 → `table:T:r`（调用方拼 r{行}c{列}）。
    文本解析与图片定位共用这一份遍历，保证两者坐标永远一致。
    """
    from docx.table import Table

    body_pos = 0
    table_idx = 0
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            yield f"table:{table_idx}:r", item
            table_idx += 1
        else:
            yield f"paragraph:{body_pos}", item
        body_pos += 1


def _embed_ids(element):
    """收集元素内所有图片引用的关系 ID（a:blip@r:embed，含嵌套）。"""
    for node in element.iter():
        if node.tag.rsplit('}', 1)[-1] != 'blip':
            continue
        for attr in _EMBED_ATTRS:
            value = node.get(attr)
            if value:
                yield value


def docx_images(payload: bytes):
    """DOCX 内嵌图片 → [(成员路径, [定位...], 字节)]，坐标与 parse_docx 的文本块一致。

    与 xlsx_images 同契约：未被任何引用指向的 word/media/* 也要返回（定位记"位置未知"），
    引用了但包里不存在的目标返回 data=None，让上层显式记未覆盖，绝不静默丢图。
    """
    from docx.table import Table
    from app.modules.workflow.script_media import relationships

    payload = _normalized(payload)          # Strict 变体统一后再读，关系与图片一次口径
    document = _open_document(payload)
    refs: dict[str, list[str]] = {}
    for location, item in iter_body(document):
        if isinstance(item, Table):
            for r_i, row in enumerate(item.rows):
                for c_i, cell in enumerate(row.cells):
                    for rid in _embed_ids(cell._tc):
                        refs.setdefault(rid, []).append(f"{location}{r_i}c{c_i}")
        else:
            for rid in _embed_ids(item._p):
                refs.setdefault(rid, []).append(location)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        targets = relationships(zf, 'word/document.xml')
        members = {n for n in zf.namelist() if n.startswith('word/media/') and not n.endswith('/')}
        by_member: dict[str, list[str]] = {}
        unresolved: list[tuple[str, list[str]]] = []
        for rid, located in refs.items():
            member = targets.get(rid)
            if member:
                by_member.setdefault(member, []).extend(located)
            else:                       # 关系表里查不到该 ID：当作缺图上报，不静默丢
                unresolved.append((f"missing:{rid}", located))
        out = []
        for name in sorted(members | set(by_member)):
            located = list(dict.fromkeys(by_member.get(name, []))) or ['位置未知']
            out.append((name, located, zf.read(name) if name in members else None))
        for name, located in unresolved:
            out.append((name, list(dict.fromkeys(located)), None))
        return out


def parse_docx(payload: bytes) -> ParsedDoc:
    payload = _normalized(payload)   # OOXML Strict 变体：python-docx 只认 Transitional（文件本身没有损坏）
    zf = open_zip_guarded(payload)
    names = zf.namelist()
    if any(n.lower().endswith(".bin") for n in names):
        raise DocFormatError("MODULE_NOT_IMPLEMENTED", "含 OLE 嵌入对象的文档暂不支持（不执行嵌入对象）")
    media = [n for n in names if n.startswith("word/media/") and not n.endswith("/")]
    try:
        from docx.table import Table  # noqa: F401  仅用于探测依赖是否可用
    except ImportError:
        raise DocFormatError("MODULE_NOT_IMPLEMENTED", "DOCX 解析依赖未安装（python-docx）") from None

    d = _open_document(payload)

    doc = ParsedDoc()

    def add(text: str, location: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        doc.blocks.append(DocBlock(paragraph_id=f"p{len(doc.blocks) + 1}", ordinal=len(doc.blocks),
                                   text=text, location=location))

    para_idx = 0
    overflow = False
    # 按 body 原始顺序遍历段落与表格（库公共 API，不自研 XML 排版）
    for location, item in iter_body(d):
        if isinstance(item, Table):
            for r_i, row in enumerate(item.rows):
                for c_i, cell in enumerate(row.cells):
                    add(cell.text, f"{location}{r_i}c{c_i}")   # table:T:r → table:T:rRcC
        else:
            if para_idx >= MAX_PARAGRAPHS:
                overflow = True
            else:
                add(item.text, location)
            para_idx += 1

    if not doc.blocks:
        raise DocFormatError("EXTRACTION_FAILED", "未从文档提取到任何文本内容")
    if overflow:
        mark_partial(doc, f"段落数超过安全上限 {MAX_PARAGRAPHS}，超出部分未纳入文本")
        doc.warnings.append("存在超限段落，未全部解析")
    for m in media:
        doc.uncovered_media.append(f"media:{m}")
    if doc.uncovered_media:
        # 图片不进"提取文字"，但验收时会作为图片一并送审（见 script_media.prepare_media）。
        doc.warnings.append(f"文档含 {len(doc.uncovered_media)} 张图片，未转成文字；验收时会作为图片一起送模型核对")
    finalize_media(doc)
    return doc
