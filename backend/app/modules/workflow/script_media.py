"""Extract embedded XLSX images with controlled OOXML relationship resolution.

Binary payloads exist only in memory during the model call, never in review JSON.
"""
import base64
import hashlib
import io
import posixpath
import re
import warnings

from app.core.errors import ApiError
from app.modules.brief_script.parsing.doc import open_zip_guarded, safe_xml
from app.modules.foundation.files_service import get_asset

R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MAX_IMAGES = 24
MAX_INLINE_BYTES = 20 * 1024 * 1024


def relationships(zf, owner):
    path = posixpath.join(posixpath.dirname(owner), '_rels', posixpath.basename(owner) + '.rels')
    root = safe_xml(zf, path)
    result = {}
    for rel in ([] if root is None else root):
        target = rel.get('Target', '')
        if rel.get('TargetMode') == 'External' or ':' in target or '\\' in target:
            continue
        resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(owner), target))
        if resolved.startswith('../') or resolved == '..':
            continue
        result[rel.get('Id')] = resolved
    return result


def xlsx_images(payload):
    """Return every media member, including unanchored media; never silently omit it."""
    from openpyxl.utils import get_column_letter
    with open_zip_guarded(payload) as zf:
        refs = {}
        cellnames = {}
        ci = safe_xml(zf, 'xl/cellimages.xml')
        ci_rels = relationships(zf, 'xl/cellimages.xml')
        if ci is not None:
            for pic in ci.iter():
                if pic.tag.rsplit('}', 1)[-1] != 'pic':
                    continue
                name = next((n.get('name') for n in pic.iter() if n.tag.rsplit('}',1)[-1] == 'cNvPr'), None)
                target = next((ci_rels.get(n.get(R+'embed')) for n in pic.iter() if n.tag.endswith('}blip')), None)
                if name and target:
                    cellnames[name] = target
                elif name:
                    cellnames[name] = 'unavailable:cellimage#'+name
        workbook = safe_xml(zf, 'xl/workbook.xml')
        wb_rels = relationships(zf, 'xl/workbook.xml')
        for sheet in ([] if workbook is None else workbook.iter()):
            if sheet.tag.rsplit('}', 1)[-1] != 'sheet':
                continue
            owner = wb_rels.get(sheet.get(R+'id'))
            root = safe_xml(zf, owner) if owner else None
            if root is None:
                continue
            sheet_name = sheet.get('name', '?')
            for cell in root.iter():
                if cell.tag.rsplit('}', 1)[-1] != 'c':
                    continue
                formula = next((n.text or '' for n in cell if n.tag.rsplit('}',1)[-1]=='f'), '')
                match = re.search(r'DISPIMG\(\s*"([^"]+)"', formula, re.I)
                if match:
                    target = cellnames.get(match[1], 'unavailable:cellimage#'+match[1])
                    refs.setdefault(target, []).append(sheet_name+'!'+cell.get('r', '?'))
            for drawing in relationships(zf, owner).values():
                if not drawing.startswith('xl/drawings/') or not drawing.endswith('.xml'):
                    continue
                dr = safe_xml(zf, drawing)
                if dr is None:
                    continue
                dr_rels = relationships(zf, drawing)
                for anchor in dr:
                    start = next((n for n in anchor if n.tag.endswith('}from')), None)
                    location = sheet_name+'!位置未知'
                    if start is not None:
                        values = {n.tag.rsplit('}', 1)[-1]: int(n.text or '0') for n in start}
                        location = sheet_name+'!'+get_column_letter(values.get('col', 0)+1)+str(values.get('row', 0)+1)
                    for blip in anchor.iter():
                        if blip.tag.endswith('}blip'):
                            target = dr_rels.get(blip.get(R+'embed'))
                            if target:
                                refs.setdefault(target, []).append(location)
                            else:
                                refs.setdefault('unavailable:'+drawing+'#'+str(blip.get(R+'link') or blip.get(R+'embed')), []).append(location)
        members = sorted({n for n in zf.namelist() if n.startswith('xl/media/') and not n.endswith('/')} | set(refs))
        return [(name, list(dict.fromkeys(refs.get(name, []))) or ['位置未知'], zf.read(name) if name in zf.namelist() else None) for name in members]


def prepare_media(settings, script):
    manifest = {'images': [], 'uncovered': [], 'incomplete': False}
    inputs = []
    if not script.get('fileId'):
        return manifest, inputs
    asset, path = get_asset(settings, script['fileId'])
    if asset.extension not in {'xlsx', 'xls', 'docx', 'doc'}:
        if script.get('parseMeta', {}).get('uncoveredMedia'):
            manifest['uncovered'].append('此格式中的图片尚未纳入多模态审核')
            manifest['incomplete'] = True
        return manifest, inputs
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != asset.sha256:
        raise ApiError(409, 'SCRIPT_FILE_CHANGED', '脚本文件校验失败，请重新上传')
    # 旧版二进制格式先由 LibreOffice 转成对应 OOXML（与脚本正文解析同一套转换），再按 OOXML 提图。
    word_variant = asset.extension in {'docx', 'doc'}
    if asset.extension in {'xls', 'doc'}:
        from app.modules.brief_script.office_preview import convert_office
        target = 'xlsx' if asset.extension == 'xls' else 'docx'
        raw = convert_office(raw, asset.extension, target, settings.data_dir / 'preview-cache').read_bytes()
    # 两种格式各自解析内嵌图片与定位，其余（解码、白底合成、张数/体积预算、失败如实记未覆盖）完全共用。
    if word_variant:
        from app.modules.brief_script.parsing.docx import docx_images
        embedded = docx_images(raw)
    else:
        embedded = xlsx_images(raw)
    from PIL import Image, ImageOps
    total = 0
    for member, locations, data in embedded:
        try:
            if data is None or len(inputs) >= MAX_IMAGES:
                raise ValueError('missing or over count')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    if source.width * source.height > 20_000_000 or getattr(source, 'n_frames', 1) != 1:
                        raise ValueError('oversized or animated image')
                    oriented = ImageOps.exif_transpose(source)
                    # Excel displays transparent drawings on the white worksheet.
                    rgba = oriented.convert('RGBA')
                    image = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
                    # No resizing: preserve text legibility. Oversized requests fail closed.
                    out = io.BytesIO(); image.save(out, format='PNG')
                    encoded = out.getvalue()
                    total += (len(encoded)+2)//3*4
                    if total > MAX_INLINE_BYTES:
                        raise ValueError('request too large')
                    identity = hashlib.sha256(member.encode()+data).hexdigest()
                    # ref：给模型的短引用（i1/i2…）。让模型抄 64 位哈希极易出错，
                    # 真实 id 只作内部稳定标识，由服务端按 ref 还原。
                    ref = f'i{len(inputs) + 1}'
                    manifest['images'].append({'id': identity, 'ref': ref, 'locations': locations, 'mimeType': 'image/png', 'width': image.width, 'height': image.height, 'sha256': hashlib.sha256(encoded).hexdigest()})
                    inputs.append({'id': identity, 'ref': ref, 'dataUrl': 'data:image/png;base64,'+base64.b64encode(encoded).decode()})
        except Exception:
            manifest['uncovered'].append('、'.join(locations)+'：图片缺失、无法解码或超出审核上限')
    manifest['incomplete'] = bool(manifest['uncovered'])
    return manifest, inputs
