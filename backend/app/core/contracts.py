"""公共单一事实源；改动需公共基础负责人评审并重新生成 OpenAPI。"""

from enum import Enum
from typing import Any, Generic, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", protected_namespaces=())


class ProviderExecution(ContractModel):
    """t-33 冻结执行上下文：入队前由 builder 从配置复制，本身不可变（frozen），
    执行中不再读环境；含凭据/查询/片段的 endpoint 一律拒绝（防密钥混入 URL 或快照）。
    错误文案固定安全，不回显 URL 内容。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid",
                              protected_namespaces=(), frozen=True, hide_input_in_errors=True)

    model_id: str
    endpoint_base: str
    config_version: str

    @field_validator("model_id", "config_version")
    @classmethod
    def _trimmed_nonempty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("provider 执行配置字段不得为空白")
        return trimmed

    @field_validator("endpoint_base")
    @classmethod
    def _https_safe_endpoint(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed or any(ord(ch) < 32 or ord(ch) == 127 for ch in trimmed):
            raise ValueError("provider endpoint 非法")  # 固定文案：不回显输入
        try:
            parts = urlsplit(trimmed)
            port = parts.port  # 非法端口在此抛 ValueError（urlsplit 本身惰性不解析）
            host = (parts.hostname or "").strip()
        except ValueError:
            raise ValueError("provider endpoint 非法") from None
        if (parts.scheme.lower() != "https" or not host or any(ch.isspace() for ch in host)
                or "." not in host or not all(ch.isalnum() or ch in ".-" for ch in host)
                or host.startswith((".", "-")) or host.endswith((".", "-"))
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or port is not None and (port < 1 or port > 65535)):
            raise ValueError("provider endpoint 必须为不含凭据/查询/片段/非法端口的 HTTPS 地址") from None
        return trimmed


class Platform(str, Enum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BILIBILI = "bilibili"


class RunMode(str, Enum):
    DEMO = "demo"
    REAL = "real"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Verdict(str, Enum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NOT_APPLICABLE = "not_applicable"


class Conclusion(str, Enum):
    PASSED = "passed"
    CHANGES_REQUESTED = "changes_requested"
    NEEDS_CONFIRMATION = "needs_confirmation"
    INCOMPLETE = "incomplete"


class TaskStage(str, Enum):
    REQUIREMENTS = "requirements"
    SCRIPT = "script"
    VIDEO = "video"
    HUMAN = "human"
    COMPLETED = "completed"


class ReviewState(str, Enum):
    PENDING = "pending"
    DRAFT = "draft"
    APPROVED = "approved"
    RETURNED = "returned"
    SUPERSEDED = "superseded"


class ReviewScope(str, Enum):
    SCRIPT = "script"
    VIDEO = "video"


class ReviewSnapshot(ContractModel):
    mode: RunMode
    rule_version_id: str
    script_version_id: str | None = None
    video_version_id: str | None = None
    model_config_id: str
    model_config_revision: int = Field(ge=1)
    capability_report_id: str | None = None
    # t-33 新增可选执行上下文（额外字段，不替换/不改动上述现有语义）。
    # 历史 demo/real 快照缺此字段照常读为 None，不伪造回填；新的 real 准入验证另批。
    execution: ProviderExecution | None = None


T = TypeVar("T")


class Envelope(ContractModel, Generic[T]):
    data: T
    request_id: str


class PageEnvelope(Envelope[list[T]], Generic[T]):
    page: int
    page_size: int
    total: int


class ErrorDetail(ContractModel):
    code: str
    message: str
    field_errors: dict[str, str] | None = None
    details: dict[str, Any] | None = None
    retryable: bool = False


class ErrorEnvelope(ContractModel):
    error: ErrorDetail
    request_id: str
