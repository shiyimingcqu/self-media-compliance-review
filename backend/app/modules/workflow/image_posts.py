"""Versioned image/text deliverables: immutable media, independent AI and brand gates."""
import base64
import hashlib
import io
import json
import warnings
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Header, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps
from pydantic import Field, ValidationError, field_validator
from typing import Literal

from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.core.files import sanitize_original_name
from app.modules.brief_script.script_service import authorize_task, require_task_writable
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from app.modules.video_model.evidence.secrets import cipher
from .project_brief import review_brief
from .prompts import prompt
from .script_output import OutputError, repair_output, request_options
from .standards import fingerprint, record, replay

router = APIRouter(tags=['image-posts'])
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_ENCODED_BYTES = 20 * 1024 * 1024


class PostImage(ContractModel):
    asset_id: str = Field(min_length=1, max_length=100)
    caption: str = Field(default='', max_length=500)


class PostInput(ContractModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=20000)
    images: list[PostImage] = Field(min_length=1, max_length=18)
    base_version_id: str | None = None

    @field_validator('title', 'body')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():raise ValueError('标题和正文不能为空白')
        return value.strip()


class ImageAnalysisInput(ContractModel):
    version_id: str
    context_key: str
    config_revision: int = Field(ge=1)
    allow_real_call: bool = False


class ImageDecisionInput(ContractModel):
    review_id: str
    context_key: str
    decision: Literal['approved', 'returned']
    note: str = Field(min_length=1, max_length=2000)
    reviewed_material: bool = False


def asset_root(settings):
    path = settings.data_dir / 'image-post-assets'
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_bytes(settings, asset, original=False):
    path = asset_root(settings) / (asset['id'] + ('.source' if original else '.png'))
    if not path.is_file():raise ApiError(409, 'IMAGE_ASSET_MISSING', '图文图片文件缺失，请重新上传')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != asset['source_sha256' if original else 'sha256']:
        raise ApiError(409, 'IMAGE_ASSET_CHANGED', '图文图片与保存的版本不一致，已阻止送审')
    return data


def image_rules(db, project_id):
    row = db.execute('SELECT * FROM project_image_standards WHERE project_id=? ORDER BY revision DESC LIMIT 1', (project_id,)).fetchone()
    return {**dict(row), 'rules': json.loads(row['rules_json'])} if row else None


def version_view(row):
    if not row:return None
    return {'id': row['id'], 'ordinal': row['ordinal'], 'versionLabel': f"图文 V{row['ordinal']}",
            'title': row['title'], 'body': row['body'], 'images': json.loads(row['images_json']), 'createdAt': row['created_at']}


def acceptance(row):
    empty = {'passed': False, 'status': 'pending', 'checks': [], 'blockingCount': 0, 'message': '提交图文后进行 AI 必须项验收'}
    if not row:return empty
    result = json.loads(row['result_json'] or '{}')
    if row['status'] != 'succeeded' or result.get('recovery') or result.get('partialReport'):
        return {**empty, 'status': 'incomplete', 'message': '报告未完整通过校验或经格式修复，不能自动通过，请核对诊断'}
    snapshot = json.loads(row['snapshot_json'])
    if (snapshot.get('brief') or {}).get('uncoveredSources'):
        return {**empty, 'status': 'incomplete', 'message': 'Brief 有未覆盖资料，暂不能进入品牌复核'}
    required = {r['id'] for r in snapshot['rules'] if r['importance'] == 'must'}
    checks = [f for f in result['report']['findings'] if f['rule_id'] in required]
    if not required or {f['rule_id'] for f in checks} != required:
        return {**empty, 'status': 'incomplete', 'message': '必须项未完整覆盖'}
    blocking = sum(f['verdict'] != 'compliant' for f in checks)
    return {'passed': blocking == 0, 'status': 'passed' if not blocking else 'needs_changes', 'checks': checks,
            'blockingCount': blocking, 'message': 'AI 必须项通过，等待品牌复核' if not blocking else f'{blocking} 项必须项未通过或待确认'}


def review_view(row):
    if not row:return None
    return {'id': row['id'], 'versionId': row['version_id'], 'contextKey': row['context_key'], 'status': row['status'],
            'snapshot': json.loads(row['snapshot_json']), 'result': json.loads(row['result_json'] or '{}'),
            'errorCode': row['error_code'], 'errorMessage': row['error_message'], 'acceptance': acceptance(row), 'createdAt': row['created_at'], 'updatedAt': row['updated_at']}


def context(db, task_id):
    task = authorize_task(db, task_id)
    standard = image_rules(db, task['project_id'])
    version = db.execute('SELECT * FROM image_post_versions WHERE task_id=? ORDER BY ordinal DESC LIMIT 1', (task_id,)).fetchone()
    brief = review_brief(db, task['project_id'], standard['source_version_id'] if standard else None)
    material = {'versionId': version['id'] if version else None, 'standardSetId': standard['id'] if standard else None,
                'brief': brief, 'platform': task['platform'], 'promptVersion': prompt('image-review')['version']}
    key = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return task, standard, version, brief, key


def state(db, task_id, summary=False):
    task, standard, version, brief, key = context(db, task_id)
    review = db.execute('SELECT * FROM image_post_reviews WHERE task_id=? AND context_key=? ORDER BY rowid DESC LIMIT 1', (task_id, key)).fetchone()
    decision = db.execute('SELECT * FROM image_post_decisions WHERE task_id=? AND context_key=? ORDER BY rowid DESC LIMIT 1', (task_id, key)).fetchone()
    decision = dict(decision) if decision and review and decision['review_id'] == review['id'] else None
    ready = bool(standard and any(r['importance'] == 'must' and r.get('enabled', True) for r in standard['rules']) and task['platform'] in json.loads(task['project_platforms']))
    accepted = acceptance(review)
    writable = task['archived_at'] is None and task['project_status'] != 'archived'
    base = {'taskId': task_id, 'projectId': task['project_id'], 'creatorName': task['creator_name'], 'contextKey': key,
            'rulesReady': ready, 'writable': writable, 'acceptance': accepted, 'decision': decision,
            'brandReviewAllowed': bool(ready and accepted['passed'] and writable),
            'brandApproved': bool(ready and accepted['passed'] and decision and decision['decision'] == 'approved'),
            'reviewStatus':review['status'] if review else None,
            'reviewCreatedAt':review['created_at'] if review else None,
            'currentVersion': version_view(version)}
    if summary:return base
    return {**base, 'standardSetId': standard['id'] if standard else None, 'rules': standard['rules'] if standard else [],
            'briefTitle': brief['title'] if brief else '品牌自定义图文要求', 'review': review_view(review),
            'versions': [version_view(v) for v in db.execute('SELECT * FROM image_post_versions WHERE task_id=? ORDER BY ordinal DESC LIMIT 50', (task_id,))],
            'reviews': [review_view(r) for r in db.execute('SELECT * FROM image_post_reviews WHERE task_id=? ORDER BY rowid DESC LIMIT 50', (task_id,))],
            'decisions': [dict(d) for d in db.execute('SELECT * FROM image_post_decisions WHERE task_id=? ORDER BY rowid DESC LIMIT 100', (task_id,))]}


def save_version(settings, task_id, payload, key):
    scope = 'image-post-version:' + task_id; fp = fingerprint(payload)
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE'); task = authorize_task(db, task_id)
        prior = replay(db, scope, key, fp)
        if prior:return prior
        require_task_writable(task)
        latest = db.execute('SELECT id FROM image_post_versions WHERE task_id=? ORDER BY ordinal DESC LIMIT 1', (task_id,)).fetchone()
        if payload.base_version_id != (latest['id'] if latest else None):
            raise ApiError(409, 'IMAGE_VERSION_CHANGED', '已有新的图文版本，请刷新后再提交，避免覆盖他人的修改')
        if len({i.asset_id for i in payload.images}) != len(payload.images):
            raise ApiError(400, 'DUPLICATE_IMAGE', '同一图文不能重复引用同一张图片')
        images = []; encoded_size = 0
        for number, item in enumerate(payload.images, 1):
            row = db.execute('SELECT * FROM image_post_assets WHERE id=? AND task_id=?', (item.asset_id, task_id)).fetchone()
            if not row:raise ApiError(404, 'IMAGE_ASSET_NOT_FOUND', '图片不属于当前合作任务')
            data = asset_bytes(settings, row)
            encoded_size += 4 * ((len(data) + 2) // 3)
            images.append({'id': row['id'], 'name': row['original_name'], 'sha256': row['sha256'], 'width': row['width'],
                           'height': row['height'], 'byteSize': row['byte_size'], 'ordinal': number, 'caption': item.caption})
        if encoded_size > MAX_ENCODED_BYTES:raise ApiError(413, 'IMAGE_INPUT_LIMIT', '全部图片编码后合计须在20MiB以内，请精简图片')
        identity = uuid4().hex; stamp = now()
        ordinal = db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM image_post_versions WHERE task_id=?', (task_id,)).fetchone()[0]
        db.execute('INSERT INTO image_post_versions VALUES (?,?,?,?,?,?,?)', (identity, task_id, ordinal, payload.title, payload.body, json.dumps(images, ensure_ascii=False), stamp))
        db.execute('UPDATE creator_tasks SET updated_at=?,revision=revision+1 WHERE id=?', (stamp, task_id))
        _, _, _, _, context_key = context(db, task_id)
        value = {'version': version_view(db.execute('SELECT * FROM image_post_versions WHERE id=?', (identity,)).fetchone()), 'contextKey': context_key}
        record(db, scope, key, fp, value)
        audit(db, task_id, 'image_post_submitted', {'versionId': identity})
        return value


def audit(db, task_id, action, detail):
    db.execute('INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)', (uuid4().hex, 'local-reviewer', 'creator_task', task_id, action, 'demo', json.dumps(detail), now()))


def analyze(settings, task_id, payload, key):
    if not payload.allow_real_call:raise ApiError(400, 'REAL_CALL_CONFIRMATION_REQUIRED', '须明确确认本次图文真实模型调用')
    scope = 'image-post-review:' + task_id; fp = fingerprint(payload)
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE'); task = authorize_task(db, task_id)
        prior = replay(db, scope, key, fp)
        if prior:return review_view(db.execute('SELECT * FROM image_post_reviews WHERE id=?', (prior['id'],)).fetchone())
        require_task_writable(task)
        task, standard, version, brief, context_key = context(db, task_id)
        if not standard or not any(r['importance'] == 'must' and r.get('enabled', True) for r in standard['rules']):
            raise ApiError(409, 'IMAGE_STANDARDS_REQUIRED', '品牌尚未保存图文审核规则，请先配置图文规则')
        if task['platform'] not in json.loads(task['project_platforms']):raise ApiError(409, 'PLATFORM_NOT_ALLOWED', '任务平台已不属于当前项目')
        if not version or version['id'] != payload.version_id or payload.context_key != context_key:
            raise ApiError(409, 'IMAGE_INPUT_CHANGED', '图文、规则或Brief已更新，请刷新后再验收')
        if db.execute("SELECT 1 FROM image_post_reviews WHERE task_id=? AND status='processing'", (task_id,)).fetchone():
            raise ApiError(409, 'IMAGE_REVIEW_BUSY', '此任务已有图文验收正在进行，请等待完成')
        config = db.execute('SELECT * FROM evidence_model_settings WHERE id=1').fetchone()
        if not config or not config['encrypted_key']:raise ApiError(409, 'MODEL_KEY_MISSING', '品牌尚未配置模型 Key')
        if config['revision'] != payload.config_revision:raise ApiError(409, 'REVISION_CONFLICT', '模型配置已更新，请重新确认')
        images = json.loads(version['images_json']); inputs = []
        for number, im in enumerate(images, 1):
            data = asset_bytes(settings, im)
            im['ref'] = f'i{number}'
            inputs.append({'id': im['id'], 'ref': im['ref'], 'dataUrl': 'data:image/png;base64,' + base64.b64encode(data).decode()})
        text = f"标题：{version['title']}\n正文：\n{version['body']}"
        for im in images:
            if im['caption']:text += f"\n第{im['ordinal']}张配文：{im['caption']}"
        # 停用规则与脚本口径一致：不送模型、不参与验收。
        snapshot = {'contentKind': 'image_post', 'version': version_view(version), 'brief': brief, 'rules': [r for r in standard['rules'] if r.get('enabled', True)],
                    'standardSetId': standard['id'], 'paragraphMode': False, 'script': text,
                    'scriptBlocks': [{'text': line, 'location': f'图文第{i}行'} for i, line in enumerate(text.splitlines(), 1)],
                    'quoteSources': [{'id': f'q{i}', 'text': line} for i, line in enumerate(text.splitlines(), 1) if line.strip()],
                    'multimodal': {'images': [{**im, 'locations': [f"第{im['ordinal']}张" + (' · 封面' if im['ordinal'] == 1 else '')]} for im in images], 'uncovered': [], 'incomplete': False},
                    'parseMeta': {'coverage': 'full'}, 'prompt': prompt('image-review'), 'model': config['model'], 'endpoint': config['endpoint'],
                    'configRevision': config['revision'], 'providerRequestOptions': request_options(config['model'])}
        identity = uuid4().hex; stamp = now()
        db.execute('INSERT INTO image_post_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)', (identity, task_id, version['id'], context_key, json.dumps(snapshot, ensure_ascii=False), 'processing', None, None, None, stamp, stamp))
        record(db, scope, key, fp, {'id': identity}); config = dict(config)
    from .router import call_script_model, validate_script_report, retain_partial_report
    result = {}; retained = {}; status = 'failed'; code = None; error = None; secret = ''
    def persist(value):
        retained.update(value)
        with connection(settings.database_path) as db:
            db.execute('UPDATE image_post_reviews SET result_json=?,updated_at=? WHERE id=?', (json.dumps(value, ensure_ascii=False), now(), identity))
    try:
        secret = cipher(settings.data_dir).decrypt(config['encrypted_key'].encode()).decode()
        result = call_script_model(config, secret, {**snapshot, '_imageInputs': inputs, '_persistOutput': persist, '_mediaLabel': '下一张为待发布图文成品图片，ref='})
        if result.get('providerError'):
            code = result['providerError']['code']; error = result['providerError']['message']
        else:
            if result.get('outputError'):
                failure = result['outputError']
                candidate = repair_output(result.get('modelResponse', '')) if failure['code'] == 'OUTPUT_INVALID_JSON' else None
                if candidate:
                    try:repaired = validate_script_report(candidate[0], snapshot)
                    except (ValueError, ValidationError):pass
                    else:
                        result['report'] = repaired
                        result['recovery'] = {'repaired': True, 'requiresManualReview': True, 'status': 'NEEDS_MANUAL_REVIEW', 'repairedOutput': candidate[1], 'providerResubmitted': False}
                raise OutputError(failure['code'], failure['message'], **failure.get('details', {}))
            result['report'] = validate_script_report(result.get('report'), snapshot)
            status = 'succeeded'
    except OutputError as exc:
        retain_partial_report(result, snapshot)
        if not result.get('recovery') and 'report' in result:result['parsedOutput'] = result.pop('report')
        result['diagnostics'] = exc.details; code = exc.code; error = str(exc) + '；原始输出已保留，未自动重试'
    except ValidationError as exc:
        retain_partial_report(result, snapshot)
        if 'report' in result:result['parsedOutput'] = result.pop('report')
        result['diagnostics'] = {'errorType': 'ValidationError', 'schemaErrors': [{'path': '.'.join(map(str, e['loc'])), 'constraint': e['type'], 'message': e['msg']} for e in exc.errors(include_input=False, include_url=False)]}
        code = 'OUTPUT_SCHEMA_INVALID'; error = '图文报告字段不符合约定；原始输出已保留，未自动重试'
    except Exception as exc:
        result = {**retained, **result}; result.pop('report', None)
        result['diagnostics'] = {'errorType': type(exc).__name__}
        code = 'INTERNAL_REVIEW_ERROR'; error = '图文验收未完成；已保留收到的输出，不自动重试，请查看诊断'
    finally:secret = ''
    with connection(settings.database_path) as db:
        from app.core.timing import stamp_duration
        stamp_duration(db, 'image_post_reviews', identity, result)
        db.execute('UPDATE image_post_reviews SET status=?,result_json=?,error_code=?,error_message=?,updated_at=? WHERE id=?', (status, json.dumps(result, ensure_ascii=False), code, error, now(), identity))
        return review_view(db.execute('SELECT * FROM image_post_reviews WHERE id=?', (identity,)).fetchone())


def decide(settings, task_id, payload, key):
    scope = 'image-post-decision:' + task_id; fp = fingerprint(payload)
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE'); task = authorize_task(db, task_id)
        prior = replay(db, scope, key, fp)
        if prior:return prior
        require_task_writable(task); current = state(db, task_id)
        review = current['review']
        if not review or review['id'] != payload.review_id or current['contextKey'] != payload.context_key:
            raise ApiError(409, 'IMAGE_REVIEW_CHANGED', '图文、规则或审核结果已更新，请重新核对当前版本')
        if not payload.reviewed_material or not payload.note.strip():raise ApiError(400, 'MATERIAL_REVIEW_REQUIRED', '请核对完整图文并填写品牌意见')
        if payload.decision == 'approved' and not current['brandReviewAllowed']:
            raise ApiError(409, 'IMAGE_ACCEPTANCE_REQUIRED', 'AI 必须项未通过或报告不完整，不能批准图文')
        identity = uuid4().hex; stamp = now()
        db.execute('INSERT INTO image_post_decisions VALUES (?,?,?,?,?,?,?)', (identity, task_id, review['id'], current['contextKey'], payload.decision, payload.note.strip(), stamp))
        value = {'id': identity, 'decision': payload.decision, 'reviewId': review['id']}
        record(db, scope, key, fp, value); audit(db, task_id, 'image_post_' + payload.decision, value)
        return value


def queue_item(db, task):
    view = state(db, task['id'])
    if view['brandReviewAllowed'] and not view['decision']:
        return {**dict(task), 'scope': 'image', 'submittedAt': view['review']['createdAt'], 'summary': view['review']['result']['report']['summary']}


@router.get('/tasks/{task_id}/image-posts', response_model=Envelope[dict])
def get_posts(task_id: str, request: Request):
    local_only(request)
    with connection(request.app.state.settings.database_path) as db:value = state(db, task_id)
    return Envelope(data=value, request_id=request.state.request_id)


@router.post('/tasks/{task_id}/image-posts', response_model=Envelope[dict], status_code=201)
def create_post(task_id: str, payload: PostInput, request: Request, idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request)
    return Envelope(data=save_version(request.app.state.settings, task_id, payload, idempotency_key), request_id=request.state.request_id)


@router.post('/tasks/{task_id}/image-posts/reviews', response_model=Envelope[dict], status_code=201)
def create_review(task_id: str, payload: ImageAnalysisInput, request: Request, idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request)
    return Envelope(data=analyze(request.app.state.settings, task_id, payload, idempotency_key), request_id=request.state.request_id)


@router.post('/tasks/{task_id}/image-posts/decisions', response_model=Envelope[dict])
def create_decision(task_id: str, payload: ImageDecisionInput, request: Request, idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request)
    return Envelope(data=decide(request.app.state.settings, task_id, payload, idempotency_key), request_id=request.state.request_id)


@router.post('/tasks/{task_id}/image-assets', response_model=Envelope[dict], status_code=201)
async def upload_image(task_id: str, request: Request, file: UploadFile = File(...), idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=8, max_length=128)):
    local_only(request); settings = request.app.state.settings
    with connection(settings.database_path) as db:require_task_writable(authorize_task(db, task_id))
    try:
        data = await file.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:raise ApiError(413, 'IMAGE_UPLOAD_LIMIT', '单张图片须在4MiB以内')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ('PNG', 'JPEG', 'WEBP') or source.width * source.height > 12_000_000 or getattr(source, 'n_frames', 1) != 1:raise ValueError()
                image = ImageOps.exif_transpose(source).convert('RGBA')
                image = Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image).convert('RGB')
                output = io.BytesIO(); image.save(output, format='PNG'); normalized = output.getvalue()
                width, height = image.size
        if len(normalized) > MAX_IMAGE_BYTES:raise ApiError(413, 'IMAGE_UPLOAD_LIMIT', '图片解码后超过4MiB，请使用较小图片')
    except ApiError:raise
    except Exception:raise ApiError(415, 'IMAGE_INVALID', '请选择可读取的静态 PNG、JPEG 或 WebP，最多1200万像素') from None
    finally:await file.close()
    name = sanitize_original_name(file.filename or '图文图片')
    source_hash = hashlib.sha256(data).hexdigest(); digest = hashlib.sha256(normalized).hexdigest()
    fp = hashlib.sha256((source_hash + name).encode()).hexdigest(); scope = 'image-post-asset:' + task_id
    paths = []
    try:
        with connection(settings.database_path) as db:
            db.execute('BEGIN IMMEDIATE'); require_task_writable(authorize_task(db, task_id))
            prior = replay(db, scope, idempotency_key, fp)
            if prior:return Envelope(data=prior, request_id=request.state.request_id)
            if db.execute('SELECT COUNT(*) FROM image_post_assets WHERE task_id=?', (task_id,)).fetchone()[0] >= 300:
                raise ApiError(409, 'IMAGE_UPLOAD_LIMIT', '此合作任务图片已达300张，请另建合作任务')
            identity = uuid4().hex; stamp = now(); root = asset_root(settings)
            for suffix, content in (('.source', data), ('.png', normalized)):
                path = root / (identity + suffix); paths.append(path); path.write_bytes(content)
            db.execute('INSERT INTO image_post_assets VALUES (?,?,?,?,?,?,?,?,?)', (identity, task_id, name, source_hash, digest, width, height, len(normalized), stamp))
            value = {'id': identity, 'name': name, 'width': width, 'height': height, 'byteSize': len(normalized), 'sha256': digest}
            record(db, scope, idempotency_key, fp, value)
    except BaseException:
        for path in paths:path.unlink(missing_ok=True)
        raise
    return Envelope(data=value, request_id=request.state.request_id)


@router.get('/tasks/{task_id}/image-assets/{asset_id}/{kind}')
def image_content(task_id: str, asset_id: str, kind: Literal['content', 'original'], request: Request):
    local_only(request); settings = request.app.state.settings
    with connection(settings.database_path) as db:
        authorize_task(db, task_id)
        asset = db.execute('SELECT * FROM image_post_assets WHERE id=? AND task_id=?', (asset_id, task_id)).fetchone()
        if not asset:raise ApiError(404, 'IMAGE_ASSET_NOT_FOUND', '图片不属于当前任务')
        data = asset_bytes(settings, asset, original=kind == 'original')
    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
    if kind == 'original':headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(asset['original_name'])
    return Response(data, media_type='application/octet-stream' if kind == 'original' else 'image/png', headers=headers)
