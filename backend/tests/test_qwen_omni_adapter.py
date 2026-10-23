"""Qwen-Omni 适配器测试（D t-32 修订版；httpx.MockTransport 全注入零真实网络）。

A 12:06 三组修正的验证：
① ConnectError/ConnectTimeout 一律 unknown（不再 SafeRetryable）；
   发送后抛 ConnectError 也不可安全重发；仅显式 PreSendFailure 可安全重试
② 输入边界：正数/finite 元数据、trusted resolver 白名单（非单纯非空）、
   2GB 十进制、identity/script ID 一致性
③ 响应 envelope 严格校验（list/scalar/缺 id/非 string content）、
   issue 时间戳 int 非 bool 且 0<=start<end<=时长、固定安全文案
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.contracts import Conclusion, Verdict
from app.modules.video_model.adapter import (
    InvalidAnalysisStructure,
    SafeRetryableError,
    UnknownSubmissionError,
)
from app.modules.video_model.qwen_omni_adapter import (
    FrozenRule,
    FrozenScript,
    MediaMetadata,
    MediaResolver,
    ModelIdentity,
    PreSendFailure,
    ProviderRejectedError,
    QwenOmniAdapter,
    QwenOmniAnalysisResult,
    ReviewContextResolver,
)

API_KEY = "sk-test-key-000000"
BASE_URL = "https://ws-test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
RULES = [FrozenRule(requirement_id="r1", text="必须口播品牌", category="口播",
                    importance="must"),
         FrozenRule(requirement_id="r2", text="禁止功效词", category="禁用表达",
                    importance="must")]
MEDIA = MediaMetadata(byte_size=1024, container="mp4", duration_s=36.0,
                      remote_reference="https://signed.example.com/v.mp4?sig=x")
IDENTITY = ModelIdentity(model="qwen3.8-omni-flash", base_url=BASE_URL,
                         model_config_id="mc-1")


class StaticMedia(MediaResolver):
    def __init__(self, meta: MediaMetadata | None, trusted: bool = True):
        self._meta, self._trusted = meta, trusted
    def resolve(self, video_file_id: str) -> MediaMetadata | None: return self._meta
    def is_trusted_reference(self, remote_reference: str) -> bool:
        return self._trusted and bool(remote_reference)


class StaticContext(ReviewContextResolver):
    def __init__(self, rules=None, script=..., identity=IDENTITY):
        self._rules, self._script, self._identity = rules, script, identity
    def rules(self, rule_version_id): return self._rules
    def script(self, script_version_id):
        return self._script if script_version_id else None
    def identity(self, model_config_id):
        return self._identity if model_config_id == self._identity.model_config_id else None


def ok_response(content: dict, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={"id": "chatcmpl-test-1",
                                     "choices": [{"message": {"content": json.dumps(content)}}],
                                     "usage": usage})


GOOD_CONTENT = {"results": [
    {"requirementId": "r1", "verdict": "compliant", "rationale": "口播出现"},
    {"requirementId": "r2", "verdict": "needs_confirmation", "rationale": "疑似功效"}],
    "issues": [{"requirementId": "r2", "category": "spoken", "importance": "must",
                "description": "功效表达待确认", "startMs": 1000, "endMs": 2000}]}


def make_adapter(transport: httpx.MockTransport, media=None, context=None) -> QwenOmniAdapter:
    return QwenOmniAdapter(
        transport=transport,
        media=media if media is not None else StaticMedia(MEDIA),
        context=context if context is not None else StaticContext(rules=RULES),
        model_config_id="mc-1", model="qwen3.8-omni-flash",
        base_url=BASE_URL, api_key=API_KEY)


class TestHonestCompleteness:
    def test_three_false_and_incomplete_even_with_valid_response(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: ok_response(GOOD_CONTENT, {"prompt_tokens": 10,
                "prompt_tokens_details": {"audio_tokens": 5, "text_tokens": 5}})))
        result = adapter.analyze_video("f1", None, "rv-1", "mc-1")
        assert result.audio_complete is False and result.video_complete is False
        assert result.rules_covered is False
        assert result.conclusion == Conclusion.INCOMPLETE
        assert result.provider_request_id == "chatcmpl-test-1"
        assert isinstance(result, QwenOmniAnalysisResult)
        assert result.raw_usage is not None
        assert result.issues[0].evidence[0].start_ms == 1000

    def test_supports_full_all_false(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)))
        assert adapter.supports_full_video is False
        assert adapter.supports_full_audio is False

    def test_raw_usage_absent_kept_none(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)))
        assert adapter.analyze_video("f1", None, "rv-1", "mc-1").raw_usage is None


class TestTransportExceptionLayering:
    """① ConnectError/ConnectTimeout 一律 unknown；仅 PreSendFailure 可安全重试。"""

    def test_connect_error_is_unknown_not_safe_retryable(self):
        def fail(request):
            raise httpx.ConnectError("connection refused")
        adapter = make_adapter(httpx.MockTransport(fail))
        with pytest.raises(UnknownSubmissionError):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_connect_error_after_send_still_unknown(self):
        """模拟"已发生发送后"抛 ConnectError（部分写入后连接断）——绝不可安全重发。"""
        class SendThenFailTransport(httpx.BaseTransport):
            def handle_request(self, request):
                # 请求已进入传输层（模拟已发出）后连接异常
                raise httpx.ConnectError("reset after bytes sent")
        adapter = QwenOmniAdapter(transport=SendThenFailTransport(),
                                  media=StaticMedia(MEDIA), context=StaticContext(rules=RULES),
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        with pytest.raises(UnknownSubmissionError):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_connect_timeout_is_unknown(self):
        def fail(request):
            raise httpx.ConnectTimeout("connect timed out")
        adapter = make_adapter(httpx.MockTransport(fail))
        with pytest.raises(UnknownSubmissionError):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_read_timeout_is_unknown(self):
        def fail(request):
            raise httpx.ReadTimeout("timed out")
        adapter = make_adapter(httpx.MockTransport(fail))
        with pytest.raises(UnknownSubmissionError):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_presend_failure_is_only_safe_retryable(self):
        """显式 PreSendFailure（可信 never-sent 证据）才可安全重试。"""
        class PreSendTransport(httpx.BaseTransport):
            def handle_request(self, request):
                raise PreSendFailure("transport 证明请求未发出（本地短路）")
        adapter = QwenOmniAdapter(transport=PreSendTransport(),
                                  media=StaticMedia(MEDIA), context=StaticContext(rules=RULES),
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        with pytest.raises(SafeRetryableError):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_plain_safe_retryable_without_evidence_converted_to_unknown(self):
        """普通 SafeRetryableError（无 never-sent 证据）→ 转换为 unknown，不放行重试。"""
        class PlainSafeRetryableTransport(httpx.BaseTransport):
            def handle_request(self, request):
                raise SafeRetryableError("generic safe-retryable without evidence")
        adapter = QwenOmniAdapter(transport=PlainSafeRetryableTransport(),
                                  media=StaticMedia(MEDIA), context=StaticContext(rules=RULES),
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        with pytest.raises(UnknownSubmissionError, match="无未发出证据"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_5xx_is_unknown(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(502, json={"id": "chatcmpl-x",
                                                  "error": {"message": "bad gateway"}})))
        with pytest.raises(UnknownSubmissionError):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_429_is_unknown_request_id_kept(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(429, json={"id": "chatcmpl-rate"})))
        with pytest.raises(UnknownSubmissionError):
            try:
                adapter.analyze_video("f1", None, "rv-1", "mc-1")
            except UnknownSubmissionError as exc:
                assert getattr(exc, "provider_request_id", None) == "chatcmpl-rate"
                raise

    def test_4xx_is_provider_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})))
        with pytest.raises(ProviderRejectedError, match="401"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")


class TestInputBoundaries:
    """② 元数据数值边界 + trusted 白名单 + ID 一致性。"""

    @pytest.mark.parametrize("meta,match", [
        (MediaMetadata(0, "mp4", 60.0, "https://x"), "非法"),
        (MediaMetadata(-5, "mp4", 60.0, "https://x"), "非法"),
        (MediaMetadata(100, "mp4", 0.0, "https://x"), "非法"),
        (MediaMetadata(100, "mp4", -1.0, "https://x"), "非法"),
        (MediaMetadata(100, "mp4", float("nan"), "https://x"), "非法"),
        (MediaMetadata(100, "mp4", float("inf"), "https://x"), "非法"),
    ])
    def test_invalid_numeric_meta_fail_closed(self, meta, match):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                media=StaticMedia(meta))
        with pytest.raises(ProviderRejectedError, match=match):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_untrusted_reference_rejected(self):
        """引用非空但未获受信解析器认可——拒（白名单语义，非字符串非空）。"""
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                media=StaticMedia(MEDIA, trusted=False))
        with pytest.raises(ProviderRejectedError, match="受信"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_local_url_rejected_by_trusted_resolver_semantics(self):
        """localhost/私网/非 https 由受信 resolver 判 False（模拟其语义）。"""
        class StrictMedia(StaticMedia):
            def is_trusted_reference(self, remote_reference: str) -> bool:
                # 模拟受信白名单：仅 https 且非 localhost/私网
                from urllib.parse import urlparse
                import ipaddress
                try:
                    u = urlparse(remote_reference)
                    if u.scheme != "https" or not u.hostname:
                        return False
                    if "@" in remote_reference:  # userinfo
                        return False
                    try:
                        ip = ipaddress.ip_address(u.hostname)
                        if ip.is_private or ip.is_loopback or ip.is_link_local:
                            return False
                    except ValueError:
                        pass  # 域名——白名单域由实现判定
                    return u.hostname.endswith("signed.example.com")
                except Exception:
                    return False
        bad_refs = ["http://x/v.mp4", "https://localhost/v.mp4",
                    "https://192.168.1.1/v.mp4", "https://user:pw@signed.example.com/v.mp4",
                    "https://evil.com/v.mp4", ""]
        for ref in bad_refs:
            meta = MediaMetadata(100, "mp4", 10.0, ref)
            adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                    media=StrictMedia(meta))
            with pytest.raises(ProviderRejectedError, match="受信"):
                adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_2gb_decimal_boundary(self):
        """官方 2GB 按十进制：2_000_000_000 恰过，2_000_000_001 拒。"""
        ok_meta = MediaMetadata(2_000_000_000, "mp4", 60.0, "https://signed.example.com/v.mp4")
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                media=StaticMedia(ok_meta))
        adapter.analyze_video("f1", None, "rv-1", "mc-1")  # 边界值通过
        over = MediaMetadata(2_000_000_001, "mp4", 60.0, "https://signed.example.com/v.mp4")
        adapter2 = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                media=StaticMedia(over))
        with pytest.raises(ProviderRejectedError, match="2GB"):
            adapter2.analyze_video("f1", None, "rv-1", "mc-1")

    def test_script_entity_id_mismatch_rejected(self):
        wrong = FrozenScript("sv-OTHER", "正文")  # 实体 ID ≠ 请求 ID
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                               context=StaticContext(rules=RULES, script=wrong))
        with pytest.raises(ProviderRejectedError, match="脚本实体 ID"):
            adapter.analyze_video("f1", "sv-1", "rv-1", "mc-1")

    def test_identity_mismatch_rejected_at_execution(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)))
        with pytest.raises(ProviderRejectedError, match="实例配置不符"):
            adapter.analyze_video("f1", None, "rv-1", "mc-OTHER")

    def test_media_limit_violations(self):
        cases = [(MediaMetadata(3_000_000_000, "mp4", 60.0, "https://x"), "2GB"),
                 (MediaMetadata(100, "mp4", 3 * 3600.0, "https://x"), "2 小时"),
                 (MediaMetadata(100, "gif", 60.0, "https://x"), "容器")]
        for meta, match in cases:
            adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                   media=StaticMedia(meta))
            with pytest.raises(ProviderRejectedError, match=match):
                adapter.analyze_video("f1", None, "rv-1", "mc-1")


class TestResponseEnvelope:
    """③ 响应 envelope 严格校验（固定安全文案）。"""

    def test_list_body_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json=[1, 2, 3])))
        with pytest.raises(InvalidAnalysisStructure, match="非 JSON 对象"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_scalar_body_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json="ok")))
        with pytest.raises(InvalidAnalysisStructure, match="非 JSON 对象"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_missing_id_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json={"choices": [{"message": {
                "content": json.dumps(GOOD_CONTENT)}}]})))
        with pytest.raises(InvalidAnalysisStructure, match="缺少请求标识"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_non_string_content_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json={"id": "x", "choices": [
                {"message": {"content": {"nested": "dict"}}}]})))
        with pytest.raises(InvalidAnalysisStructure, match="content 非 string"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_missing_choices_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json={"id": "x"})))
        with pytest.raises(InvalidAnalysisStructure, match="choices 结构缺失"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_non_json_http_body_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, text="not json")))
        with pytest.raises(InvalidAnalysisStructure, match="非 JSON"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")


class TestStrictSchema:
    @pytest.mark.parametrize("content,match", [
        ({"results": [], "issues": []}, "results 缺失或为空"),
        ({"results": [{"requirementId": "rX", "verdict": "compliant", "rationale": "x"}]}, "未知规则"),
        ({"results": [{"requirementId": "r1", "verdict": "compliant", "rationale": "x"},
                      {"requirementId": "r1", "verdict": "compliant", "rationale": "y"}]}, "重复"),
        ({"results": [{"requirementId": "r1", "verdict": "maybe", "rationale": "x"}]}, "verdict"),
        ({"results": [{"requirementId": "r1", "verdict": "compliant", "rationale": "  "}]}, "rationale"),
    ])
    def test_invalid_structures_rejected(self, content, match):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(content)))
        with pytest.raises(InvalidAnalysisStructure, match=match):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_issue_unknown_rule_rejected(self):
        content = {"results": GOOD_CONTENT["results"],
                   "issues": [{"requirementId": "rX", "category": "visual",
                               "importance": "must", "description": "x"}]}
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(content)))
        with pytest.raises(InvalidAnalysisStructure, match="未知规则"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_non_json_content_rejected(self):
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json={"id": "x", "choices": [
                {"message": {"content": "not json at all"}}]})))
        with pytest.raises(InvalidAnalysisStructure, match="非合法 JSON"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")


class TestIssueTimestamps:
    """③ issue 时间戳：int 非 bool、0<=start<end<=时长；缺定位无 evidence。"""

    @pytest.mark.parametrize("start,end,match", [
        (True, 2000, "时间区间非法"),   # bool 非 int
        (1000, True, "时间区间非法"),
        (-1, 2000, "时间区间非法"),    # 负 start
        (2000, 1000, "时间区间非法"),  # end<=start
        (2000, 2000, "时间区间非法"),
        (1000, 999999, "时间区间非法"),  # end 超媒体时长（36s=36000ms）
    ])
    def test_invalid_timestamps_rejected(self, start, end, match):
        content = {"results": GOOD_CONTENT["results"],
                   "issues": [{"requirementId": "r2", "category": "spoken",
                               "importance": "must", "description": "x",
                               "startMs": start, "endMs": end}]}
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(content)))
        with pytest.raises(InvalidAnalysisStructure, match=match):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_boundary_timestamps_accepted(self):
        content = {"results": GOOD_CONTENT["results"],
                   "issues": [{"requirementId": "r2", "category": "spoken",
                               "importance": "must", "description": "x",
                               "startMs": 0, "endMs": 36000}]}  # 恰等于时长
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(content)))
        result = adapter.analyze_video("f1", None, "rv-1", "mc-1")
        assert result.issues[0].evidence[0].end_ms == 36000

    def test_missing_timestamps_no_evidence_no_fabrication(self):
        content = {"results": GOOD_CONTENT["results"],
                   "issues": [{"requirementId": "r2", "category": "spoken",
                               "importance": "must", "description": "无定位问题"}]}
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(content)))
        result = adapter.analyze_video("f1", None, "rv-1", "mc-1")
        assert result.issues[0].evidence == []  # 缺定位明示为无，不伪造范围


class TestPreSendRejection:
    def test_no_rules_rejected_before_send(self):
        sent = []
        adapter = make_adapter(httpx.MockTransport(
            lambda req: (sent.append(req), ok_response(GOOD_CONTENT))[1]),
            context=StaticContext(rules=None))
        with pytest.raises(ProviderRejectedError, match="规则版本"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")
        assert sent == []

    def test_no_media_fail_closed(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                                media=StaticMedia(None))
        with pytest.raises(ProviderRejectedError, match="元数据缺失"):
            adapter.analyze_video("f1", None, "rv-1", "mc-1")

    def test_invalid_script_reference_rejected(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)),
                               context=StaticContext(rules=RULES, script=None))
        with pytest.raises(ProviderRejectedError, match="脚本"):
            adapter.analyze_video("f1", "sv-ghost", "rv-1", "mc-1")


class TestConfigFreeze:
    def test_unknown_config_at_construction_rejected(self):
        with pytest.raises(ValueError, match="未知模型配置"):
            QwenOmniAdapter(transport=httpx.MockTransport(lambda r: ok_response(GOOD_CONTENT)),
                            media=StaticMedia(MEDIA), context=StaticContext(rules=RULES),
                            model_config_id="mc-OTHER", base_url=BASE_URL, api_key=API_KEY)

    def test_identity_id_mismatch_rejected_at_construction(self):
        """解析器返回的身份 ID ≠ 请求配置 ID → 构造期 fail-closed（不等运行期）。"""
        mismatched = ModelIdentity(model="qwen3.8-omni-flash", base_url=BASE_URL,
                                   model_config_id="mc-OTHER")  # identity 内部 ID 与查询键不符
        class MismatchContext(StaticContext):
            def identity(self, model_config_id):
                # 对 mc-1 的查询返回内部 ID 为 mc-OTHER 的身份（模拟解析器错位）
                return mismatched if model_config_id == "mc-1" else None
        with pytest.raises(ValueError, match="身份 ID 与请求配置不一致"):
            QwenOmniAdapter(transport=httpx.MockTransport(lambda r: ok_response(GOOD_CONTENT)),
                            media=StaticMedia(MEDIA),
                            context=MismatchContext(rules=RULES),
                            model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)

    def test_empty_key_or_url_rejected(self):
        with pytest.raises(ValueError, match="api_key"):
            QwenOmniAdapter(transport=httpx.MockTransport(lambda r: ok_response(GOOD_CONTENT)),
                            media=StaticMedia(MEDIA), context=StaticContext(rules=RULES),
                            model_config_id="mc-1", base_url=BASE_URL, api_key="")
        with pytest.raises(ValueError, match="base_url"):
            QwenOmniAdapter(transport=httpx.MockTransport(lambda r: ok_response(GOOD_CONTENT)),
                            media=StaticMedia(MEDIA), context=StaticContext(rules=RULES),
                            model_config_id="mc-1", base_url="", api_key=API_KEY)


class TestQueryUnsupported:
    def test_query_request_status_raises_unsupported(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)))
        with pytest.raises(NotImplementedError, match="查询端点"):
            adapter.query_request_status("chatcmpl-any")


class TestRequestShape:
    def test_request_body_and_headers(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["auth"] = request.headers.get("authorization")
            return ok_response(GOOD_CONTENT)

        adapter = make_adapter(httpx.MockTransport(handler))
        adapter.analyze_video("f1", None, "rv-1", "mc-1")
        assert captured["body"]["model"] == "qwen3.8-omni-flash"
        assert captured["body"]["response_format"] == {"type": "json_object"}
        content = captured["body"]["messages"][0]["content"]
        assert content[0]["video_url"]["url"] == MEDIA.remote_reference
        assert "必须口播品牌" in content[1]["text"]
        assert captured["auth"] == f"Bearer {API_KEY}"

    def test_script_text_included(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return ok_response(GOOD_CONTENT)

        ctx = StaticContext(rules=RULES, script=FrozenScript("sv-1", "先说不好喝"))
        adapter = QwenOmniAdapter(transport=httpx.MockTransport(handler),
                                  media=StaticMedia(MEDIA), context=ctx,
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        adapter.analyze_video("f1", "sv-1", "rv-1", "mc-1")
        prompt = captured["body"]["messages"][0]["content"][1]["text"]
        assert "先说不好喝" in prompt

    def test_full_script_included_with_tail_sentinel(self):
        """完整冻结脚本进请求（含尾部哨兵）——不静默截断。"""
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return ok_response(GOOD_CONTENT)

        tail_sentinel = "TAIL_SENTINEL_末尾哨兵_9876543210"
        script_text = "开头部份。" + "脚" * 1960 + tail_sentinel  # 恰在 2000 字预算内含首尾
        ctx = StaticContext(rules=RULES, script=FrozenScript("sv-1", script_text))
        adapter = QwenOmniAdapter(transport=httpx.MockTransport(handler),
                                  media=StaticMedia(MEDIA), context=ctx,
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        adapter.analyze_video("f1", "sv-1", "rv-1", "mc-1")
        prompt = captured["body"]["messages"][0]["content"][1]["text"]
        assert tail_sentinel in prompt  # 尾部哨兵完整进入请求

    def test_script_over_budget_rejected_not_truncated(self):
        """超 2000 字脚本：发送前明确拒——不截断不发送。"""
        sent = []

        def handler(request):
            sent.append(request)
            return ok_response(GOOD_CONTENT)

        long_script = "字" * 2001
        ctx = StaticContext(rules=RULES, script=FrozenScript("sv-1", long_script))
        adapter = QwenOmniAdapter(transport=httpx.MockTransport(handler),
                                  media=StaticMedia(MEDIA), context=ctx,
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        with pytest.raises(ProviderRejectedError, match="超出 prompt 预算"):
            adapter.analyze_video("f1", "sv-1", "rv-1", "mc-1")
        assert sent == []  # 零发送

    def test_script_label_does_not_claim_approved(self):
        """prompt 文案如实：'资格未在本 adapter 验证'——不伪称已通过。"""
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return ok_response(GOOD_CONTENT)

        ctx = StaticContext(rules=RULES, script=FrozenScript("sv-1", "正文"))
        adapter = QwenOmniAdapter(transport=httpx.MockTransport(handler),
                                  media=StaticMedia(MEDIA), context=ctx,
                                  model_config_id="mc-1", base_url=BASE_URL, api_key=API_KEY)
        adapter.analyze_video("f1", "sv-1", "rv-1", "mc-1")
        prompt = captured["body"]["messages"][0]["content"][1]["text"]
        assert "资格未在本 adapter 验证" in prompt
        assert "已通过脚本" not in prompt  # 不伪称 approved


class TestSecretHygiene:
    def test_key_not_in_repr_or_str(self):
        adapter = make_adapter(httpx.MockTransport(lambda req: ok_response(GOOD_CONTENT)))
        assert API_KEY not in repr(adapter)
        assert API_KEY not in str(adapter)

    def test_error_messages_no_url_or_response_leak(self):
        """错误文案固定安全：不透传响应原文/远端 URL。"""
        adapter = make_adapter(httpx.MockTransport(
            lambda req: httpx.Response(200, json={"id": "x", "choices": [
                {"message": {"content": "leak https://secret.example.com/abc token123"}}]})))
        with pytest.raises(InvalidAnalysisStructure) as exc_info:
            adapter.analyze_video("f1", None, "rv-1", "mc-1")
        assert "secret.example.com" not in str(exc_info.value)
        assert "token123" not in str(exc_info.value)
