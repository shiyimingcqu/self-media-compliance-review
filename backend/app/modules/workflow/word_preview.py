"""Adapt Word layout declarations for docx-preview, on a display copy only.

docx-preview parses w:line with parseInt and assumes a unitless number. Strict
files can contain a length such as 18pt: reading this as 18/240 collapses lines.
Preserve the declared physical length as twips and an exact line height. Never
rewrite source versions, audit records, or the input used for AI analysis.
"""
from decimal import Decimal, ROUND_HALF_UP
import io
import re
import zipfile

from app.modules.brief_script.parsing.doc import open_zip_guarded, safe_xml

WORD_NAMESPACES = {'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                   'http://purl.oclc.org/ooxml/wordprocessingml/main'}
LENGTH = re.compile(r'^([0-9]+(?:\.[0-9]+)?)(pt|in|cm|mm|pc|pi)$', re.I)
TWIPS = {'pt': Decimal(20), 'in': Decimal(1440), 'cm': Decimal(1440)/Decimal('2.54'),
         'mm': Decimal(1440)/Decimal('25.4'), 'pc': Decimal(240), 'pi': Decimal(240)}


def twips_of(value):
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',value):return Decimal(value)
    match=LENGTH.fullmatch(value)
    return Decimal(match[1])*TWIPS[match[2].lower()] if match else None


def adapt_layout(root):
    rewritten=0
    for element in root.iter():
        if not isinstance(element.tag,str) or not element.tag.startswith('{'):continue
        namespace,_,name=element.tag[1:].partition('}')
        if namespace not in WORD_NAMESPACES:continue
        q=lambda local:'{'+namespace+'}'+local
        if name=='spacing':
            match=LENGTH.fullmatch(element.get(q('line'),''))
            if not match:continue
            twips=(Decimal(match[1])*TWIPS[match[2].lower()]).quantize(Decimal(1),rounding=ROUND_HALF_UP)
            if twips<=0:continue
            element.set(q('line'),str(twips))
            if element.get(q('lineRule'),'auto')=='auto':element.set(q('lineRule'),'exact')
            rewritten+=1
        elif name=='tblLayout':
            # The renderer mistakenly reads `val`, whereas Word records `type`.
            # Add a renderer-only alias, preserving the actual source declaration.
            mode=element.get(q('type'))
            if mode in {'fixed','autofit'} and element.get(q('val'))!=mode:
                element.set(q('val'),mode);rewritten+=1
        elif name=='tbl':
            properties=element.find(q('tblPr'));grid=element.find(q('tblGrid'))
            if properties is None or grid is None:continue
            width=properties.find(q('tblW'));layout=properties.find(q('tblLayout'))
            if width is None or twips_of(width.get(q('w'),''))!=0:continue
            mode=width.get(q('type'),'dxa')
            if mode!='dxa' and not (mode=='auto' and layout is not None and layout.get(q('type'))=='fixed'):continue
            columns=[twips_of(column.get(q('w'),'')) for column in grid if column.tag==q('gridCol')]
            if not columns or any(value is None or value<=0 for value in columns):continue
            # Recover from the recorded grid, never from an invented page-width guess.
            total=sum(columns).quantize(Decimal(1),rounding=ROUND_HALF_UP)
            width.set(q('w'),str(total));width.set(q('type'),'dxa');rewritten+=1
    return rewritten


def normalize_word_preview(payload: bytes) -> tuple[bytes, int]:
    # lxml is the same XML library used by the existing python-docx parser;
    # preserving namespace prefixes also preserves drawing/compatibility markup.
    from lxml import etree
    rewritten = 0
    members = []
    with open_zip_guarded(payload) as archive:
        for info in archive.infolist():
            data = archive.read(info.filename)
            if info.filename.startswith('word/') and info.filename.endswith('.xml'):
                safe_xml(archive, info.filename)
                root=etree.fromstring(data,parser=etree.XMLParser(resolve_entities=False,no_network=True))
                count=adapt_layout(root)
                if count:
                    rewritten+=count
                    data = etree.tostring(root, encoding='UTF-8', xml_declaration=True)
            members.append((info, data))
    if not rewritten:return payload, 0
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for info,data in members:archive.writestr(info,data)
    return output.getvalue(), rewritten


def source_bookmark_name(file_id: str, location: str) -> str:
    return 'crv_'+file_id[:8]+'_'+location.replace(':','_')


def add_source_bookmarks(payload: bytes, file_id: str) -> tuple[bytes, int]:
    """Zero-length display bookmarks use the exact coordinates of parse_docx.

    python-docx expands merged cells to logical grid columns. Reusing iter_body
    and row.cells preserves those aliases rather than guessing from HTML order.
    Only document.xml in the preview copy changes; media and source stay intact.
    """
    from lxml import etree
    from docx.table import Table
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from app.modules.brief_script.parsing.docx import _normalized, _open_document, iter_body
    payload=_normalized(payload)
    document=_open_document(payload)
    existing={node.get(qn('w:name')) for node in document.element.iter(qn('w:bookmarkStart'))}
    ids=[int(node.get(qn('w:id'),'0')) for node in document.element.iter(qn('w:bookmarkStart')) if node.get(qn('w:id'),'0').isdigit()]
    next_id=max(ids,default=0)+1
    count=0
    def mark(paragraph, location):
        nonlocal next_id,count
        name=source_bookmark_name(file_id,location)
        if name in existing:return
        start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),str(next_id));start.set(qn('w:name'),name)
        end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),str(next_id))
        position=1 if len(paragraph) and paragraph[0].tag==qn('w:pPr') else 0
        paragraph.insert(position,start);paragraph.insert(position+1,end)
        existing.add(name);next_id+=1;count+=1
    for location,item in iter_body(document):
        if isinstance(item,Table):
            for row_index,row in enumerate(item.rows):
                for column_index,cell in enumerate(row.cells):
                    if cell.paragraphs:mark(cell.paragraphs[0]._p,f'{location}{row_index}c{column_index}')
        else:mark(item._p,location)
    if not count:return payload,0
    output=io.BytesIO()
    with open_zip_guarded(payload) as source,zipfile.ZipFile(output,'w') as target:
        for info in source.infolist():
            data=etree.tostring(document.element,encoding='UTF-8',xml_declaration=True) if info.filename=='word/document.xml' else source.read(info.filename)
            target.writestr(info,data)
    return output.getvalue(),count
