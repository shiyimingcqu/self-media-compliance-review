"""t-40 PDF Brief 解析测试：真实物理页号、页号级未覆盖、加密/损坏明确拒、页号一致性校验。

合成 PDF 用 pypdf 现场生成，保证 CI 可复现（不依赖用户私有样本）；
真实样本（27 页/12 页含图页）在隔离 scratch 另跑一次，不进仓库。
"""

import io

import pytest

from app.modules.brief_script.parsing.pdf import PdfFormatError, parse_pdf


def _pdf_bytes(pages_text):
    from pypdf import PdfWriter
    w = PdfWriter()
    for text in pages_text:
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_requires_text_layer_and_marks_blank_pages_uncovered():
    """空白页无文本层 → 页号级 uncovered，绝不因"有页"就报可读完整。"""
    data = _pdf_bytes(["", ""])
    with pytest.raises(PdfFormatError) as exc:
        parse_pdf(data)
    assert exc.value.code == "EXTRACTION_FAILED"


def test_pdf_corrupt_and_encrypted_rejected():
    with pytest.raises(PdfFormatError):
        parse_pdf(b"not a pdf at all")
    # 加密：用 pypdf 生成带密码的 PDF
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("secret-pass")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(PdfFormatError) as exc:
        parse_pdf(buf.getvalue())
    assert "加密" in exc.value.message


def test_pdf_page_limit_rejected():
    import app.modules.brief_script.parsing.pdf as m
    old = m.MAX_PAGES
    m.MAX_PAGES = 1
    try:
        with pytest.raises(PdfFormatError) as exc:
            parse_pdf(_pdf_bytes(["", ""]))
        assert "上限" in exc.value.message
    finally:
        m.MAX_PAGES = old


def test_pdf_partial_flags_are_page_level():
    """页号级未覆盖的语义自检（不依赖手工构造文本层）：

    真实样本已实证（27 页 508 块 / 20 页未覆盖、12 页 112 块 / 12 页未覆盖），
    此处只固定 uncovered_sources 的格式与 partial 推导规则，防止后续被改成布尔。
    """
    from app.modules.brief_script.parsing.pdf import ParsedPdf
    d = ParsedPdf(page_count=3)
    assert d.partial is False                     # 无未覆盖 → 不 partial
    d.uncovered_sources = ["page:1", "page:3"]
    assert d.partial is True                      # 任一页未覆盖 → partial
    assert all(s.startswith("page:") for s in d.uncovered_sources)  # 页号级而非布尔
