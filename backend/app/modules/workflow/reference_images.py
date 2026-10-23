"""Project-scoped immutable PNG references, with bounded decode and idempotent upload."""
import hashlib
import io
import json
import warnings
from uuid import uuid4

from fastapi import APIRouter, File, Header, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps

from app.core.contracts import Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only

router=APIRouter(tags=['video-references'])
MAX_BYTES=4*1024*1024


def root(settings):
    path=settings.data_dir/'video-references';path.mkdir(parents=True,exist_ok=True)
    return path


def get_reference(db,settings,project_id,identity,include_data=False):
    row=db.execute('SELECT * FROM video_reference_images WHERE id=? AND project_id=?',(identity,project_id)).fetchone()
    if not row:
        raise ApiError(404,'REFERENCE_NOT_FOUND','参考图不属于当前项目')
    value=dict(row)
    if include_data:
        data=(root(settings)/(row['id']+'.png')).read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ApiError(409,'REFERENCE_CHANGED','参考图校验失败，请重新上传')
        value['data']=data
    return value


@router.get('/projects/{project_id}/reference-images',response_model=Envelope[list[dict]])
def list_references(project_id:str,request:Request):
    local_only(request)
    from .standards import project_for
    with connection(request.app.state.settings.database_path) as db:
        project_for(db,project_id)
        rows=db.execute('SELECT * FROM video_reference_images WHERE project_id=? ORDER BY created_at,id',(project_id,)).fetchall()
    return Envelope(data=[dict(r) for r in rows],request_id=request.state.request_id)


@router.post('/projects/{project_id}/reference-images',response_model=Envelope[dict],status_code=201)
async def upload_reference(project_id:str,request:Request,file:UploadFile=File(...),idempotency_key:str=Header(...,alias='Idempotency-Key',min_length=8,max_length=128)):
    local_only(request);settings=request.app.state.settings
    from .standards import project_for
    with connection(settings.database_path) as db:
        project_for(db,project_id)
    try:
        data=await file.read(MAX_BYTES+1)
        if not data or len(data)>MAX_BYTES:
            raise ApiError(413,'REFERENCE_IMAGE_LIMIT','参考图须在4MiB以内')
        fingerprint=hashlib.sha256(data).hexdigest()
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ('PNG','JPEG','WEBP') or im.width*im.height>12_000_000 or getattr(im,'n_frames',1)!=1:
                    raise ValueError()
                image=ImageOps.exif_transpose(im).convert('RGBA')
                image=Image.alpha_composite(Image.new('RGBA',image.size,'white'),image).convert('RGB')
                encoded=io.BytesIO();image.save(encoded,format='PNG');normalized=encoded.getvalue()
                width,height=image.size
        if len(normalized)>MAX_BYTES:
            raise ApiError(413,'REFERENCE_IMAGE_LIMIT','参考图转码后超过4MiB，请使用较小图片')
    except ApiError:raise
    except Exception:
        raise ApiError(415,'REFERENCE_IMAGE_INVALID','请提供可读取的静态PNG、JPEG或WebP图片') from None
    finally:
        await file.close()
    scope='reference-image:'+project_id
    from app.core.files import sanitize_original_name
    name=sanitize_original_name(file.filename or '产品参考图')
    fingerprint=hashlib.sha256((fingerprint+'|'+name).encode()).hexdigest()
    path=None
    try:
        with connection(settings.database_path) as db:
            db.execute('BEGIN IMMEDIATE');project_for(db,project_id)
            prior=db.execute('SELECT * FROM idempotency_records WHERE scope=? AND key=?',(scope,idempotency_key)).fetchone()
            if prior:
                if prior['request_hash']!=fingerprint:
                    raise ApiError(409,'IDEMPOTENCY_CONFLICT','幂等键已用于其他参考图')
                return Envelope(data=json.loads(prior['response_json']),request_id=request.state.request_id)
            if db.execute('SELECT COUNT(*) FROM video_reference_images WHERE project_id=?',(project_id,)).fetchone()[0]>=30:
                raise ApiError(409,'REFERENCE_IMAGE_LIMIT','每个项目最多30张参考图')
            identity=uuid4().hex;stamp=now();digest=hashlib.sha256(normalized).hexdigest()
            path=root(settings)/(identity+'.png');path.write_bytes(normalized)
            db.execute('INSERT INTO video_reference_images VALUES (?,?,?,?,?,?,?,?)',(identity,project_id,name,digest,width,height,len(normalized),stamp))
            value=get_reference(db,settings,project_id,identity)
            db.execute('INSERT INTO idempotency_records VALUES (?,?,?,?,?)',(scope,idempotency_key,fingerprint,json.dumps(value),stamp))
    except BaseException:
        if path:path.unlink(missing_ok=True)
        raise
    return Envelope(data=value,request_id=request.state.request_id)


@router.get('/projects/{project_id}/reference-images/{identity}/content')
def reference_content(project_id:str,identity:str,request:Request):
    local_only(request)
    from .standards import project_for
    with connection(request.app.state.settings.database_path) as db:
        project_for(db,project_id)
        value=get_reference(db,request.app.state.settings,project_id,identity,True)
    return Response(value['data'],media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
