"""Read-only XLSX display model. No formula execution, network fetches or AI calls."""
import base64
import hashlib
import io
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.contracts import Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.core.pdf_preview import PDF_LOCK,render_pdf_page
from app.modules.brief_script.parsing.doc import DocFormatError, open_zip_guarded, safe_xml
from app.modules.brief_script.script_service import authorize_task, script_version_detail
from app.modules.foundation.files_service import get_asset
from app.modules.video_model.evidence.console_router import local_only
from .script_media import prepare_media, relationships, R

router=APIRouter(tags=['script-layout'])
MAX_ROWS=500
MAX_COLS=32
MAX_GRID_CELLS=20000


def color(value,theme):
    if not value:return None
    from openpyxl.styles.colors import COLOR_INDEX
    rgb=None
    if value.type=='rgb':rgb=value.rgb[-6:]
    elif value.type=='indexed' and value.indexed<len(COLOR_INDEX):rgb=COLOR_INDEX[value.indexed][-6:]
    elif value.type=='theme' and value.theme<len(theme):rgb=theme[value.theme]
    if not rgb or not re.fullmatch('[0-9a-fA-F]{6}',rgb):return None
    tint=value.tint or 0
    channels=[int(rgb[n:n+2],16) for n in (0,2,4)]
    return '#'+''.join(f'{max(0,min(255,round(c*(1+tint) if tint<0 else c+(255-c)*tint))):02x}' for c in channels)


def cell_style(cell,theme):
    f=cell.font;a=cell.alignment
    style={'fontFamily':(f.name or 'Arial')[:100],'fontSize':max(8,min(64,float(f.sz or 11)*4/3)), 'fontWeight':700 if f.b else 400,'fontStyle':'italic' if f.i else 'normal','textAlign':a.horizontal if a.horizontal in ('left','center','right','justify') else 'left','verticalAlign':{'center':'middle','top':'top','bottom':'bottom'}.get(a.vertical,'bottom'),'whiteSpace':'pre-wrap' if a.wrap_text else 'pre','color':color(f.color,theme) or '#202a36'}
    if f.u:style['textDecoration']='underline'
    if cell.fill.patternType=='solid':style['backgroundColor']=color(cell.fill.fgColor,theme) or '#ffffff'
    for name in ('top','bottom','left','right'):
        side=getattr(cell.border,name)
        if side and side.style:
            width=3 if side.style=='thick' else 2 if side.style in ('medium','double') else 1
            kind='dashed' if 'dash' in side.style.lower() else 'dotted' if side.style=='dotted' else 'double' if side.style=='double' else 'solid'
            style['border'+name.capitalize()]=f'{width}px {kind} {color(side.color,theme) or "#425166"}'
    return style


def drawing_positions(zf,owner,columns,rows):
    """Resolve embedded drawing anchors using the original row/column geometry."""
    from openpyxl.utils import get_column_letter
    found=[]
    for drawing in relationships(zf,owner).values():
        if not drawing.startswith('xl/drawings/') or not drawing.endswith('.xml'):continue
        root=safe_xml(zf,drawing)
        if root is None:continue
        rels=relationships(zf,drawing)
        for anchor in root:
            start=next((n for n in anchor if n.tag.endswith('}from')),None)
            end=next((n for n in anchor if n.tag.endswith('}to')),None)
            extent=next((n for n in anchor if n.tag.endswith('}ext')),None)
            if start is None:continue
            def point(node):
                v={n.tag.rsplit('}',1)[-1]:int(n.text or 0) for n in node}
                c=v.get('col',0);r=v.get('row',0)
                return v,sum(columns[:c])+v.get('colOff',0)/9525,sum(rows[:r])+v.get('rowOff',0)/9525
            try:
                v,x,y=point(start)
                if v.get('col',0)>=len(columns) or v.get('row',0)>=len(rows):continue
                if end is not None:
                    _,ex,ey=point(end);width=ex-x;height=ey-y
                elif extent is not None:width=int(extent.get('cx',0))/9525;height=int(extent.get('cy',0))/9525
                else:continue
                if width<=0 or height<=0:continue
            except (ValueError,TypeError):continue
            for blip in anchor.iter():
                if blip.tag.endswith('}blip'):
                    target=rels.get(blip.get(R+'embed'))
                    if target and target in zf.namelist():
                        data=zf.read(target)
                        found.append({'id':hashlib.sha256(target.encode()+data).hexdigest(),'cell':get_column_letter(v.get('col',0)+1)+str(v.get('row',0)+1),'x':x,'y':y,'width':width,'height':height})
    return found


def build_layout(payload,manifest):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter,range_boundaries
    warnings=['浏览预览保留表格结构与常用样式；字体、自动行高、复杂图形与打印效果可能和Excel不同。公式只读缓存，不计算、不执行。']
    sheets=[];budget=0
    with open_zip_guarded(payload) as zf:
        theme_root=safe_xml(zf,'xl/theme/theme1.xml');theme=[]
        if theme_root is not None:
            scheme=next((n for n in theme_root.iter() if n.tag.endswith('}clrScheme')),None)
            if scheme is not None:
                colors={n.tag.rsplit('}',1)[-1]:next(iter(n),None) for n in scheme}
                for name in ('lt1','dk1','lt2','dk2','accent1','accent2','accent3','accent4','accent5','accent6','hlink','folHlink'):
                    n=colors.get(name);theme.append((n.get('val') if n is not None and n.tag.endswith('}srgbClr') else n.get('lastClr') if n is not None else '') or '')
        wb_rels=relationships(zf,'xl/workbook.xml');wbxml=safe_xml(zf,'xl/workbook.xml')
        owners={n.get('name'):wb_rels.get(n.get(R+'id')) for n in wbxml.iter() if n.tag.rsplit('}',1)[-1]=='sheet'}
        formulas=load_workbook(io.BytesIO(payload),data_only=False,keep_links=False)
        values=load_workbook(io.BytesIO(payload),data_only=True,keep_links=False)
        try:
            for ws in formulas.worksheets[:10]:
                max_row=ws.max_row or 1;max_col=ws.max_column or 1
                for image in manifest['images']:
                    for location in image['locations']:
                        if location.startswith(ws.title+'!') and re.fullmatch('[A-Z]+[1-9][0-9]*',location[len(ws.title)+1:]):
                            c,r,_,_=range_boundaries(location[len(ws.title)+1:]);max_row=max(max_row,r);max_col=max(max_col,c)
                nr=min(max_row,MAX_ROWS);nc=min(max_col,MAX_COLS)
                if budget+nr*nc>MAX_GRID_CELLS:
                    warnings.append(f'{ws.title}超出本次预览网格上限，请查看原文件。');continue
                if nr<max_row or nc<max_col:warnings.append(f'{ws.title}仅预览前{nr}行、{nc}列，超出部分请查看原文件。')
                columns=[max(18,min(900,(ws.sheet_format.defaultColWidth or 8.43)*7+5)) for _ in range(nc)]
                hidden_columns=[]
                for dim in ws.column_dimensions.values():
                    for index in range(max(1,dim.min or 1),min(nc,dim.max or dim.min or 1)+1):
                        columns[index-1]=max(18,min(900,float(dim.width or 8.43)*7+5))
                        if dim.hidden:hidden_columns.append(index)
                rows=[max(16,min(900,float(ws.row_dimensions[r].height or ws.sheet_format.defaultRowHeight or 15)*4/3)) for r in range(1,nr+1)]
                images=drawing_positions(zf,owners.get(ws.title,''),columns,rows)
                # Drawings can extend beyond the last nonempty cell; keep their canvas.
                for image in images:
                    while len(columns)<MAX_COLS and sum(columns)<image['x']+image['width']:columns.append((ws.sheet_format.defaultColWidth or 8.43)*7+5)
                    while len(rows)<MAX_ROWS and sum(rows)<image['y']+image['height']:rows.append((ws.sheet_format.defaultRowHeight or 15)*4/3)
                nr=len(rows);nc=len(columns)
                if budget+nr*nc>MAX_GRID_CELLS:
                    warnings.append(f'{ws.title}图片范围超出本次预览网格上限，请查看原文件。');continue
                budget+=nr*nc
                merges=[]
                for merged in ws.merged_cells.ranges:
                    c1,r1,c2,r2=range_boundaries(str(merged))
                    if r1<=nr and c1<=nc:merges.append({'r1':r1,'c1':c1,'r2':min(r2,nr),'c2':min(c2,nc)})
                cells=[]
                for row in ws.iter_rows(max_row=nr,max_col=nc):
                    for cell in row:
                        if cell.value is None and not cell.has_style:continue
                        formula=cell.data_type=='f';cached=values[ws.title][cell.coordinate].value if formula else cell.value
                        text='' if cached is None else str(cached)
                        cells.append({'address':cell.coordinate,'row':cell.row,'col':cell.column,'text':text,'formula':str(cell.value) if formula else None,'uncached':formula and cached is None,'style':cell_style(cell,theme)})
                placed={(im['id'],im['cell']) for im in images}
                for image in manifest['images']:
                    for location in image['locations']:
                        if not location.startswith(ws.title+'!'):continue
                        address=location[len(ws.title)+1:]
                        if not re.fullmatch('[A-Z]+[1-9][0-9]*',address) or (image['id'],address) in placed:continue
                        c1,r1,_,_=range_boundaries(address)
                        if r1>nr or c1>nc:continue
                        merged=next((m for m in merges if m['r1']<=r1<=m['r2'] and m['c1']<=c1<=m['c2']),None)
                        c2=merged['c2'] if merged else c1;r2=merged['r2'] if merged else r1
                        width=sum(columns[c1-1:c2]);height=sum(rows[r1-1:r2]);scale=min(max(1,width-8)/image['width'],max(1,height-8)/image['height'])
                        images.append({'id':image['id'],'cell':address,'x':sum(columns[:c1-1])+4,'y':sum(rows[:r1-1])+4,'width':image['width']*scale,'height':image['height']*scale})
                known={im['id'] for im in manifest['images']};images=[im for im in images if im['id'] in known]
                sheets.append({'name':ws.title,'rowCount':nr,'colCount':nc,'columns':columns,'rows':rows,'cells':cells,'merges':merges,'images':images,'hiddenRows':[r for r in range(1,nr+1) if ws.row_dimensions[r].hidden],'hiddenColumns':hidden_columns,'hidden':ws.sheet_state!='visible'})
            if len(formulas.worksheets)>10:warnings.append('只预览前10个工作表，其他表请查看原文件。')
        finally:formulas.close();values.close()
    if any(s['hiddenRows'] or s['hiddenColumns'] or s['hidden'] for s in sheets):warnings.append('原文件有隐藏工作表或行列，预览以淡色标记展示，便于核查。')
    if manifest['uncovered']:warnings.extend(manifest['uncovered'])
    return {'supported':True,'sheets':sheets,'warnings':warnings,'images':manifest['images']}


def owned_script(settings,task_id,script_id):
    with connection(settings.database_path) as db:
        authorize_task(db,task_id);script=script_version_detail(db,script_id)
        if script['taskId']!=task_id:raise ApiError(404,'RESOURCE_NOT_FOUND','脚本不属于此任务')
    return script


def original_document(settings, script):
    asset, path = get_asset(settings, script['fileId'])
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != asset.sha256:
        raise ApiError(409, 'SCRIPT_FILE_CHANGED', '原文件校验不符，不能提供预览')
    if asset.extension in {'doc', 'docx'}:
        from app.modules.brief_script.office_preview import convert_office
        path = convert_office(payload, asset.extension, 'pdf', settings.data_dir / 'preview-cache')
    elif asset.extension != 'pdf':
        raise ApiError(415, 'PREVIEW_UNSUPPORTED', '此文件不是分页文档')
    return path


def word_document(settings, script):
    asset,path=get_asset(settings,script['fileId'])
    payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=asset.sha256:raise ApiError(409,'SCRIPT_FILE_CHANGED','原文件校验不符')
    if asset.extension=='doc':
        from app.modules.brief_script.office_preview import convert_office
        return convert_office(payload,'doc','docx',settings.data_dir / 'preview-cache')
    if asset.extension!='docx':raise ApiError(415,'PREVIEW_UNSUPPORTED','此文件不是 Word 文档')
    # Apply the existing OOXML archive limits before handing the source to the browser.
    with open_zip_guarded(payload):pass
    return path


def document_metadata(path):
    import pypdfium2 as pdfium
    try:
        with PDF_LOCK, pdfium.PdfDocument(str(path)) as document:
            pages = []
            for index in range(len(document)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    pages.append({'page': index + 1, 'width': width, 'height': height})
                finally:page.close()
            return pages
    except Exception:
        raise ApiError(422, 'DOCUMENT_PREVIEW_INVALID', '文档无法打开，可能已加密或损坏；请下载原文件核对。') from None


@router.get('/tasks/{task_id}/scripts/{script_id}/layout',response_model=Envelope[dict])
def script_layout(task_id:str,script_id:str,request:Request):
    local_only(request);settings=request.app.state.settings;script=owned_script(settings,task_id,script_id)
    if not script.get('fileId'):return Envelope(data={'supported':False,'sheets':[],'warnings':[],'images':[]},request_id=request.state.request_id)
    asset,path=get_asset(settings,script['fileId'])
    if asset.extension in {'doc','docx'} and os.getenv('REVIEW_DOCX_PREVIEW','browser')!='pdf':
        try:word_document(settings,script)
        except DocFormatError as exc:raise ApiError(422,exc.code,exc.message)
        return Envelope(data={'supported':False,'sheets':[],'images':[],'warnings':[],
                              'original':{'kind':'docx','format':asset.extension}},request_id=request.state.request_id)
    if asset.extension in {'doc','docx','pdf'}:
        pages = document_metadata(original_document(settings, script))
        return Envelope(data={'supported':False,'sheets':[],'images':[], 'warnings':[],
                              'original':{'kind':'pages','format':asset.extension,'pages':pages}}, request_id=request.state.request_id)
    if asset.extension == 'txt':
        from app.modules.brief_script.parsing.txt import ParseFailure, parse_txt
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=asset.sha256:raise ApiError(409,'SCRIPT_FILE_CHANGED','原文件校验不符')
        try:text = parse_txt(payload).text
        except ParseFailure as exc:raise ApiError(422,exc.code,exc.message)
        return Envelope(data={'supported':False,'sheets':[],'images':[], 'warnings':[],
                              'original':{'kind':'text','format':'txt','text':text}}, request_id=request.state.request_id)
    if asset.extension == 'xls':
        from app.modules.brief_script.office_preview import convert_office
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=asset.sha256:raise ApiError(409,'SCRIPT_FILE_CHANGED','原文件校验不符')
        path=convert_office(payload,'xls','xlsx',settings.data_dir / 'preview-cache')
        manifest,_=prepare_media(settings,script)
        layout=build_layout(path.read_bytes(),manifest)
        return Envelope(data={**layout,'scriptVersionId':script_id,'fileId':script['fileId']},request_id=request.state.request_id)
    if asset.extension!='xlsx':return Envelope(data={'supported':False,'sheets':[],'warnings':[],'images':[]},request_id=request.state.request_id)
    payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=asset.sha256:raise ApiError(409,'SCRIPT_FILE_CHANGED','原文件校验不符，不能提供表格预览')
    try:
        manifest,_=prepare_media(settings,script);layout=build_layout(payload,manifest)
    except DocFormatError as exc:raise ApiError(400,exc.code,exc.message)
    except (ValueError,KeyError,TypeError):raise ApiError(400,'SCRIPT_LAYOUT_INVALID','表格布局无法读取，请查看原文件或使用文字预览')
    return Envelope(data={**layout,'scriptVersionId':script_id,'fileId':script['fileId']},request_id=request.state.request_id)


@router.get('/tasks/{task_id}/scripts/{script_id}/layout/document')
def script_word_document(task_id:str,script_id:str,request:Request):
    local_only(request);settings=request.app.state.settings;script=owned_script(settings,task_id,script_id)
    if not script.get('fileId'):raise ApiError(404,'RESOURCE_NOT_FOUND','此脚本没有原文件')
    try:
        path=word_document(settings,script)
        from .word_preview import normalize_word_preview,add_source_bookmarks
        payload, count=normalize_word_preview(path.read_bytes())
        payload, anchors=add_source_bookmarks(payload,script['fileId'])
    except DocFormatError as exc:raise ApiError(422,exc.code,exc.message)
    return Response(payload,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
                             'X-Preview-Formatting-Normalized':str(count),'X-Preview-Source-Anchors':str(anchors)})


@router.get('/tasks/{task_id}/scripts/{script_id}/layout/pages/{page_no}')
def script_document_page(task_id:str,script_id:str,page_no:int,request:Request):
    local_only(request);settings=request.app.state.settings;script=owned_script(settings,task_id,script_id)
    if not script.get('fileId'):raise ApiError(404,'RESOURCE_NOT_FOUND','此脚本没有原文件')
    asset, source = get_asset(settings,script['fileId'])
    if asset.extension == 'xls':
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=asset.sha256:raise ApiError(409,'SCRIPT_FILE_CHANGED','原文件校验不符')
        from app.modules.brief_script.office_preview import convert_office
        path=convert_office(payload,'xls','pdf',settings.data_dir / 'preview-cache')
    else:
        path=original_document(settings,script)
    return Response(render_pdf_page(path,page_no),media_type='image/png',headers={'Cache-Control':'private, max-age=3600','X-Content-Type-Options':'nosniff'})


@router.get('/tasks/{task_id}/scripts/{script_id}/layout/images/{image_id}')
def script_layout_image(task_id:str,script_id:str,image_id:str,request:Request):
    local_only(request);settings=request.app.state.settings;script=owned_script(settings,task_id,script_id)
    _,inputs=prepare_media(settings,script)
    image=next((im for im in inputs if im['id']==image_id),None)
    if not image:raise ApiError(404,'RESOURCE_NOT_FOUND','图片不属于此脚本或无法解码')
    return Response(base64.b64decode(image['dataUrl'].split(',',1)[1]),media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
