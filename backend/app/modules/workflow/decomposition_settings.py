"""Independent language-model configuration for Brief rule decomposition."""
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field, field_validator, model_validator

from app.core.contracts import ContractModel, Envelope
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.foundation.service import now
from app.modules.video_model.evidence.console_router import local_only
from app.modules.video_model.evidence.console_schemas import ModelSettingsInput
from app.modules.video_model.evidence.console_service import DEFAULT_ENDPOINT
from app.modules.video_model.evidence.secrets import cipher

router = APIRouter(tags=['decomposition-model'])


class DecompositionSettingsInput(ContractModel):
    endpoint: str
    model: str = Field(default='qwen3.8-flash', min_length=1, max_length=200, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*$')
    api_key: str | None = Field(default=None, min_length=8, max_length=512, repr=False)
    clear_key: bool = False
    revision: int = Field(ge=0)
    reasoning_effort: Literal['none', 'low', 'medium', 'xhigh'] = 'xhigh'
    temperature: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)

    @field_validator('endpoint')
    @classmethod
    def endpoint_valid(cls, value):return ModelSettingsInput.allowed_endpoint(value)

    @field_validator('api_key')
    @classmethod
    def key_valid(cls, value):return ModelSettingsInput.valid_key(value)

    @model_validator(mode='after')
    def key_action(self):
        if self.clear_key and self.api_key is not None:raise ValueError('清除和替换 Key 不能同时操作')
        return self


class DecompositionSettingsView(ContractModel):
    endpoint: str
    model: str
    has_key: bool
    revision: int
    updated_at: str | None = None
    reasoning_effort: Literal['none', 'low', 'medium', 'xhigh'] = 'xhigh'
    temperature: float = 0.1


class CopyReviewCredentialsInput(ContractModel):
    revision: int = Field(ge=0)


def settings_view(settings):
    with connection(settings.database_path) as db:
        row = db.execute('SELECT * FROM decomposition_model_settings WHERE id=1').fetchone()
    return DecompositionSettingsView(endpoint=row['endpoint'] if row else DEFAULT_ENDPOINT,
        model=row['model'] if row else 'qwen3.8-flash', has_key=bool(row and row['encrypted_key']),
        revision=row['revision'] if row else 0, updated_at=row['updated_at'] if row else None,
        reasoning_effort=row['reasoning_effort'] if row else 'xhigh', temperature=row['temperature'] if row else 0.1)


def save_settings(settings, payload):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM decomposition_model_settings WHERE id=1').fetchone()
        if payload.revision != (row['revision'] if row else 0):raise ApiError(409, 'REVISION_CONFLICT', '解构模型配置已更新，请刷新后重试')
        encrypted = row['encrypted_key'] if row else None
        if payload.clear_key:encrypted = None
        elif payload.api_key is not None:encrypted = cipher(settings.data_dir).encrypt(payload.api_key.encode()).decode()
        db.execute('INSERT INTO decomposition_model_settings VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                   'endpoint=excluded.endpoint,model=excluded.model,encrypted_key=excluded.encrypted_key,revision=excluded.revision,'
                   'updated_at=excluded.updated_at,reasoning_effort=excluded.reasoning_effort,temperature=excluded.temperature',
                   (payload.endpoint, payload.model, encrypted, payload.revision + 1, now(), payload.reasoning_effort, payload.temperature))
    return settings_view(settings)


def copy_review_credentials(settings, revision):
    with connection(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM decomposition_model_settings WHERE id=1').fetchone()
        if revision != (row['revision'] if row else 0):raise ApiError(409, 'REVISION_CONFLICT', '解构模型配置已更新，请刷新')
        review = db.execute('SELECT * FROM evidence_model_settings WHERE id=1').fetchone()
        if not review or not review['encrypted_key']:raise ApiError(409, 'MODEL_KEY_MISSING', '审核模型尚未保存可沿用的 Key')
        db.execute('INSERT INTO decomposition_model_settings VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                   'endpoint=excluded.endpoint,encrypted_key=excluded.encrypted_key,revision=excluded.revision,updated_at=excluded.updated_at',
                   (review['endpoint'], row['model'] if row else 'qwen3.8-flash', review['encrypted_key'], revision + 1, now(),
                    row['reasoning_effort'] if row else 'xhigh', row['temperature'] if row else 0.1))
    return settings_view(settings)


@router.get('/settings/models/decomposition', response_model=Envelope[DecompositionSettingsView])
def get_settings(request: Request):
    local_only(request)
    return Envelope(data=settings_view(request.app.state.settings), request_id=request.state.request_id)


@router.put('/settings/models/decomposition', response_model=Envelope[DecompositionSettingsView])
def put_settings(payload: DecompositionSettingsInput, request: Request):
    local_only(request)
    return Envelope(data=save_settings(request.app.state.settings, payload), request_id=request.state.request_id)


@router.post('/settings/models/decomposition/copy-review-credentials', response_model=Envelope[DecompositionSettingsView])
def copy_settings(payload: CopyReviewCredentialsInput, request: Request):
    local_only(request)
    return Envelope(data=copy_review_credentials(request.app.state.settings, payload.revision), request_id=request.state.request_id)
