from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.core.contracts import ContractModel, Platform, ProjectStatus, RunMode

ProductName = Annotated[str, Field(min_length=1, max_length=200, strict=True)]


class Actor(ContractModel):
    id: str = "local-reviewer"
    name: str = "本地审核人"


class Context(ContractModel):
    actor: Actor = Field(default_factory=Actor)
    timezone: str
    mode: RunMode = RunMode.DEMO
    implementation_stage: str = "foundation"
    providers: list[str] = Field(default_factory=list)


class Brand(ContractModel):
    id: str
    name: str


class ProjectCreate(ContractModel):
    name: str = Field(min_length=1, max_length=50)
    brand_name: str | None = Field(default=None, min_length=1, max_length=50)
    brand_id: str | None = None
    description: str = Field(default="", max_length=200)
    platforms: list[Platform] = Field(min_length=1, max_length=3)
    product_names: list[ProductName] = Field(default_factory=list, max_length=30)

    @field_validator("name", "brand_name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_brand(self):
        if bool(self.brand_id) == bool(self.brand_name):
            raise ValueError("brandId 和 brandName 必须且只能填写一个")
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("投放平台不能重复")
        return self


class Project(ContractModel):
    id: str
    name: str
    brand: Brand
    description: str
    platforms: list[Platform]
    product_names: list[str] = Field(default_factory=list)
    mode: RunMode
    status: ProjectStatus
    revision: int
    created_at: str
    updated_at: str
    task_count: int = 0
    completed_task_count: int = 0


class ProjectPatch(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    brand_name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    platforms: list[Platform] | None = Field(default=None, min_length=1, max_length=3)
    product_names: list[ProductName] | None = Field(default=None, max_length=30)

    @field_validator("name", "brand_name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("至少提交一个有效字段，不能用 null 清除项目字段")
        if self.platforms and len(set(self.platforms)) != len(self.platforms):
            raise ValueError("投放平台不能重复")
        return self


class RuleSourceRef(ContractModel):
    """规则版本真实源引用（t-30）：前端据 fileId 调 /files/:id/preview；异常指针=null 不借他 Brief/项目。"""

    id: str
    file_id: str
    extraction_status: str


class RuleVersionSummary(ContractModel):
    """规则版本真实状态全量透出（不预过滤历史）；任务兼容选择器须再读 rule detail，
    本投影不冒充 eligibility。source=该版本实际绑定源（LEFT JOIN，无/异常=null）。"""

    id: str
    version_label: str
    state: str
    revision: int
    source: RuleSourceRef | None = None


class BriefSummary(ContractModel):
    id: str
    title: str
    platforms: list[Platform]  # 已存范围；旧数据 [] = 未设置，明确原样返回不静默裁剪
    latest_source_version_id: str | None
    file_id: str | None  # 来自 latest source；该源必须属于本 brief，否则两字段皆 None
    extraction_status: str | None
    versions: list[RuleVersionSummary]


class ProjectDetail(Project):
    """独立详情模型：briefs 仅详情端点提供，列表 Project 不带该字段（不误读为空）。
    归档项目可读（读路径不拒）；跨模式沿用既有 404。"""

    briefs: list[BriefSummary]


class Dashboard(ContractModel):
    active_projects: int
    pending_audit_tasks: int | None = None
    pending_human_tasks: int | None = None
    task_statistics_available: bool = False
    historical_comparison_available: bool = False


class ModuleAvailability(ContractModel):
    id: str
    owner: str
    state: Literal["foundation", "not_implemented"]
