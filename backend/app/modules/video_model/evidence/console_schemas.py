from typing import Literal
from urllib.parse import urlsplit
import ipaddress

from pydantic import Field, field_validator, model_validator

from app.core.contracts import ContractModel

ENDPOINTS = {
    "https://maas.qianwenaiapi.com/compatible-mode/v1": "用户提供的兼容服务",
    "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1": "用户提供的兼容服务",
    "https://dashscope.aliyuncs.com/compatible-mode/v1": "北京",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1": "新加坡",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1": "美国",
}


class ModelSettingsInput(ContractModel):
    endpoint: str
    model: str = Field(default="qwen3.8-omni-flash", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    max_inline_bytes: int | None = Field(default=None, ge=1, deprecated=True, description='仅兼容旧客户端；平台不再按此字段限制视频发送大小')
    api_key: str | None = Field(default=None, min_length=8, max_length=512, repr=False)
    clear_key: bool = False
    revision: int = Field(ge=0)

    @field_validator("endpoint")
    @classmethod
    def allowed_endpoint(cls, value):
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(c.isspace() for c in normalized)):
            raise ValueError("请填写 HTTPS 接口地址，不包含凭证、查询参数或片段")
        host = parsed.hostname.lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local')):
            raise ValueError("请填写外部模型服务地址")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("不支持内网 IP 地址")
        try:
            parsed.port
        except ValueError:
            raise ValueError("接口端口无效") from None
        return normalized

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None and (value != value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError("Key 不能包含空白或非ASCII字符")
        return value

    @model_validator(mode="after")
    def key_action(self):
        if self.clear_key and self.api_key is not None:
            raise ValueError("清除和替换 Key 不能同时操作")
        return self


class ModelSettingsView(ContractModel):
    endpoint: str
    model: str
    has_key: bool
    revision: int
    updated_at: str | None = None
    max_inline_bytes: int | None = None
    max_model_requests: int | None = None
    audio_video_coverage_verified: bool = False


class EvidenceRunInput(ContractModel):
    kind: Literal["connection", "video"]
    config_revision: int = Field(ge=1)
    allow_real_call: bool = False
    file_id: str | None = None
    task_id: str | None = None
    compression_preset: Literal['balanced', 'compact', 'original'] = 'balanced'
    test_rule: str = Field(default="观察视频中出现的主体、可见文字和可听口播；不确定时标待确认，不捏造。", min_length=1, max_length=1000)


class VideoPreparationInput(ContractModel):
    file_id: str
    preset: Literal['balanced', 'compact', 'original'] = 'balanced'


class VideoPreparationView(ContractModel):
    file_id: str
    preset: str
    original_bytes: int
    prepared_bytes: int
    original: dict
    prepared: dict
    source_sha256: str
    prepared_sha256: str
    can_send: bool
    timeline_preserved: bool
    frame_rate_policy: str
    evidence_from_original: bool
    max_inline_bytes: int | None


class EvidenceRunView(ContractModel):
    id: str
    kind: str
    task_id: str | None = None
    file_id: str | None = None
    status: str
    config_revision: int
    snapshot: dict
    result: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    billed_mode: Literal["real"] = "real"
    affects_workflow: bool = False
