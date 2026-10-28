"""Use native PDF geometry to check VLM boxes, without guessing reading order."""
import ctypes
import hashlib
import math
import re


def _intersection(a,b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def _area(box):return (box[2]-box[0])*(box[3]-box[1])


def _union(a,b):return [min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])]


def pdf_geometry(settings,snapshot):
    """Optional native evidence. Scanned/rotated PDFs retain the VLM path."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from app.modules.foundation.files_service import get_asset
    _,path=get_asset(settings,snapshot['fileId'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=snapshot['fileSha256']:raise ValueError('PDF source integrity')
    result={}
    with pdfium.PdfDocument(path) as document:
        for index in range(len(document)):
            page=document[index]
            try:
                if page.get_rotation()!=0:continue
                width,height=page.get_size()
                if min(width,height)<=0:continue
                def normalized(bounds):
                    l,b,r,t=bounds
                    return [max(0,math.floor(1000*l/width)),max(0,math.floor(1000*(height-t)/height)),
                            min(1000,math.ceil(1000*r/width)),min(1000,math.ceil(1000*(height-b)/height))]
                textpage=page.get_textpage()
                try:
                    panels=[];spans=[]
                    for obj in page.get_objects(max_depth=1):
                        bounds=obj.get_bounds();box=normalized(bounds)
                        if _area(box)<=0:continue
                        if obj.type==raw.FPDF_PAGEOBJ_TEXT:
                            text=textpage.get_text_bounded(*bounds).replace('\r\n','\n').strip()
                            if text and '\ufffd' not in text:spans.append({'bbox':box,'text':text})
                        if obj.type!=raw.FPDF_PAGEOBJ_PATH or raw.FPDFPath_CountSegments(obj)!=5:continue
                        fill=ctypes.c_int();stroke=ctypes.c_int()
                        if not raw.FPDFPath_GetDrawMode(obj,ctypes.byref(fill),ctypes.byref(stroke)) or not fill.value:continue
                        rgba=[ctypes.c_uint() for _ in range(4)]
                        if not raw.FPDFPageObj_GetFillColor(obj,*[ctypes.byref(v) for v in rgba]) or rgba[3].value<250:continue
                        # Only axis-aligned rectangular paths, not a curved logo's bounds.
                        points=[];valid=True
                        for n in range(5):
                            segment=raw.FPDFPath_GetPathSegment(obj,n)
                            if raw.FPDFPathSegment_GetType(segment) not in (raw.FPDF_SEGMENT_MOVETO,raw.FPDF_SEGMENT_LINETO):valid=False;break
                            x=ctypes.c_float();y=ctypes.c_float()
                            raw.FPDFPathSegment_GetPoint(segment,ctypes.byref(x),ctypes.byref(y))
                            points.append((round(x.value,2),round(y.value,2)))
                        if not valid or len({p[0] for p in points})!=2 or len({p[1] for p in points})!=2:continue
                        if _area(box)>850000 or box[3]-box[1]<25:continue
                        text=textpage.get_text_bounded(*bounds).replace('\r\n','\n').strip()
                        panels.append({'id':f'pdf-panel-{len(panels)+1}','bbox':box,'text':text})
                    result[index+1]={'panels':panels,'spans':spans}
                finally:textpage.close()
            finally:page.close()
    return result


def layout_candidates(geometry,page_no):
    return [{'id':p['id'],'bbox':p['bbox'],'label':p['text'][:80]} for p in geometry.get(page_no,{}).get('panels',[])][:60]


def refine_page(page,geometry):
    """Snap to actual panels and complete intersected text lines before cropping."""
    evidence=geometry.get(page.page_no,{})
    panels=evidence.get('panels',[]);spans=evidence.get('spans',[]);changes=[];regions=[];containers={}
    row_labels=[p for p in panels if p['bbox'][2]-p['bbox'][0]<140 and p['bbox'][3]-p['bbox'][1]>60
                and 0<len(p['text'])<=20 and '\n' not in p['text']]
    materials={'文案':'copy','图片':'image','视频':'video','脚本':'script','copy':'copy','image':'image','video':'video'}
    for original in page.regions:
        region=original.model_copy(deep=True);box=region.bbox;selected=None;best=0
        for panel in panels:
            candidate=panel['bbox']
            if candidate[2]-candidate[0]<140:continue
            overlap=_intersection(box,candidate);score=overlap/(_area(box)+_area(candidate)-overlap)
            if getattr(region,'container_id',None)==panel['id']:
                selected=panel;break
            if score>best and score>=0.24 and overlap/min(_area(box),_area(candidate))>=0.50:
                selected=panel;best=score
        if selected:
            box=selected['bbox'].copy()
            labels=[label for label in row_labels if label['bbox'][2]<=box[0]+5 and box[0]-label['bbox'][2]<600
                    and max(0,min(box[3],label['bbox'][3])-max(box[1],label['bbox'][1]))/(label['bbox'][3]-label['bbox'][1])>=0.45]
            labels.sort(key=lambda p:p['bbox'][1])
            if labels:
                region.row_header='／'.join(dict.fromkeys(p['text'] for p in labels))
                mapped=[materials.get(p['text']) for p in labels]
                if all(mapped):region.applicability.materials=list(dict.fromkeys(mapped))
            headings=[p for p in panels if p['id']!=selected['id'] and 0<len(p['text'])<=20 and '\n' not in p['text']
                      and p['bbox'][3]-p['bbox'][1]<=100
                      and p['bbox'][3]<=box[1]+5 and box[1]-p['bbox'][3]<700
                      and max(0,min(box[2],p['bbox'][2])-max(box[0],p['bbox'][0]))/(box[2]-box[0])>=0.8
                      and not any(other['id']!=p['id'] and _area(other['bbox'])>2*_area(p['bbox'])
                                  and _intersection(p['bbox'],other['bbox'])/_area(p['bbox'])>=0.97 for other in panels)]
            if headings:region.column_header=max(headings,key=lambda p:p['bbox'][3])['text']
            if region.row_header and region.column_header:region.label=region.row_header+' / '+region.column_header
            region.applicability.section=region.row_header or region.label
            region.applicability.section_quote=region.row_header
            region.applicability.region=region.label
        else:
            # A line cut at either edge must be included in full. We never use
            # text words to infer row membership, only native box intersections.
            for span in spans:
                if _intersection(original.bbox,span['bbox'])/_area(span['bbox'])>=0.55:box=_union(box,span['bbox'])
        region.bbox=box
        if box!=original.bbox:changes.append({'regionId':region.id,'oldBbox':original.bbox,'bbox':box,'method':'native-panel' if selected else 'native-text-boundary'})
        identity=selected['id'] if selected else None
        if identity and identity in containers and containers[identity].role==region.role:
            existing=containers[identity]
            existing.applicability.materials=list(dict.fromkeys([*existing.applicability.materials,*region.applicability.materials]))
            if existing.row_header!=region.row_header:existing.row_header='／'.join(dict.fromkeys([existing.row_header,region.row_header]))
            changes.append({'regionId':region.id,'mergedInto':existing.id,'method':'same-native-panel'})
        else:
            regions.append(region)
            if identity:containers[identity]=region
    return page.model_copy(update={'regions':regions}),changes
