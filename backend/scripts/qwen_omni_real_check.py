"""t-42：Qwen-Omni 真实连通能力验证脚本（**显式人工触发，永不自动调用**）。

⚠ 真实调用会产生供应商计费：仅在 owner 显式执行（--allow-real-call）时运行。
⚠ 样本文件（视频/规则/脚本）不随仓库分发，需 owner 自备本地路径。

用途：在你已配好 DASHSCOPE_API_KEY 后，由你手动执行一次，验证
qwen3.8-omni-flash 对「完整原视频 + 规则逐条 + 脚本对照」的真实返回质量。

安全与边界（A 2026-10-20 15:08 验收条件）：
- 必须显式 --allow-real-call，否则拒绝执行（缺开关立即退出，非堆栈）
- 密钥只从环境变量 DASHSCOPE_API_KEY 读；**绝不打印、绝不落库、不写日志**
- endpoint 优先 --endpoint，其次环境变量 DASHSCOPE_BASE_URL；缺失清晰退出
- 视频原文件路径不进日志（只显示 basename 与字节数）
- 本地视频经 Base64 data URI 提交（官方 video_url 支持 data:;base64）；
  **编码后必须 < 10MB**（官方硬限），超限直接拒绝，不发送
- 不写数据库、不启动 worker、不改任何服务/路由
- 401/404/429/5xx/超时按既有分层：**不盲重试**；429/5xx/超时=unknown
  （保留 requestId 供人工查询），4xx=明确拒绝
- 输出 JSON 同时保留：① 模型原始 content 原文（人工判断质量用）
  ② adapter 同源解析结果（results/issues/时间戳/理由）③ usage ④ requestId

用法（示例见文件末尾 docstring 或 --help）：
    python backend/scripts/qwen_omni_real_check.py \
        --video samples/a.mp4 --rules samples/rules.json \
        --endpoint https://ws-x.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1 \
        --allow-real-call --out real_check_result.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402

from app.modules.video_model.adapter import InvalidAnalysisStructure  # noqa: E402
from app.modules.video_model.qwen_omni_adapter import (  # noqa: E402
    QWEN_MODEL_DEFAULT,
    FrozenRule,
    FrozenScript,
    _build_prompt,
    _parse_structured,
)

# 官方硬限：Base64 编码串必须小于 10MB
MAX_BASE64_BYTES = 10 * 1024 * 1024
DEFAULT_DURATION_SECONDS = 7200.0  # 未探测时按官方上限，避免误拒合法时间戳
EXIT_USAGE = 2        # 参数/环境缺失
EXIT_REJECTED = 3     # 供应商明确拒绝（4xx）
EXIT_UNKNOWN = 4      # 状态未知（超时/429/5xx）——不盲重试
EXIT_INVALID = 5      # 响应结构非法
EXIT_SENTINEL = 90    # --self-test 结果异常


def _fail(code: int, message: str) -> None:
    """清晰退出：只输出一行可操作信息，不打印堆栈、不泄露密钥/路径。"""
    print(f"[exit {code}] {message}", file=sys.stderr)
    raise SystemExit(code)


def _load_rules(rules_arg: str) -> list[FrozenRule]:
    """规则来源：JSON 文件或内联 JSON 数组或纯文本（每行一条）。"""
    text = rules_arg
    path = Path(rules_arg)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    text = text.strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            _fail(EXIT_USAGE, f"--rules JSON 解析失败：{exc.msg}")
        rules: list[FrozenRule] = []
        for i, item in enumerate(items, 1):
            if isinstance(item, str):
                rules.append(FrozenRule(f"r{i}", item, "其他", "must"))
            elif isinstance(item, dict):
                rid = str(item.get("requirementId") or item.get("id") or f"r{i}")
                rules.append(FrozenRule(
                    rid, str(item.get("text") or ""),
                    str(item.get("category") or "其他"),
                    str(item.get("importance") or "must")))
            else:
                _fail(EXIT_USAGE, "--rules JSON 项必须是字符串或对象")
        return [r for r in rules if r.text.strip()]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return [FrozenRule(f"r{i}", ln, "其他", "must") for i, ln in enumerate(lines, 1)]


def _video_to_data_uri(path: Path) -> tuple[str, int]:
    """本地视频 → Base64 data URI（编码后 <10MB 硬校验；不打印原路径）。"""
    if not path.is_file():
        _fail(EXIT_USAGE, f"视频文件不存在：{path.name}")
    raw = path.read_bytes()
    if not raw:
        _fail(EXIT_USAGE, f"视频文件为空：{path.name}")
    encoded = base64.b64encode(raw)
    if len(encoded) >= MAX_BASE64_BYTES:
        _fail(EXIT_USAGE,
              f"Base64 编码后 {len(encoded)} 字节，超出官方 10MB 上限"
              f"（原始 {len(raw)} 字节）——请改用更短样例")
    return f"data:;base64,{encoded.decode('ascii')}", len(raw)


def run_check(*, video: Path, rules_arg: str, script_file: Path | None,
              model: str, endpoint: str, api_key: str, out: Path,
              duration_seconds: float, transport: httpx.BaseTransport | None = None,
              started: bool = False) -> dict:
    """执行一次真实（或 mock transport 的）能力验证；返回结果 dict。"""
    rules = _load_rules(rules_arg)
    if not rules:
        _fail(EXIT_USAGE, "--rules 未解析出任何规则")
    script = None
    if script_file is not None:
        if not script_file.is_file():
            _fail(EXIT_USAGE, f"脚本文件不存在：{script_file.name}")
        script = FrozenScript("script-cli", script_file.read_text(encoding="utf-8"))

    data_uri, raw_bytes = _video_to_data_uri(video)
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": data_uri}},
                {"type": "text", "text": _build_prompt(rules, script)},
            ],
        }],
        "modalities": ["text"],
        "response_format": {"type": "json_object"},
    }
    print(f"[info] video={video.name} bytes={raw_bytes} rules={len(rules)} "
          f"script={'yes' if script else 'no'} model={model}")

    client = httpx.Client(transport=transport, timeout=300.0, base_url=endpoint)
    try:
        response = client.post("/chat/completions", json=payload,
                               headers={"Authorization": f"Bearer {api_key}"})
    except httpx.ConnectError as exc:
        _fail(EXIT_UNKNOWN, f"连接失败（无法证明请求未发出，按 unknown 处理）：{type(exc).__name__}")
    except httpx.HTTPError as exc:
        _fail(EXIT_UNKNOWN, f"网络异常，提交状态未知，不自动重试：{type(exc).__name__}")

    request_id = None
    try:
        body = response.json()
        if isinstance(body, dict):
            request_id = body.get("id")
    except ValueError:
        body = None

    if response.status_code == 429 or response.status_code >= 500:
        _fail(EXIT_UNKNOWN,
              f"供应商 HTTP {response.status_code}，状态未知不盲重试"
              f"（requestId={request_id or '未返回'}）")
    if response.status_code >= 400:
        _fail(EXIT_REJECTED, f"供应商拒绝 HTTP {response.status_code}（不可重试）")
    if not isinstance(body, dict):
        _fail(EXIT_INVALID, "响应非 JSON 对象")

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _fail(EXIT_INVALID, "响应缺少 choices[0].message.content")
    if not isinstance(content, str):
        _fail(EXIT_INVALID, "响应 content 非字符串")

    # 能力验证关键：原始 content 必须原样保留（不能只留解析结果）
    parsed: dict | None = None
    parse_error: str | None = None
    try:
        parsed = _parse_structured(content, [r.requirement_id for r in rules],
                                   duration_seconds)
    except InvalidAnalysisStructure as exc:
        parse_error = str(exc)

    result = {
        "model": model,
        "endpoint": endpoint,
        "requestId": request_id,
        "usage": body.get("usage"),
        "rawContent": content,                      # 模型原始输出（人工核质量）
        "rawContentBytes": len(content.encode("utf-8")),
        "adapterParsed": (
            None if parsed is None else {
                "results": [r.model_dump(by_alias=True, mode="json") for r in parsed["results"]],
                "issues": [i.model_dump(by_alias=True, mode="json") for i in parsed["issues"]],
            }
        ),
        "adapterParseError": parse_error,
        "integrityNote": "产品结论层仍为保守三 False + INCOMPLETE（本脚本不改变该语义）",
        "videoBytes": raw_bytes,
        "ruleCount": len(rules),
        "selfTest": started,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] requestId={request_id} usage={'有' if body.get('usage') else '无'} "
          f"解析={'成功' if parsed else '失败'} → {out}")
    return result


def self_test(out: Path) -> int:
    """mock transport 全链路自测（不联网、不付费、不读真实密钥）。"""
    fixture_content = json.dumps({
        "results": [{"requirementId": "r1", "verdict": "compliant",
                     "rationale": "自测：口播出现"},
                    {"requirementId": "r2", "verdict": "needs_confirmation",
                     "rationale": "自测：疑似功效"}],
        "issues": [{"requirementId": "r2", "category": "spoken", "importance": "must",
                    "description": "自测：功效表达待确认", "startMs": 1000, "endMs": 2000}],
    }, ensure_ascii=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "chatcmpl-selftest",
            "choices": [{"message": {"content": fixture_content}}],
            "usage": {"prompt_tokens": 11, "prompt_tokens_details": {"audio_tokens": 5}},
        })

    video = out.parent / "_selftest_video.bin"
    video.write_bytes(b"\x00" * 2048)  # 小样例，仅本地自测
    rules = out.parent / "_selftest_rules.json"
    rules.write_text(json.dumps([
        {"requirementId": "r1", "text": "必须口播品牌名", "category": "口播", "importance": "must"},
        {"requirementId": "r2", "text": "禁止功效表达", "category": "禁用表达", "importance": "must"},
    ], ensure_ascii=False), encoding="utf-8")
    try:
        result = run_check(
            video=video, rules_arg=str(rules), script_file=None,
            model=QWEN_MODEL_DEFAULT, endpoint="https://mock.local/compatible-mode/v1",
            api_key="sk-selftest-not-real", out=out, duration_seconds=60.0,
            transport=httpx.MockTransport(handler), started=True)
    finally:
        video.unlink(missing_ok=True)
        rules.unlink(missing_ok=True)

    ok = (result["requestId"] == "chatcmpl-selftest"
          and result["adapterParseError"] is None
          and result["adapterParsed"] is not None
          and len(result["adapterParsed"]["results"]) == 2
          and result["rawContent"] == fixture_content
          and result["usage"] is not None)
    if not ok:
        print("[self-test] FAILED", file=sys.stderr)
        return EXIT_SENTINEL
    print("[self-test] PASS：rawContent 原文保留 / 解析 2 条结果 / usage 与 requestId 齐全")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qwen-Omni 真实连通能力验证（显式触发；不写库/不启 worker）")
    parser.add_argument("--video", type=Path, help="本地视频路径（Base64 后须 <10MB）")
    parser.add_argument("--rules", help="规则：JSON 文件 / 内联 JSON 数组 / 纯文本每行一条")
    parser.add_argument("--script-file", type=Path, default=None, help="可选脚本节选文件")
    parser.add_argument("--model", default=QWEN_MODEL_DEFAULT, help="默认 qwen3.8-omni-flash")
    parser.add_argument("--endpoint", default=None,
                        help="覆盖 DASHSCOPE_BASE_URL（官方 workspace endpoint）")
    parser.add_argument("--out", type=Path, default=Path("real_check_result.json"),
                        help="结果 JSON 输出路径")
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION_SECONDS,
                        help="人工提供的视频时长（用于时间戳校验；未探测时按官方上限）")
    parser.add_argument("--allow-real-call", action="store_true",
                        help="显式确认发起真实付费调用；缺失则拒绝执行")
    parser.add_argument("--self-test", action="store_true",
                        help="mock transport 自测（不联网、不付费）")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.out)
    if not args.allow_real_call:
        _fail(EXIT_USAGE, "缺少 --allow-real-call：本脚本不会在未显式确认时发起真实调用")
    if args.video is None or not args.rules:
        _fail(EXIT_USAGE, "必须提供 --video 与 --rules")
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        _fail(EXIT_USAGE, "环境变量 DASHSCOPE_API_KEY 未设置（密钥不入源码/日志/库）")
    endpoint = (args.endpoint or os.environ.get("DASHSCOPE_BASE_URL", "")).strip()
    if not endpoint:
        _fail(EXIT_USAGE, "缺少 endpoint：请给 --endpoint 或设置 DASHSCOPE_BASE_URL")

    run_check(video=args.video, rules_arg=args.rules, script_file=args.script_file,
              model=args.model, endpoint=endpoint, api_key=api_key, out=args.out,
              duration_seconds=args.duration_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
