"""B 独立入口（t-19 登记 + t-20 规则流修订，A 00:10 收口）。
无 AI 提取端点；scripts 仍 501。
写端点统一顺序：归属/当前模式授权 → 幂等回放 → 可写状态/If-Match revision。
启用语义（A 裁决 A）：enable 只改本版状态位，不触碰他版与任何任务绑定；同 Brief 多 enabled 并存。"""

import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from pydantic import Field, field_validator

from app.core.contracts import ContractModel, Envelope, Platform
from app.core.database import connection
from app.core.errors import ApiError
from app.modules.brief_script import rule_service, script_service, service_registration
from app.modules.brief_script.rule_service import now

router = APIRouter(tags=["B · brief-script"])

CATEGORIES = ("产品事实", "卖点", "画面", "口播", "字幕", "禁用表达", "发布材料", "其他")
CONTENT_TYPES = ("video", "image_text", "general")
PHASES = ("warmup", "seeding", "general")
REVIEW_STAGES = ("script", "video", "human")
SEMANTIC_FIELDS = ("text", "category", "importance", "platforms", "content_types", "phases", "review_stages",
                   "source", "exceptions")


# ---------- 请求模型 ----------


class BriefCreate(ContractModel):
    file_id: str
    title: str = Field(min_length=1, max_length=100)
    platforms: list[Platform] = Field(min_length=1, max_length=3)

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value):
        value = value.strip() if isinstance(value, str) else value
        if not value:
            raise ValueError("title 不能为空白")
        return value

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("平台不能重复")
        return value


class VersionCreate(ContractModel):
    source_version_id: str
    parent_id: str | None = None


class ScopeListValidatorMixin:
    @field_validator("content_types")
    @classmethod
    def _ct(cls, v):
        if not set(v) <= set(CONTENT_TYPES):
            raise ValueError("内容形式闭集 video/image_text/general")
        return v

    @field_validator("phases")
    @classmethod
    def _ph(cls, v):
        if not set(v) <= set(PHASES):
            raise ValueError("活动阶段闭集 warmup/seeding/general")
        return v

    @field_validator("review_stages")
    @classmethod
    def _rs(cls, v):
        if not set(v) <= set(REVIEW_STAGES):
            raise ValueError("检查阶段闭集 script/video/human")
        return v

    @field_validator("platforms")
    @classmethod
    def _uniq(cls, v):
        if v is not None and len(set(v)) != len(v):
            raise ValueError("平台不能重复")
        return v


class RequirementBody(ScopeListValidatorMixin, ContractModel):
    text: str = Field(min_length=1, max_length=500)
    category: str
    importance: Literal["must", "suggested"]
    platforms: list[Platform] = Field(min_length=1, max_length=3)
    content_types: list[str] = Field(min_length=1, max_length=3)
    phases: list[str] = Field(min_length=1, max_length=3)
    review_stages: list[str] = Field(min_length=1, max_length=3)
    exceptions: str = Field(default="", max_length=300)
    source: dict

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in CATEGORIES:
            raise ValueError("类别不在闭集")
        return v


class RequirementPatch(ScopeListValidatorMixin, ContractModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = None
    importance: Literal["must", "suggested"] | None = None
    platforms: list[Platform] | None = Field(default=None, min_length=1, max_length=3)
    content_types: list[str] | None = Field(default=None, min_length=1, max_length=3)
    phases: list[str] | None = Field(default=None, min_length=1, max_length=3)
    review_stages: list[str] | None = Field(default=None, min_length=1, max_length=3)
    exceptions: str | None = Field(default=None, max_length=300)
    source: dict | None = None

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v is not None and v not in CATEGORIES:
            raise ValueError("类别不在闭集")
        return v


class ConfirmBody(ContractModel):
    requirement_ids: list[str] = Field(min_length=1)


class DisableBody(ContractModel):
    requirement_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class ConflictCreate(ContractModel):
    requirement_ids: list[str] = Field(min_length=2)
    explanation: str = Field(min_length=1, max_length=500)


class AffectedChange(ContractModel):
    requirement_id: str = Field(min_length=1)
    action: Literal["disable", "update_text"]
    text: str | None = Field(default=None, max_length=500)

    @field_validator("text", mode="before")
    @classmethod
    def _need_text(cls, v):
        return v


class ConflictResolve(ContractModel):
    resolution: str = Field(min_length=1, max_length=500)
    affected_requirement_changes: list[AffectedChange] = Field(default_factory=list)

    @field_validator("affected_requirement_changes")
    @classmethod
    def _validate_changes(cls, value):
        for change in value:
            if change.action == "update_text" and not (change.text or "").strip():
                raise ValueError("update_text 必须提供新文本")
            if change.action not in ("disable", "update_text"):
                raise ValueError("action 闭集 disable/update_text")
        return value


class ItemMapping(ContractModel):
    uncovered_source_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    note: str = Field(min_length=1, max_length=500)


class CoverageBody(ContractModel):
    uncovered_source_ids: list[str] = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    item_mappings: list[ItemMapping] = Field(min_length=1,
                                             description="与 uncoveredSourceIds 一一对应的逐条补录映射（B_0006）")


class EnableBody(ContractModel):
    change_summary: str = Field(min_length=1, max_length=200)


class ScriptCreate(ContractModel):
    input_type: Literal["file", "text"]
    file_id: str | None = None
    text: str | None = None


class SourceBlock(ContractModel):
    paragraph_id: str
    ordinal: int
    start_offset: int = 0   # TXT/粘贴：字符偏移；XLSX/DOCX 无字符偏移用 0 且以 location 定位
    end_offset: int = 0
    text: str
    location: str | None = None  # t-38 真实格式结构化定位（如 视频脚本!A9 / paragraph:12 / table:0:r2）


class ParseMeta(ContractModel):
    """t-38 真实格式解析元数据（持久化）：告警、未纳入文本的图片锚点、覆盖度三态。

    coverage: full=文本完整 / partial=有图片等未纳入内容 / unknown=历史行无元数据（**不补 false**）。
    下游 review/模型上下文必须读取本字段，不得把 partial/unknown 当作完整脚本。
    """

    warnings: list[str] = Field(default_factory=list)
    uncovered_media: list[str] = Field(default_factory=list)
    partial: bool = False
    coverage: str = "unknown"  # 默认 unknown：缺元数据时不谎称完整


class ScriptError(ContractModel):
    code: str
    message: str


class ScriptVersionCreated(ContractModel):
    script_version_id: str
    task_id: str
    ordinal: int
    version_label: str
    input_type: str
    file_id: str | None = None
    extraction_status: Literal["ready", "invalid"]
    source_map: list[SourceBlock] = Field(default_factory=list)
    char_count: int | None = None  # file 输入无持久原文=None（不伪 0）
    parse_meta: ParseMeta = Field(default_factory=ParseMeta)  # t-38
    created_at: str
    error: ScriptError | None = None


class ScriptVersionSummary(ContractModel):
    script_version_id: str
    ordinal: int
    version_label: str
    input_type: str
    file_id: str | None = None
    extraction_status: str
    char_count: int | None = None  # list 读 length(text)，file 行为 None，schema nullable 一致
    created_at: str
    is_current: bool


class ScriptVersionDetail(ContractModel):
    script_version_id: str
    task_id: str
    ordinal: int
    version_label: str
    input_type: str
    file_id: str | None = None
    text: str | None = None
    extraction_status: str
    source_map: list[SourceBlock] = Field(default_factory=list)
    parse_meta: ParseMeta = Field(default_factory=ParseMeta)  # t-38：来自持久列而非仅创建响应
    is_current: bool
    created_at: str


# ---------- 视图模型 ----------


class TextBlockView(ContractModel):
    paragraph_id: str
    ordinal: int
    start_offset: int
    end_offset: int
    text: str
    page_no: int | None = None  # t-40：PDF 真实物理页号（1 基）；TXT 无页概念为 null


class BriefError(ContractModel):
    code: str
    message: str


class BriefRegistrationResult(ContractModel):
    brief_id: str
    source_version_id: str
    file_id: str
    platforms: list[Platform]
    extraction_status: Literal["ready", "invalid"]
    encoding: str | None = None
    blocks: list[TextBlockView] = Field(default_factory=list)
    # t-40 PDF：真实页数与页号级未覆盖来源（图片页/扫描页）。TXT 为 None/空，不伪造。
    page_count: int | None = None
    uncovered_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: BriefError | None = None


class VersionView(ContractModel):
    id: str
    brief_id: str
    source_version_id: str
    version_label: str
    state: str
    parent_id: str | None = None
    revision: int
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    created_at: str


class RequirementView(ContractModel):
    id: str
    lineage_id: str
    rule_version_id: str | None = None
    text: str
    category: str
    importance: str
    platforms: list[str]
    content_types: list[str]
    phases: list[str]
    review_stages: list[str]
    exceptions: str
    source: dict
    confirmation: str


class SkipItem(ContractModel):
    id: str
    reason: str


class ConfirmResult(ContractModel):
    confirmed_ids: list[str]
    skipped: list[SkipItem]


class DisableResult(ContractModel):
    requirement_id: str
    confirmation: str


class ConflictView(ContractModel):
    id: str
    rule_version_id: str
    requirement_ids: list[str]
    explanation: str
    state: str
    resolution: str | None = None


class ResolveResult(ContractModel):
    conflict_id: str
    state: str
    resolution: str
    applied_changes: list[SkipItem]


class CoverageView(ContractModel):
    id: str
    rule_version_id: str
    uncovered_items: list[str]
    item_mappings: list[ItemMapping]


class CoverageRow(ContractModel):
    uncovered_items: list[str]
    statement: str
    reason: str
    item_mappings: list[ItemMapping]
    created_at: str


class VersionDetail(ContractModel):
    version: VersionView
    requirements: list[RequirementView]
    conflicts: list[ConflictView]
    coverage: list[CoverageRow]


# ---------- 辅助 ----------


def _load_brief(db, brief_id, mode="demo"):
    row = db.execute("SELECT b.*, p.mode AS project_mode FROM briefs b JOIN projects p ON p.id=b.project_id "
                     "WHERE b.id=?", (brief_id,)).fetchone()
    if row is None or row["project_mode"] != mode:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Brief 不存在")
    return row


def _load_source(db, source_version_id, brief_id):
    row = db.execute("SELECT * FROM brief_source_versions WHERE id=? AND brief_id=?",
                     (source_version_id, brief_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "源版本不属于该 Brief")
    return row


def _authorize(db, version_id: str):
    """第一步永远是归属+当前模式；失败 404，不泄露存在性，也不回放跨模式响应。"""
    return rule_service.get_version(db, version_id)


def _replay(stored, view_cls):
    return view_cls.model_validate(stored) if stored is not None else None


def _snapshot(view):
    return view.model_dump(by_alias=True, mode="json")


def _require_key(key: str) -> str:
    if not key or not key.strip():
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency-Key 不能为空白", fields={"Idempotency-Key": "空白"})
    return key


def _get_requirement(db, requirement_id: str, version_id: str):
    row = db.execute("SELECT * FROM requirements WHERE id=? AND rule_version_id=?",
                     (requirement_id, version_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "要求不存在于该版本")
    return row


# ---------- Brief 登记（t-19） ----------


@router.post("/projects/{project_id}/briefs", response_model=Envelope[BriefRegistrationResult], status_code=201)
def create_brief(project_id: str, request: Request, payload: BriefCreate,
                 idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    _require_key(idempotency_key)
    settings = request.app.state.settings
    with connection(settings.database_path) as db:
        result = service_registration.create_brief(db, settings, project_id, payload, idempotency_key)
    return Envelope(data=BriefRegistrationResult(**result), request_id=request.state.request_id)


# ---------- 规则版本 ----------


@router.post("/briefs/{brief_id}/rule-versions", response_model=Envelope[VersionView], status_code=201)
def create_version(brief_id: str, request: Request, payload: VersionCreate,
                   idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    """派生草稿：真实复制要求与 open 冲突（新 id、lineage 保留）；源不一致拒绝搬证据。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    fingerprint = rule_service.fp("version", brief_id, payload.source_version_id, payload.parent_id or "")
    scope = f"local-reviewer:demo:POST/rule-versions:{brief_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        brief = _load_brief(db, brief_id)  # 归属/模式先行
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), VersionView)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        source = _load_source(db, payload.source_version_id, brief_id)
        if source["extraction_status"] != "ready":
            raise ApiError(409, "PARSE_NOT_READY", "草稿只能基于解析就绪的源版本")
        parent = None
        if payload.parent_id:
            parent = _authorize(db, payload.parent_id)  # 同模式归属
            if parent["brief_id"] != brief_id:
                raise ApiError(400, "VALIDATION_ERROR", "派生只能基于同一 Brief 的规则版本",
                               fields={"parentId": "跨 Brief 不可借"})
            if parent["source_version_id"] != payload.source_version_id:
                raise ApiError(409, "SOURCE_MISMATCH", "派生源版本与父版本不一致，不把旧证据搬到新来源",
                               details={"parentSource": parent["source_version_id"]})
            if parent["state"] != "enabled":
                raise ApiError(400, "VALIDATION_ERROR", "派生草稿只能基于已启用版本")
        count = db.execute("SELECT COUNT(*) FROM rule_versions WHERE brief_id=?", (brief_id,)).fetchone()[0]
        view = VersionView(id=uuid4().hex, brief_id=brief_id, source_version_id=source["id"],
                           version_label=f"v{count + 1}", state="draft", parent_id=payload.parent_id,
                           revision=1, confirmed_by=None, confirmed_at=None, created_at=now())
        db.execute("INSERT INTO rule_versions (id,brief_id,source_version_id,version_label,state,parent_id,revision,created_at) "
                   "VALUES (?,?,?,?,?,?,1,?)",
                   (view.id, brief_id, source["id"], view.version_label, "draft", payload.parent_id, view.created_at))
        copied = rule_service.copy_draft_from(db, parent, view.id) if parent else 0
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(view), view.created_at)
        db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                   (uuid4().hex, "local-reviewer", "rule_version", view.id,
                    "draft_derived" if parent else "draft_created", "demo",
                    json.dumps({"source": source["id"], "parent": payload.parent_id, "copied": copied}),
                    view.created_at))
    return Envelope(data=view, request_id=request.state.request_id)


@router.get("/briefs/{brief_id}/rule-versions", response_model=Envelope[list[VersionView]])
def list_versions(brief_id: str, request: Request):
    settings = request.app.state.settings
    with connection(settings.database_path) as db:
        _load_brief(db, brief_id)
        rows = db.execute("SELECT id,brief_id,source_version_id,version_label,state,parent_id,revision,"
                          "confirmed_by,confirmed_at,created_at FROM rule_versions WHERE brief_id=? "
                          "ORDER BY created_at DESC,id DESC", (brief_id,)).fetchall()
    return Envelope(data=[VersionView(**dict(r)) for r in rows], request_id=request.state.request_id)


@router.get("/rule-versions/{version_id}", response_model=Envelope[VersionDetail])
def version_detail(version_id: str, request: Request):
    settings = request.app.state.settings
    with connection(settings.database_path) as db:
        version = _authorize(db, version_id)  # GET 同样模式核查
        reqs = db.execute("SELECT id,lineage_id,text,category,importance,platforms_json,content_types_json,"
                          "phases_json,review_stages_json,exceptions,source_json,confirmation FROM requirements "
                          "WHERE rule_version_id=? ORDER BY rowid", (version_id,)).fetchall()
        conflicts = db.execute("SELECT id,requirement_ids_json,explanation,state,resolution FROM rule_conflicts "
                               "WHERE rule_version_id=?", (version_id,)).fetchall()
        coverage = db.execute("SELECT id,uncovered_items_json,statement,reason,created_at FROM coverage_confirmations "
                              "WHERE rule_version_id=? ORDER BY created_at DESC", (version_id,)).fetchall()
        links = db.execute("SELECT confirmation_id, uncovered_item, requirement_id, note "
                           "FROM coverage_item_links WHERE confirmation_id IN "
                           "(SELECT id FROM coverage_confirmations WHERE rule_version_id=?)", (version_id,)).fetchall()
    linked: dict[str, list[ItemMapping]] = {}
    for row in links:
        linked.setdefault(row["confirmation_id"], []).append(
            ItemMapping(uncovered_source_id=row["uncovered_item"], requirement_id=row["requirement_id"],
                        note=row["note"]))
    return Envelope(data=VersionDetail(
        version=VersionView(**{k: version[k] for k in version.keys() if k in VersionView.model_fields}),
        requirements=[_get_requirement_view(r, version_id) for r in reqs],
        conflicts=[ConflictView(id=c["id"], rule_version_id=version_id,
                                requirement_ids=json.loads(c["requirement_ids_json"]),
                                explanation=c["explanation"], state=c["state"], resolution=c["resolution"])
                   for c in conflicts],
        coverage=[CoverageRow(uncovered_items=json.loads(c["uncovered_items_json"]), statement=c["statement"],
                              reason=c["reason"], item_mappings=sorted(linked.get(c["id"], []),
                                                                       key=lambda m: m.uncovered_source_id),
                              created_at=c["created_at"]) for c in coverage]),
        request_id=request.state.request_id)


def _get_requirement_view(row, version_id: str) -> RequirementView:
    return RequirementView(id=row["id"], lineage_id=row["lineage_id"], rule_version_id=version_id,
                           text=row["text"], category=row["category"], importance=row["importance"],
                           platforms=json.loads(row["platforms_json"]),
                           content_types=json.loads(row["content_types_json"]),
                           phases=json.loads(row["phases_json"]), review_stages=json.loads(row["review_stages_json"]),
                           exceptions=row["exceptions"], source=json.loads(row["source_json"]),
                           confirmation=row["confirmation"])


@router.post("/rule-versions/{version_id}/requirements", response_model=Envelope[RequirementView], status_code=201)
def add_requirement(version_id: str, request: Request, body: RequirementBody,
                    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                    if_match: str | None = Header(default=None, alias="If-Match")):
    _require_key(idempotency_key)
    settings = request.app.state.settings
    platforms = sorted({p.value for p in body.platforms})
    payload = {**body.model_dump(by_alias=True, mode="json"), "platforms": platforms}
    fingerprint = rule_service.fp("req", version_id, json.dumps(payload, sort_keys=True, ensure_ascii=False))
    scope = f"local-reviewer:demo:POST/requirements:{version_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = _authorize(db, version_id)
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), RequirementView)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        brief = _load_brief(db, version["brief_id"])
        source = _load_source(db, version["source_version_id"], version["brief_id"])
        rule_service.validate_source(db, source, body.source)
        rule_service.platforms_gate(db, brief, platforms)
        view = RequirementView(id=uuid4().hex, lineage_id=uuid4().hex, rule_version_id=version_id,
                               text=body.text.strip(), category=body.category, importance=body.importance,
                               platforms=platforms, content_types=body.content_types, phases=body.phases,
                               review_stages=body.review_stages, exceptions=body.exceptions,
                               source=body.source, confirmation="pending")
        stamp = now()
        db.execute("INSERT INTO requirements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (view.id, version_id, view.lineage_id, view.text, view.category, view.importance,
                    json.dumps(view.platforms), json.dumps(view.content_types), json.dumps(view.phases),
                    json.dumps(view.review_stages), view.exceptions,
                    json.dumps(view.source, ensure_ascii=False), "pending"))
        rule_service.bump(db, version_id)
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(view), stamp)
    return Envelope(data=view, request_id=request.state.request_id)


@router.patch("/requirements/{requirement_id}", response_model=Envelope[RequirementView])
def patch_requirement(requirement_id: str, request: Request, body: RequirementPatch,
                      idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                      if_match: str | None = Header(default=None, alias="If-Match")):
    """契约 3.1 编辑要求：审核语义字段（文本/类别/重要性/范围/来源）变更后已确认项**重置 pending**；
    仅 exceptions 变更不重置。lineageId/所属版本锁定；If-Match=父版本 revision（要求行无独立 revision）。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    patch = body.model_dump(by_alias=True, mode="json", exclude_none=True)
    fingerprint = rule_service.fp("patch-req", requirement_id, if_match or "",
                                  json.dumps(patch, sort_keys=True, ensure_ascii=False))
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM requirements WHERE id=?", (requirement_id,)).fetchone()
        if row is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "要求不存在")
        version = _authorize(db, row["rule_version_id"])
        replay = _replay(rule_service.replay_or_none(
            db, f"local-reviewer:demo:PATCH/requirements:{row['rule_version_id']}", idempotency_key, fingerprint),
            RequirementView)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        # 语义变化=补丁中任一字段的实际值与当前不同（相同值 no-op 不清确认）；
        # A 00:37 更正：exceptions 直接改变审核语义（可豁免必须项），变化同样重置 pending。
        current = {"text": row["text"], "category": row["category"], "importance": row["importance"],
                   "platforms": sorted(json.loads(row["platforms_json"])),
                   "content_types": json.loads(row["content_types_json"]), "phases": json.loads(row["phases_json"]),
                   "review_stages": json.loads(row["review_stages_json"]), "source": json.loads(row["source_json"]),
                   "exceptions": row["exceptions"]}
        normalized_patch = {k: (sorted({p.value if isinstance(p, Platform) else p for p in v}) if k == "platforms" else v)
                            for k, v in patch.items()}
        semantic = any(normalized_patch.get(field) != current[field] for field in normalized_patch)
        platforms = sorted({p.value if isinstance(p, Platform) else p for p in
                            patch.get("platforms", json.loads(row["platforms_json"]))})
        brief = _load_brief(db, version["brief_id"])
        source = _load_source(db, version["source_version_id"], version["brief_id"])
        new_source = patch.get("source", json.loads(row["source_json"]))
        if semantic:
            rule_service.validate_source(db, source, new_source)
            rule_service.platforms_gate(db, brief, platforms)
        merged = {"text": patch.get("text", row["text"]).strip(), "category": patch.get("category", row["category"]),
                  "importance": patch.get("importance", row["importance"]), "platforms": platforms,
                  "content_types": patch.get("content_types", json.loads(row["content_types_json"])),
                  "phases": patch.get("phases", json.loads(row["phases_json"])),
                  "review_stages": patch.get("review_stages", json.loads(row["review_stages_json"])),
                  "exceptions": patch.get("exceptions", row["exceptions"]), "source": new_source}
        if not merged["category"]:
            raise ApiError(400, "VALIDATION_ERROR", "类别不能为空", fields={"category": "空白"})
        confirmation = "pending" if semantic and row["confirmation"] == "confirmed" else row["confirmation"]
        db.execute("UPDATE requirements SET text=?,category=?,importance=?,platforms_json=?,content_types_json=?,"
                   "phases_json=?,review_stages_json=?,exceptions=?,source_json=?,confirmation=? WHERE id=?",
                   (merged["text"], merged["category"], merged["importance"], json.dumps(merged["platforms"]),
                    json.dumps(merged["content_types"]), json.dumps(merged["phases"]),
                    json.dumps(merged["review_stages"]), merged["exceptions"],
                    json.dumps(merged["source"], ensure_ascii=False), confirmation, requirement_id))
        rule_service.bump(db, version["id"])
        view = RequirementView(id=requirement_id, lineage_id=row["lineage_id"], rule_version_id=version["id"],
                               confirmation=confirmation, **merged)
        rule_service.record(db, f"local-reviewer:demo:PATCH/requirements:{version['id']}", idempotency_key,
                            fingerprint, _snapshot(view), now())
    return Envelope(data=view, request_id=request.state.request_id)


@router.post("/rule-versions/{version_id}/confirm", response_model=Envelope[ConfirmResult])
def confirm_requirements(version_id: str, request: Request, body: ConfirmBody,
                         idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                         if_match: str | None = Header(default=None, alias="If-Match")):
    _require_key(idempotency_key)
    settings = request.app.state.settings
    ids = sorted(set(body.requirement_ids))
    fingerprint = rule_service.fp("confirm", version_id, if_match or "", json.dumps(ids))
    scope = f"local-reviewer:demo:POST/confirm:{version_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = _authorize(db, version_id)
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), ConfirmResult)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        in_conflict: set[str] = set()
        for row in db.execute("SELECT requirement_ids_json FROM rule_conflicts WHERE rule_version_id=? AND state='open'",
                              (version_id,)):
            in_conflict.update(json.loads(row[0]))
        confirmed: list[str] = []
        skipped: list[SkipItem] = []
        stamp = now()
        for req_id in ids:
            row = db.execute("SELECT text,category,platforms_json,confirmation FROM requirements "
                             "WHERE id=? AND rule_version_id=?", (req_id, version_id)).fetchone()
            if row is None:
                skipped.append(SkipItem(id=req_id, reason="not_found"))
                continue
            if req_id in in_conflict:
                skipped.append(SkipItem(id=req_id, reason="open_conflict"))
                continue
            if row["confirmation"] == "disabled":
                skipped.append(SkipItem(id=req_id, reason="disabled"))
                continue
            if row["confirmation"] == "confirmed":
                confirmed.append(req_id)
                continue
            try:
                complete = bool(row["text"].strip()) and bool(row["category"]) and bool(json.loads(row["platforms_json"]))
            except (TypeError, ValueError):
                complete = False
            if not complete:
                skipped.append(SkipItem(id=req_id, reason="incomplete_fields"))
                continue
            db.execute("UPDATE requirements SET confirmation='confirmed' WHERE id=?", (req_id,))
            confirmed.append(req_id)
        if confirmed:
            rule_service.bump(db, version_id)
            db.execute("UPDATE rule_versions SET state='pending_confirmation' WHERE id=? AND state='draft'",
                       (version_id,))
        result = ConfirmResult(confirmed_ids=confirmed, skipped=skipped)
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(result), stamp)
    return Envelope(data=result, request_id=request.state.request_id)


@router.post("/rule-versions/{version_id}/disable", response_model=Envelope[DisableResult])
def disable_requirement(version_id: str, request: Request, body: DisableBody,
                        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                        if_match: str | None = Header(default=None, alias="If-Match")):
    """确认清零第二路径：明确停用（需理由）。请求体入参（对齐基线，不用 Query）。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    fingerprint = rule_service.fp("disable", version_id, body.requirement_id, body.reason.strip(), if_match or "")
    scope = f"local-reviewer:demo:POST/disable:{version_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = _authorize(db, version_id)
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), DisableResult)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        _get_requirement(db, body.requirement_id, version_id)
        db.execute("UPDATE requirements SET confirmation='disabled' WHERE id=?", (body.requirement_id,))
        rule_service.bump(db, version_id)
        result = DisableResult(requirement_id=body.requirement_id, confirmation="disabled")
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(result), now())
    return Envelope(data=result, request_id=request.state.request_id)


@router.post("/rule-versions/{version_id}/conflicts", response_model=Envelope[ConflictView], status_code=201)
def add_conflict(version_id: str, request: Request, body: ConflictCreate,
                 idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                 if_match: str | None = Header(default=None, alias="If-Match")):
    _require_key(idempotency_key)
    settings = request.app.state.settings
    ids = sorted(set(body.requirement_ids))
    fingerprint = rule_service.fp("conflict", version_id, if_match or "", json.dumps(ids), body.explanation.strip())
    scope = f"local-reviewer:demo:POST/conflicts:{version_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = _authorize(db, version_id)
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), ConflictView)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        for req_id in ids:
            _get_requirement(db, req_id, version_id)
        view = ConflictView(id=uuid4().hex, rule_version_id=version_id, requirement_ids=ids,
                            explanation=body.explanation.strip(), state="open", resolution=None)
        db.execute("INSERT INTO rule_conflicts VALUES (?,?,?,?,?,?,?)",
                   (view.id, version_id, json.dumps(ids), view.explanation, "open", None, None))
        rule_service.bump(db, version_id)
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(view), now())
    return Envelope(data=view, request_id=request.state.request_id)


@router.post("/conflicts/{conflict_id}/resolve", response_model=Envelope[ResolveResult])
def resolve_conflict(conflict_id: str, request: Request, body: ConflictResolve,
                     idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                     if_match: str | None = Header(default=None, alias="If-Match")):
    """原子解决：冲突置 resolved 并应用受控改动（仅限本冲突关联且同版本）；
    update_text 会重置该项 pending 需重新确认；重放不重复变更。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    changes = [c.model_dump(by_alias=True, mode="json", exclude_none=True) for c in body.affected_requirement_changes]
    fingerprint = rule_service.fp("resolve", conflict_id, if_match or "", body.resolution.strip(),
                                  json.dumps(changes, sort_keys=True, ensure_ascii=False))
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        conflict = db.execute("SELECT * FROM rule_conflicts WHERE id=?", (conflict_id,)).fetchone()
        if conflict is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "冲突不存在")
        version = _authorize(db, conflict["rule_version_id"])
        scope = f"local-reviewer:demo:POST/resolve:{conflict_id}"
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), ResolveResult)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        related = set(json.loads(conflict["requirement_ids_json"]))
        applied: list[SkipItem] = []
        brief = _load_brief(db, version["brief_id"])
        source = _load_source(db, version["source_version_id"], version["brief_id"])
        for change in changes:
            req_id = change["requirementId"]
            if req_id not in related:
                raise ApiError(400, "VALIDATION_ERROR", "受控改动只能针对本冲突关联的要求",
                               fields={"affectedRequirementChanges": req_id})
            row = _get_requirement(db, req_id, version["id"])
            if change["action"] == "disable":
                db.execute("UPDATE requirements SET confirmation='disabled' WHERE id=?", (req_id,))
                applied.append(SkipItem(id=req_id, reason="disabled"))
            else:
                new_text = (change.get("text") or "").strip()
                platforms = sorted({p.value if isinstance(p, Platform) else p
                                    for p in json.loads(row["platforms_json"])})
                rule_service.platforms_gate(db, brief, platforms)
                db.execute("UPDATE requirements SET text=?, confirmation='pending' WHERE id=?", (new_text, req_id))
                applied.append(SkipItem(id=req_id, reason="text_updated_pending"))
        cur = db.execute("UPDATE rule_conflicts SET state='resolved', resolution=?, resolved_by=? "
                         "WHERE id=? AND state='open'",
                         (body.resolution.strip(), "local-reviewer", conflict_id))
        if cur.rowcount != 1:
            raise ApiError(409, "CONFLICT_ALREADY_RESOLVED", "冲突已解决")
        rule_service.bump(db, version["id"])
        result = ResolveResult(conflict_id=conflict_id, state="resolved", resolution=body.resolution.strip(),
                              applied_changes=applied)
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(result), now())
    return Envelope(data=result, request_id=request.state.request_id)


@router.post("/rule-versions/{version_id}/coverage-confirmation", response_model=Envelope[CoverageView], status_code=201)
def coverage_confirmation(version_id: str, request: Request, body: CoverageBody,
                          idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                          if_match: str | None = Header(default=None, alias="If-Match")):
    """未覆盖逐条签署（B_0006）：uncoveredSourceIds 与 itemMappings 一一对应；
    每条链接要求必须属本版本、非 disabled、来源为品牌人工补录声明（manualNote），
    note 是该 item 的人工责任声明（不宣称机器验证补录语义真实性）。
    同一补录确实覆盖多 item 时逐 item 单独签述；不允许无声明挂普通已确认引用项。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    items = sorted(set(body.uncovered_source_ids))
    if len(body.uncovered_source_ids) != len(items):
        raise ApiError(400, "VALIDATION_ERROR", "uncoveredSourceIds 不能重复", fields={"uncoveredSourceIds": "重复"})
    mapped = [m.model_copy(update={"uncovered_source_id": m.uncovered_source_id.strip()}) for m in body.item_mappings]
    mapped_ids = [m.uncovered_source_id for m in mapped]
    if len(set(mapped_ids)) != len(mapped_ids):
        raise ApiError(400, "VALIDATION_ERROR", "itemMappings 的 uncoveredSourceId 不能重复",
                       fields={"itemMappings": "重复项"})
    if set(mapped_ids) != set(items):
        missing = sorted(set(items) - set(mapped_ids))
        extra = sorted(set(mapped_ids) - set(items))
        raise ApiError(400, "VALIDATION_ERROR", "itemMappings 必须与 uncoveredSourceIds 一一对应",
                       fields={"itemMappings": json.dumps({"missing": missing, "unknown": extra}, ensure_ascii=False)})
    canonical = json.dumps(sorted([m.uncovered_source_id, m.requirement_id, m.note.strip()] for m in mapped),
                           ensure_ascii=False)
    fingerprint = rule_service.fp("coverage", version_id, if_match or "", json.dumps(items), canonical,
                                  body.statement.strip(), body.reason.strip())
    scope = f"local-reviewer:demo:POST/coverage:{version_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = _authorize(db, version_id)
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), CoverageView)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        source = _load_source(db, version["source_version_id"], version["brief_id"])
        uncovered = json.loads(source["uncovered_sources_json"] or "[]")
        unknown = [u for u in items if u not in uncovered]
        if unknown:
            raise ApiError(400, "VALIDATION_ERROR", "签署项必须是该源版本的未覆盖来源",
                           fields={"uncoveredSourceIds": ", ".join(unknown)},
                           details={"uncovered": uncovered})
        stamp = now()
        view_id = uuid4().hex
        db.execute("INSERT INTO coverage_confirmations VALUES (?,?,?,?,?,?,?,?)",
                   (view_id, version_id, json.dumps(items), body.statement.strip(), body.reason.strip(),
                    "local-reviewer", "demo", stamp))
        mappings: list[ItemMapping] = []
        for mapping in mapped:
            note = mapping.note.strip()
            if not (1 <= len(note) <= 500):
                raise ApiError(400, "VALIDATION_ERROR", "note 必填 1–500 字",
                               fields={"itemMappings": mapping.uncovered_source_id})
            requirement = _get_requirement(db, mapping.requirement_id, version_id)
            if requirement["confirmation"] == "disabled":
                raise ApiError(400, "VALIDATION_ERROR", "补录要求已停用，不能作为覆盖关联",
                               fields={"itemMappings": mapping.uncovered_source_id})
            requirement_source = json.loads(requirement["source_json"])
            supplement = requirement_source.get("manualNote")
            if not (isinstance(supplement, str) and supplement.strip()):
                # 文档引用型要求无法对应"未覆盖项"（正因为未覆盖才无文本证据），拒无声明挂载。
                raise ApiError(400, "VALIDATION_ERROR",
                               "关联要求必须是品牌人工补录声明（source.manualNote），普通文档引用项不得挂未覆盖页",
                               fields={"itemMappings": f"{mapping.uncovered_source_id} -> {mapping.requirement_id}"})
            mappings.append(ItemMapping(uncovered_source_id=mapping.uncovered_source_id,
                                        requirement_id=mapping.requirement_id, note=note))
            db.execute("INSERT INTO coverage_item_links VALUES (?,?,?,?,?,?)",
                       (view_id, mapping.uncovered_source_id, mapping.requirement_id, note,
                        json.dumps(requirement_source, ensure_ascii=False, sort_keys=True), stamp))
        view = CoverageView(id=view_id, rule_version_id=version_id, uncovered_items=items, item_mappings=mappings)
        rule_service.bump(db, version_id)
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(view), stamp)
    return Envelope(data=view, request_id=request.state.request_id)


@router.post("/rule-versions/{version_id}/enable", response_model=Envelope[VersionView])
def enable_version(version_id: str, request: Request, body: EnableBody,
                   idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
                   if_match: str | None = Header(default=None, alias="If-Match")):
    """启用不可变快照：条件 UPDATE 绑定 revision+状态；只动本版，不改他版/任务绑定（A 裁决 A）。
    changeSummary 为请求体字段（对齐基线接口位置）。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    fingerprint = rule_service.fp("enable", version_id, if_match or "", body.change_summary.strip())
    scope = f"local-reviewer:demo:POST/enable:{version_id}"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        version = _authorize(db, version_id)
        replay = _replay(rule_service.replay_or_none(db, scope, idempotency_key, fingerprint), VersionView)
        if replay is not None:
            return Envelope(data=replay, request_id=request.state.request_id)
        rule_service.require_editable(version)
        rule_service.check_if_match(version, if_match)
        brief = _load_brief(db, version["brief_id"])
        source = _load_source(db, version["source_version_id"], version["brief_id"])
        rule_service.enable_gate(db, version_id, brief, source)
        stamp = now()
        cur = db.execute("UPDATE rule_versions SET state='enabled', confirmed_by=?, confirmed_at=?, "
                         "revision=revision+1 WHERE id=? AND revision=? AND state IN ('draft','pending_confirmation')",
                         ("local-reviewer", stamp, version_id, version["revision"]))
        if cur.rowcount != 1:
            raise ApiError(409, "REVISION_CONFLICT", "版本状态或修订已变更，请重读")
        updated = db.execute("SELECT id,brief_id,source_version_id,version_label,state,parent_id,revision,"
                             "confirmed_by,confirmed_at,created_at FROM rule_versions WHERE id=?",
                             (version_id,)).fetchone()
        view = VersionView(**dict(updated))
        rule_service.record(db, scope, idempotency_key, fingerprint, _snapshot(view), stamp)
        db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                   (uuid4().hex, "local-reviewer", "rule_version", version_id, "enabled", "demo",
                    json.dumps({"changeSummary": body.change_summary.strip()}), stamp))
    return Envelope(data=view, request_id=request.state.request_id)


@router.post("/tasks/{task_id}/scripts", response_model=Envelope[ScriptVersionCreated], status_code=201)
def create_script_version(task_id: str, request: Request, payload: ScriptCreate,
                          idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    """不可变脚本版本首增量（t-25）：粘贴/TXT 文件两路；不接 script-jobs、无 AI、不排无消费者队列。"""
    _require_key(idempotency_key)
    settings = request.app.state.settings
    with connection(settings.database_path) as db:
        result = script_service.create_script_version(db, settings, task_id, payload, idempotency_key)
    return Envelope(data=ScriptVersionCreated(**result), request_id=request.state.request_id)


@router.get("/tasks/{task_id}/scripts", response_model=Envelope[list[ScriptVersionSummary]])
def script_versions_list(task_id: str, request: Request):
    settings = request.app.state.settings
    with connection(settings.database_path) as db:
        data = [ScriptVersionSummary(**d) for d in script_service.list_script_versions(db, task_id)]
    return Envelope(data=data, request_id=request.state.request_id)


@router.get("/script-versions/{version_id}", response_model=Envelope[ScriptVersionDetail])
def script_version_detail(version_id: str, request: Request):
    settings = request.app.state.settings
    with connection(settings.database_path) as db:
        data = ScriptVersionDetail(**script_service.script_version_detail(db, version_id))
    return Envelope(data=data, request_id=request.state.request_id)
