"""Qwen-Omni（qwen3.8-omni-flash）最小适配器（D t-32；mock transport only）。

安全/契约基线（A 2026-10-20 12:00 验收条件）：
- 完整性诚实：本批无已验证的完整覆盖证据——Qwen 构造结果显式
  audio/video/rules 三 False + conclusion=INCOMPLETE，不因模型自报或
  素材存在而 True（supports_full_* 同理 False）
- raw_usage 为本地子类字段（QwenOmniAnalysisResult），不改公共协议、
  不 setattr、不动 DB
- 审核上下文：构造注入 ReviewContextResolver（规则/脚本/配置 ID→冻结
  实体）；缺任何实际上下文/媒体元数据/已认可远端引用，发送前明确拒绝；
  返回的 rule/result ID 必须来自传入规则集合，未知/重复拒
- 配置冻结：model/endpoint/api_key 在构造时定格（含 model_config_id
  匹配校验）；不每次执行重读 env 冒充快照；密钥仅内存、不打印
- 传输：首批 httpx.MockTransport（无真实网络、不新增依赖）；发送后
  一切不确定失败→UnknownSubmissionError；仅 transport 可证明 never-sent
  才 SafeRetryableError；4xx→ProviderRejectedError（不可重试）；429/5xx
  不盲 retry（保守 unknown/rejected 语义）；requestId 能取则保留
- query_request_status：官方无查询端点——NotImplementedError（unsupported）
- 结构化输出：response_format=json_object + Pydantic 严格校验；
  非流式为 OpenAI 兼容标准面，真实连通验证前不切流式不重发
"""
from __future__ import annotations

import ipaddress
import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.contracts import Conclusion, Verdict
from app.core.review_contracts import Evidence, Issue, RuleResult
from app.modules.video_model.adapter import (
    InvalidAnalysisStructure,
    ModelAdapterProtocol,
    SafeRetryableError,
    UnknownSubmissionError,
    VideoAnalysisResult,
)

QWEN_MODEL_DEFAULT = "qwen3.8-omni-flash"
_SUPPORTED_CONTAINERS = {"mp4", "avi", "mkv", "mov", "flv", "wmv"}
_MAX_VIDEO_BYTES = 2_000_000_000  # 官方"2 GB"按十进制保守取（不冒充 2GiB）
_MAX_DURATION_S = 2 * 3600  # 官方 2 小时
_MAX_SCRIPT_CHARS = 2000  # 脚本全文预算：超限明确拒（不截断——全文一致性）


class PreSendFailure(SafeRetryableError):
    """唯一可信 never-sent 失败：调用方/transport 明确证明请求从未发出。

    仅当证据链明确（如本地构造期拒绝、注入的 transport 在发送前短路）
    才允许抛出。普通 httpx.ConnectError/ConnectTimeout 无法证明未发出
    ——一律按 UnknownSubmissionError 处理（DNS 解析后连接阶段失败仍可能
    已达服务端边缘）。
    """


class ProviderRejectedError(Exception):
    """供应商明确拒绝（4xx 鉴权/配额/参数）——不可重试。

    区别于 InvalidAnalysisStructure（结果格式失败）：这是请求被拒，
    没有产生任何分析结果。
    """


@dataclass(frozen=True)
class MediaMetadata:
    """服务端探测的媒体元数据（来自 MediaResolver，非模型自报）。"""

    byte_size: int
    container: str  # 无点小写：mp4/avi/...
    duration_s: float
    remote_reference: str | None = None  # 已认可的远端可达引用（无则不可发送）


@dataclass(frozen=True)
class FrozenRule:
    """冻结的规则实体（审核上下文解析器提供）。"""

    requirement_id: str
    text: str
    category: str
    importance: str


@dataclass(frozen=True)
class FrozenScript:
    """冻结的脚本实体（可选——无脚本阶段为 None）。"""

    script_version_id: str
    text: str


@dataclass(frozen=True)
class ModelIdentity:
    """构造时定格的配置身份（执行期不变——不重读 env）。"""

    model: str
    base_url: str
    model_config_id: str


class MediaResolver(ABC):
    """受信 fileId→服务端探测元数据 + 引用来源白名单（mock only）。"""

    @abstractmethod
    def resolve(self, video_file_id: str) -> MediaMetadata | None: ...

    @abstractmethod
    def is_trusted_reference(self, remote_reference: str) -> bool:
        """引用是否来自受信认可来源（签名 URL 服务/白名单域）。

        单纯字符串非空不够：localhost/私网 IP/非 https/userinfo/未认可
        origin 一律 False（本方法由受信实现判定，adapter 不自判）。
        """


class ReviewContextResolver(ABC):
    """受信 ID→冻结审核上下文（规则/脚本/配置身份）。"""

    @abstractmethod
    def rules(self, rule_version_id: str) -> list[FrozenRule] | None:
        """None=版本不存在或不可用（发送前拒绝，不发空壳 prompt）。"""

    @abstractmethod
    def script(self, script_version_id: str | None) -> FrozenScript | None:
        """脚本实体；None=引用无效（传了 ID 却解析不出）。"""

    @abstractmethod
    def identity(self, model_config_id: str) -> ModelIdentity | None:
        """配置身份；None=未知配置。"""


@dataclass
class QwenOmniAnalysisResult(VideoAnalysisResult):
    """本地子类：附加 raw_usage（已知原始 usage 原样透传，不算价不落 DB）。

    不改公共协议字段；禁止 setattr 塞字段。
    """

    raw_usage: dict[str, Any] | None = field(default=None)


class QwenOmniAdapter(ModelAdapterProtocol):
    """qwen3.8-omni-flash 适配器（首批 MockTransport；无真实网络）。

    构造注入全部依赖（transport/resolvers/身份/密钥）——密钥仅内存参数
    （调用方从 env 读一次传入），本类不读 .env、不打印密钥。
    """

    def __init__(self, *, transport: httpx.BaseTransport,
                 media: MediaResolver, context: ReviewContextResolver,
                 model_config_id: str, model: str = QWEN_MODEL_DEFAULT,
                 base_url: str = "", api_key: str = "",
                 timeout_s: float = 120.0):
        if not api_key:
            raise ValueError("api_key 必须由调用方注入（内存参数）")
        if not base_url:
            raise ValueError("base_url 必须由调用方注入")
        identity = context.identity(model_config_id)
        if identity is None:
            raise ValueError(f"未知模型配置 {model_config_id}——明确拒绝，不自动换")
        if identity.model_config_id != model_config_id:
            # 解析器返回的身份 ID 必须等于请求的配置 ID（构造期 fail-closed）
            raise ValueError("解析器返回的身份 ID 与请求配置不一致——构造失败")
        if identity.model != model or identity.base_url != base_url:
            raise ValueError("传入 model/base_url 与配置身份不一致——冻结校验失败")
        self._identity = identity  # 构造定格
        self._api_key = api_key
        self._transport = transport
        self._media = media
        self._context = context
        self._client = httpx.Client(transport=transport, timeout=timeout_s,
                                    base_url=base_url)

    # ---------- 能力诚实（官方未披露 fps/采样/音轨处理——不推断） ----------

    @property
    def supports_full_video(self) -> bool:
        return False  # 完整文件输入≠逐帧完整审阅；未验证

    @property
    def supports_full_audio(self) -> bool:
        return False  # 音轨处理细节未验证

    def query_request_status(self, provider_request_id: str) -> str:
        """官方无 Chat Completions 查询端点——unsupported（不模拟状态）。"""
        raise NotImplementedError(
            "qwen3.8-omni-flash 无任务查询端点；超时按 unknown 人工处置")

    # ---------- 审核执行 ----------

    def analyze_video(self, video_file_id: str, script_version_id: str | None,
                      rule_version_id: str, model_config_id: str) -> VideoAnalysisResult:
        # 0) 配置身份：执行参数必须匹配构造定格身份
        if model_config_id != self._identity.model_config_id:
            raise ProviderRejectedError(
                f"model_config_id {model_config_id} 与实例配置不符（构造冻结）")
        # 1) 审核上下文（发送前解析；缺任何一项即拒绝——不发空壳 prompt）
        rules = self._context.rules(rule_version_id)
        if not rules:
            raise ProviderRejectedError(f"规则版本 {rule_version_id} 不可解析——拒绝发送")
        rule_ids = [r.requirement_id for r in rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ProviderRejectedError("规则 ID 重复——拒绝")
        script = self._context.script(script_version_id)
        if script_version_id is not None and script is None:
            raise ProviderRejectedError(f"脚本 {script_version_id} 不可解析——拒绝发送")
        if script is not None and script.script_version_id != script_version_id:
            # 解析出的实体 ID 必须与请求 ID 一致（防解析器错位）
            raise ProviderRejectedError("脚本实体 ID 与请求不一致——拒绝发送")
        if script is not None and len(script.text) > _MAX_SCRIPT_CHARS:
            # 完整性优先：超预算明确拒绝，不静默截断（全文一致性）
            raise ProviderRejectedError(
                f"脚本超出 prompt 预算（{len(script.text)}>{_MAX_SCRIPT_CHARS}）——拒绝发送")
        # 2) 媒体元数据（服务端探测；缺失/非法 fail-closed）
        meta = self._media.resolve(video_file_id)
        if meta is None:
            raise ProviderRejectedError(f"视频 {video_file_id} 元数据缺失——fail-closed")
        if not _valid_media_meta(meta):
            raise ProviderRejectedError("媒体元数据非法（数值/引用）——fail-closed")
        if meta.byte_size > _MAX_VIDEO_BYTES:
            raise ProviderRejectedError("视频超出官方 2GB URL 限制")
        if meta.duration_s > _MAX_DURATION_S:
            raise ProviderRejectedError("视频超出官方 2 小时时长限制")
        if meta.container not in _SUPPORTED_CONTAINERS:
            raise ProviderRejectedError(f"容器 {meta.container} 不在官方支持列表")
        if not self._media.is_trusted_reference(meta.remote_reference or ""):
            # 受信来源白名单（非单纯字符串非空）；公网签名 URL 服务为依赖缺口
            raise ProviderRejectedError(
                "远端引用未获受信解析器认可（签名 URL 服务为依赖缺口）——拒绝发送")
        # 3) 发送（MockTransport；结构化 JSON Object 输出）
        payload = {
            "model": self._identity.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": meta.remote_reference}},
                    {"type": "text", "text": _build_prompt(rules, script)},
                ],
            }],
            "modalities": ["text"],
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post("/chat/completions", json=payload,
                                         headers={"Authorization": f"Bearer {self._api_key}"})
        except PreSendFailure:
            raise  # 唯一可信 never-sent（显式证据链）——安全重试
        except SafeRetryableError:
            # 普通 SafeRetryableError 无 never-sent 证据：保守转 unknown
            raise UnknownSubmissionError("提交状态未知（无未发出证据）")
        except httpx.HTTPError as exc:
            # 普通网络异常（含 ConnectError/ConnectTimeout/读写超时/中断）：
            # 无法证明请求从未到达——一律保守 unknown，绝不安全重发
            raise UnknownSubmissionError(f"提交状态未知：{type(exc).__name__}")
        # 4) 响应处理（严格 envelope 校验；固定安全文案不透传原文）
        if response.status_code >= 500 or response.status_code == 429:
            rid = _safe_request_id(response)
            exc = UnknownSubmissionError(f"供应商 {response.status_code}，状态不确定")
            exc.provider_request_id = rid  # type: ignore[attr-defined]
            raise exc
        if response.status_code >= 400:
            raise ProviderRejectedError(f"供应商拒绝：HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise InvalidAnalysisStructure("供应商响应非法：非 JSON")
        if not isinstance(body, dict):
            raise InvalidAnalysisStructure("供应商响应非法：非 JSON 对象")
        request_id = body.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise InvalidAnalysisStructure("供应商响应非法：缺少请求标识")
        content = _content_text(body)
        usage = body.get("usage")
        parsed = _parse_structured(content, rule_ids, meta.duration_s)
        # 5) 完整性诚实：本批无已验证覆盖证据——显式三 False + INCOMPLETE
        return QwenOmniAnalysisResult(
            conclusion=Conclusion.INCOMPLETE,
            results=parsed["results"],
            issues=parsed["issues"],
            provider_request_id=request_id,
            audio_complete=False,
            video_complete=False,
            rules_covered=False,
            raw_usage=usage if isinstance(usage, dict) else None,
        )


def _build_prompt(rules: list[FrozenRule], script: FrozenScript | None) -> str:
    lines = ["按以下规则逐条审核视频，输出 JSON：{\"results\":[{\"requirementId\":"
             "\"...\",\"verdict\":\"compliant|non_compliant|needs_confirmation|not_applicable\","
             "\"rationale\":\"...\"}],\"issues\":[{\"requirementId\":\"...\",\"category\":"
             "\"visual|spoken|subtitle|script_consistency|other\",\"importance\":\"must|suggested\","
             "\"description\":\"...\",\"startMs\":0,\"endMs\":0}]}", "规则列表："]
    for r in rules:
        lines.append(f"- [{r.requirement_id}]（{r.category}/{r.importance}）{r.text}")
    if script:
        # 完整冻结脚本（不截断——全文一致性）；资格未在本 adapter 验证，
        # 文案如实（真实 approved 状态须以后由可信 resolver 证据支撑）。
        lines.append(f"绑定脚本（资格未在本 adapter 验证，id={script.script_version_id}）：")
        lines.append(script.text)
    return "\n".join(lines)


def _valid_media_meta(meta: MediaMetadata) -> bool:
    """元数据数值与引用的输入边界：正数/finite/字符串形状。"""
    if not isinstance(meta.byte_size, int) or isinstance(meta.byte_size, bool):
        return False
    if meta.byte_size <= 0:
        return False
    if not isinstance(meta.duration_s, (int, float)) or isinstance(meta.duration_s, bool):
        return False
    if not math.isfinite(meta.duration_s) or meta.duration_s <= 0:
        return False
    if not isinstance(meta.container, str) or not meta.container:
        return False
    if meta.remote_reference is not None and not isinstance(meta.remote_reference, str):
        return False
    return True


def _safe_request_id(response: httpx.Response) -> str | None:
    """尽力提取 requestId（失败返回 None，不透传异常细节）。"""
    try:
        body = response.json()
        if isinstance(body, dict):
            rid = body.get("id")
            return rid if isinstance(rid, str) else None
    except Exception:
        pass
    return None


def _content_text(body: dict[str, Any]) -> str:
    """提取 choices[0].message.content（严格类型；固定安全文案）。"""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise InvalidAnalysisStructure("供应商响应非法：choices 结构缺失")
    if not isinstance(content, str):
        raise InvalidAnalysisStructure("供应商响应非法：content 非 string")
    return content


def _parse_structured(content: str, allowed_rule_ids: list[str],
                      media_duration_s: float) -> dict[str, list[Any]]:
    """严格校验：JSON Object + 结果/问题 ID 白名单 + 时间区间合法。

    错误文案固定安全（不透传模型原文/URL）。
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise InvalidAnalysisStructure("模型输出非法：非合法 JSON")
    if not isinstance(data, dict):
        raise InvalidAnalysisStructure("模型输出非法：非 JSON Object")
    raw_results = data.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise InvalidAnalysisStructure("模型输出非法：results 缺失或为空")
    results: list[RuleResult] = []
    seen: set[str] = set()
    verdicts = {"compliant", "non_compliant", "needs_confirmation", "not_applicable"}
    for item in raw_results:
        if not isinstance(item, dict):
            raise InvalidAnalysisStructure("模型输出非法：result 项非对象")
        rid = item.get("requirementId")
        if not isinstance(rid, str) or rid not in allowed_rule_ids:
            raise InvalidAnalysisStructure("模型输出非法：结果引用未知规则")
        if rid in seen:
            raise InvalidAnalysisStructure("模型输出非法：结果重复引用规则")
        seen.add(rid)
        verdict = item.get("verdict")
        if verdict not in verdicts:
            raise InvalidAnalysisStructure("模型输出非法：verdict 非法")
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise InvalidAnalysisStructure("模型输出非法：rationale 缺失或空白")
        results.append(RuleResult(requirement_id=rid, verdict=Verdict(verdict),
                                  rationale=rationale, evidence=[]))
    raw_issues = data.get("issues", [])
    if not isinstance(raw_issues, list):
        raise InvalidAnalysisStructure("模型输出非法：issues 非数组")
    issues: list[Issue] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            raise InvalidAnalysisStructure("模型输出非法：issue 项非对象")
        rid = item.get("requirementId")
        if rid is not None and (not isinstance(rid, str) or rid not in allowed_rule_ids):
            raise InvalidAnalysisStructure("模型输出非法：问题引用未知规则")
        category = item.get("category")
        if category not in {"visual", "spoken", "subtitle", "script_consistency", "other"}:
            raise InvalidAnalysisStructure("模型输出非法：category 非法")
        importance = item.get("importance")
        if importance not in {"must", "suggested"}:
            raise InvalidAnalysisStructure("模型输出非法：importance 非法")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise InvalidAnalysisStructure("模型输出非法：issue description 缺失或空白")
        start, end = item.get("startMs"), item.get("endMs")
        evidence: list[Evidence] = []
        if start is not None or end is not None:
            # 给定了时间：必须 int（非 bool）、0<=start<end<=媒体时长——否则非法
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or not (0 <= start < end <= int(media_duration_s * 1000))):
                raise InvalidAnalysisStructure("模型输出非法：issue 时间区间非法")
            evidence.append(Evidence(scope="time-range", description=description,
                                     start_ms=start, end_ms=end))
        # 未给时间：无 evidence（缺定位明示为无定位，不伪造范围）
        issues.append(Issue(id=f"issue_{len(issues) + 1}", requirement_id=rid,
                            category=category, importance=importance,
                            description=description,
                            suggestion=item.get("suggestion") or "", evidence=evidence,
                            origin="ai"))
    return {"results": results, "issues": issues}
