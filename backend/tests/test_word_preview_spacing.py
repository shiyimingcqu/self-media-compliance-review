import io
import zipfile

import pytest

from app.modules.brief_script.parsing.doc import DocFormatError
from app.modules.workflow.word_preview import WORD_NAMESPACES, normalize_word_preview


def package(xml):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',xml)
        archive.writestr('word/media/image.png',b'unchanged-image')
        archive.writestr('word/_rels/document.xml.rels',b'unchanged-relationships')
    return output.getvalue()


@pytest.mark.parametrize('namespace',WORD_NAMESPACES)
@pytest.mark.parametrize('length',['18pt','0.25in','0.635cm','6.35mm','1.5pc','1.5pi'])
def test_physical_line_spacing_keeps_its_physical_size(namespace,length):
    from lxml import etree
    raw=package(f'<w:document xmlns:w="{namespace}"><w:body><w:p><w:pPr><w:spacing w:line="{length}" w:lineRule="auto"/></w:pPr><w:r><w:t>两行文字不应重叠</w:t></w:r></w:p></w:body></w:document>')
    result,count=normalize_word_preview(raw)
    assert count==1
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        root=etree.fromstring(archive.read('word/document.xml'))
        spacing=root.find('.//{'+namespace+'}spacing')
        assert spacing.get('{'+namespace+'}line')=='360'
        assert spacing.get('{'+namespace+'}lineRule')=='exact'
        assert root.find('.//{'+namespace+'}t').text=='两行文字不应重叠'
        assert archive.read('word/media/image.png')==b'unchanged-image'
        assert archive.read('word/_rels/document.xml.rels')==b'unchanged-relationships'
    assert normalize_word_preview(result)==(result,0)


def test_unitless_word_line_ratios_are_not_reinterpreted():
    raw=package('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr></w:p></w:body></w:document>')
    assert normalize_word_preview(raw)==(raw,0)


def test_preview_does_not_resolve_xml_entities():
    raw=package('<!DOCTYPE doc [<!ENTITY xxe SYSTEM "file:///private">]><doc>&xxe;</doc>')
    with pytest.raises(DocFormatError):normalize_word_preview(raw)


@pytest.mark.parametrize('namespace',WORD_NAMESPACES)
@pytest.mark.parametrize('mode',['auto','dxa'])
def test_zero_table_width_is_recovered_from_its_recorded_columns(namespace,mode):
    from lxml import etree
    raw=package(f'<w:document xmlns:w="{namespace}"><w:body><w:tbl><w:tblPr><w:tblW w:w="0pt" w:type="{mode}"/><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid><w:gridCol w:w="100pt"/><w:gridCol w:w="4000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>列一</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>列二文字</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    result,count=normalize_word_preview(raw)
    assert count==2
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        root=etree.fromstring(archive.read('word/document.xml'))
        width=root.find('.//{'+namespace+'}tblW');layout=root.find('.//{'+namespace+'}tblLayout')
        assert width.get('{'+namespace+'}w')=='6000'
        assert width.get('{'+namespace+'}type')=='dxa'
        assert layout.get('{'+namespace+'}type')==layout.get('{'+namespace+'}val')=='fixed'
        assert [item.text for item in root.findall('.//{'+namespace+'}t')]==['列一','列二文字']
        assert archive.read('word/media/image.png')==b'unchanged-image'
    assert normalize_word_preview(result)==(result,0)


def test_nonzero_explicit_width_is_not_replaced_by_grid_sum():
    from lxml import etree
    namespace='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    raw=package(f'<w:document xmlns:w="{namespace}"><w:body><w:tbl><w:tblPr><w:tblW w:w="8000" w:type="dxa"/><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid><w:gridCol w:w="6000"/></w:tblGrid></w:tbl></w:body></w:document>')
    result,count=normalize_word_preview(raw)
    assert count==1  # Layout adapter only; existing positive width remains authoritative.
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        root=etree.fromstring(archive.read('word/document.xml'))
        assert root.find('.//{'+namespace+'}tblW').get('{'+namespace+'}w')=='8000'
