import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# allow_real_provider_calls 白名单：仅此列值（大小写不敏感）解析为 True；其余一律 False（fail-closed）。
_REAL_FLAG_TRUE = {"1", "true", "yes"}


def _parse_real_flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _REAL_FLAG_TRUE


def build_provider_execution(settings: "Settings") -> "object":
    """纯构建冻结执行上下文（t-33）：入队前把当前配置**复制**成不可变 ProviderExecution。
    - 不产生任何 provider 调用、不单独赋予真实资格（allow_real/能力/资格准入另批接线）；
    - 缺任一配置（model_id/endpoint/version 任一为空或非法）→ 明确失败，不凭环境变化回填历史 execution；
    - 返回对象本身 frozen：构建后再改 Settings/env 不影响已建 execution。"""
    from app.core.contracts import ProviderExecution  # 局部导入避免模块级循环
    missing = [name for name, val in (("provider_model_id", settings.provider_model_id),
                                      ("provider_endpoint_base", settings.provider_endpoint_base),
                                      ("provider_config_version", settings.provider_config_version))
               if not (val or "").strip()]
    if missing:
        raise ValueError(f"provider 执行配置缺失：{', '.join(missing)}")
    return ProviderExecution(model_id=settings.provider_model_id,
                             endpoint_base=settings.provider_endpoint_base,
                             config_version=settings.provider_config_version)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = BACKEND_ROOT / ".data"
    timezone: str = "Asia/Hong_Kong"
    # 文档保留20MiB上限；视频原片默认不设应用大小上限，供应商发送限制另行校验。
    max_document_bytes: int = 20 * 1024 * 1024
    max_video_bytes: int | None = None
    # t-33 provider 执行配置（总闸默认关；无任何密钥字段——凭据不在此、不入源码/日志/契约）。
    # allow_real_provider_calls 非全局 mode：须与 job/project.mode=='real' AND 且资格/能力另批验证。
    allow_real_provider_calls: bool = False
    provider_model_id: str = ""
    provider_endpoint_base: str = ""
    provider_config_version: str = ""

    @property
    def database_path(self) -> Path:
        return self.data_dir / "workbench.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> "Settings":
        path = Path(os.getenv("REVIEW_DATA_DIR", ".data"))
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return cls(data_dir=path.resolve(), timezone=os.getenv("REVIEW_TIMEZONE", "Asia/Hong_Kong"),
                   allow_real_provider_calls=_parse_real_flag(os.getenv("REVIEW_ALLOW_REAL_PROVIDER_CALLS")),
                   provider_model_id=os.getenv("REVIEW_PROVIDER_MODEL_ID", ""),
                   provider_endpoint_base=os.getenv("REVIEW_PROVIDER_ENDPOINT_BASE", ""),
                   provider_config_version=os.getenv("REVIEW_PROVIDER_CONFIG_VERSION", ""))
