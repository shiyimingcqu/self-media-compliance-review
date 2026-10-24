"""OOXML Strict 变体（purl.oclc.org）归一化测试。

真实样本背景：Word 勾选"以 Strict 类型"另存的 .docx（或某些在线转换器产出）文件完好、
正文可读，但 python-docx 只认 Transitional 命名空间，会在"按关系类型找主文档"这一步
KeyError，用户侧表现为"未能完整解析脚本"。这里锁定：归一化后解析结果与 Transitional
完全一致，且不含 Strict 的包不被重建。
"""

import io
import zipfile

import pytest

from app.modules.brief_script.parsing.doc import (DocFormatError, normalize_strict_ooxml,
                                                 open_zip_guarded)
from app.modules.brief_script.parsing.docx import parse_docx
from test_real_format_parsing import _docx_bytes

# Transitional → Strict 的反向映射（构造样本用；与生产代码的正向表互为镜像）
TRANSITIONAL_TO_STRICT = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "http://purl.oclc.org/ooxml/wordprocessingml/main",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "http://purl.oclc.org/ooxml/drawingml/main",
    "http://schemas.openxmlformats.org/drawingml/2006/picture": "http://purl.oclc.org/ooxml/drawingml/picture",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing": "http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships": "http://purl.oclc.org/ooxml/officeDocument/relationships",
    "http://schemas.openxmlformats.org/officeDocument/2006/math": "http://purl.oclc.org/ooxml/officeDocument/math",
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties": "http://purl.oclc.org/ooxml/officeDocument/extendedProperties",
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties": "http://purl.oclc.org/ooxml/officeDocument/customProperties",
    "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes": "http://purl.oclc.org/ooxml/officeDocument/docPropsVTypes",
}


def to_strict(payload: bytes) -> bytes:
    """把包内 XML/关系成员改写成 OOXML Strict 命名空间（媒体原样复制）。"""
    src = zipfile.ZipFile(io.BytesIO(payload))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith((".xml", ".rels")):
                text = data.decode("utf-8", errors="ignore")
                for transitional, strict in TRANSITIONAL_TO_STRICT.items():
                    text = text.replace(transitional, strict)
                data = text.encode("utf-8")
            dst.writestr(name, data)
    return out.getvalue()


def _strict_member_count(payload: bytes) -> int:
    zf = zipfile.ZipFile(io.BytesIO(payload))
    return sum(1 for name in zf.namelist()
               if name.endswith((".xml", ".rels")) and b"purl.oclc.org" in zf.read(name))


def test_sample_is_really_the_unsupported_variant():
    """先证明样本确实是"库读不了"：改写后 python-docx 直接打开必须失败。"""
    from docx import Document
    data = to_strict(_docx_bytes(["正文一"]))
    assert _strict_member_count(data) > 0
    with pytest.raises(Exception) as error:
        Document(io.BytesIO(data))
    assert "officeDocument" in str(error.value)


def test_strict_docx_blocks_and_locations_match_transitional():
    """归一化后段落/表格定位与 Transitional 完全一致，图片仍按 partial 标注。"""
    data = _docx_bytes(["正文一", "正文二"], table=[["时长", "特效"], ["6S", "偷笑女"]], with_media=True)
    plain = parse_docx(data)
    strict = parse_docx(to_strict(data))
    assert [(b.location, b.text) for b in strict.blocks] == [(b.location, b.text) for b in plain.blocks]
    assert [b.text for b in strict.blocks] == ["正文一", "正文二", "时长", "特效", "6S", "偷笑女"]
    assert strict.coverage == "partial" and strict.uncovered_media == ["media:word/media/image1.png"]


def test_transitional_package_is_not_rebuilt():
    """不含 Strict 命名空间时不得重建压缩包（零改写），避免给正常文件引入无谓变动。"""
    plain = _docx_bytes(["正文一"])
    assert normalize_strict_ooxml(open_zip_guarded(plain)) == (None, 0)
    rebuilt, rewritten = normalize_strict_ooxml(open_zip_guarded(to_strict(plain)))
    assert rewritten > 0 and rebuilt is not None
    # 重建包仍通过压缩包安全门，且不再残留 Strict 命名空间
    assert _strict_member_count(rebuilt) == 0
    open_zip_guarded(rebuilt)


def test_corrupt_document_xml_names_both_possible_causes():
    """包结构合法但主文档 XML 破损：文案要同时点出"可能损坏"与"暂不支持的变体"。"""
    src = zipfile.ZipFile(io.BytesIO(_docx_bytes(["正文一"])))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "word/document.xml":
                data = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            dst.writestr(name, data)
    with pytest.raises(DocFormatError) as error:
        parse_docx(out.getvalue())
    assert error.value.code == "DOCX_STRUCTURE_UNSUPPORTED"
    assert "损坏" in error.value.message and "另存" in error.value.message
    assert "docx" in error.value.message
