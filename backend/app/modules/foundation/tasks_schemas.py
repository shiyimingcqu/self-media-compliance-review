from typing import Literal

from pydantic import Field, field_validator

from app.core.contracts import ContractModel, Platform, RunMode, TaskStage

CampaignPhase = Literal["warmup", "seeding", "general"]


class TaskCreate(ContractModel):
    creator_name: str = Field(min_length=1, max_length=50)
    platform: Platform
    content_type: Literal["video"] = "video"
    campaign_phase: CampaignPhase
    rule_version_id: str
    acknowledge_duplicate: bool = False  # 细化已有"确认后继续"需求；不重写公共规格

    @field_validator("creator_name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskPatch(ContractModel):
    creator_name: str | None = Field(default=None, min_length=1, max_length=50)
    campaign_phase: CampaignPhase | None = None

    @field_validator("creator_name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class TaskRebind(ContractModel):
    rule_version_id: str
    reason: str = Field(min_length=1, max_length=500)
    acknowledge_invalidation: bool


class EligibilityView(ContractModel):
    allowed: bool
    code: str = ""
    message: str = ""


class CreatorTask(ContractModel):
    id: str
    project_id: str
    creator_name: str
    platform: Platform
    content_type: str
    campaign_phase: CampaignPhase
    rule_version_id: str | None
    stage: TaskStage
    revision: int
    archived_at: str | None = None
    mode: RunMode
    created_at: str
    updated_at: str
    eligibility: EligibilityView | None = None


class InvalidationEvent(ContractModel):
    id: str
    task_id: str
    kind: str
    reason: str
    mode: RunMode
    created_at: str
