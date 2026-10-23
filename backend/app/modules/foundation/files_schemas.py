from typing import Literal

from pydantic import Field

from app.core.contracts import ContractModel, RunMode

FilePurpose = Literal["brief", "script", "video"]


class FileAsset(ContractModel):
    """对外形状；storageKey 仅服务端可见，不在此模型中。"""

    id: str
    original_name: str
    extension: str
    mime: str
    byte_size: int
    sha256: str
    purpose: FilePurpose
    signature_state: Literal["verified", "unverifiable"]
    media_check: Literal["unverified"]
    mode: RunMode
    created_at: str


class PreviewBlock(ContractModel):
    paragraph_id: str
    ordinal: int
    start_offset: int
    end_offset: int
    text: str
    page_no: int | None = None  # t-40：PDF 真实物理页号；TXT 为 None（不伪造）


class PreviewResult(ContractModel):
    source_version_id: str
    file_id: str
    extraction_status: Literal["ready"]
    blocks: list[PreviewBlock]
    # t-40：从**该 source 的持久行**读出的分页元数据，刷新后不靠 blocks 反推。
    # TXT/旧源为 None/空（不伪造页数，也不谎称已全部覆盖）。
    page_count: int | None = None
    uncovered_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
