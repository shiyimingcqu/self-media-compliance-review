"""显式启动完整视频工具审查，不写业务库，不自动重试收费请求。"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-real-call", action="store_true")
    parser.add_argument("--mock", action="store_true", help="只验证工具循环，非内容审核")
    args = parser.parse_args()
    if not args.mock and not args.allow_real_call:
        parser.error("真实模式需显式 --allow-real-call；最多4次模型请求，可能计费")
    if args.mock and args.allow_real_call:
        parser.error("--mock 与 --allow-real-call 不能同时使用")
    # 本地取证不向追踪服务上传视频/规则/脚本。
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    from app.modules.video_model.evidence.tools import EvidenceSession
    from app.modules.video_model.evidence.agent import run_agent
    import imageio_ffmpeg
    rules = json.loads(args.rules.read_text(encoding="utf-8-sig"))
    session = EvidenceSession(video=args.video, output_dir=args.output,
                              ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(), rules=rules,
                              script=args.script.read_text(encoding="utf-8-sig") if args.script else "")
    calls = 0

    def mock_complete(messages, tools):
        nonlocal calls
        calls += 1
        if calls == 1:
            instructions = [
                ("capture_evidence_image", {"timestamp_ms": 0, "issue_id": "mock-1"}),
                ("cut_evidence_clip", {"start_ms": 0, "end_ms": min(2000, session.duration_ms), "issue_id": "mock-1"}),
            ]
            return {"choices": [{"message": {"role": "assistant", "tool_calls": [
                {"id": f"mock-call-{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(values)}}
                for i, (name, values) in enumerate(instructions)]}}]}
        findings = [{"id": "mock-1" if i == 0 else f"mock-{i+1}", "rule_id": r["id"],
                     "verdict": "needs_confirmation", "description": "模拟流程，不构成实际内容审核",
                     "evidence_ids": [a["id"] for a in session.artifacts] if i == 0 else []}
                    for i, r in enumerate(rules)]
        return {"choices": [{"message": {"content": json.dumps({"summary": "工具流程演示", "findings": findings})}}]}

    if args.mock:
        result = run_agent(session, mock_complete, max_inline_bytes=20_000_000)
    else:
        import httpx
        from app.core.contracts import ProviderExecution
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        endpoint = os.environ.get("REVIEW_PROVIDER_ENDPOINT_BASE", "")
        model = os.environ.get("REVIEW_PROVIDER_MODEL_ID", "qwen3.8-omni-flash")
        if not key or not endpoint:
            raise ValueError("请在本机环境设置 DASHSCOPE_API_KEY 和 REVIEW_PROVIDER_ENDPOINT_BASE")
        config = ProviderExecution(model_id=model, endpoint_base=endpoint, config_version="evidence-cli-v1")
        # 仅批准的百炼 HTTPS origin，不允许任意地址读取凭证或接收业务视频。
        from urllib.parse import urlsplit
        from app.modules.video_model.evidence.console_schemas import ENDPOINTS
        if config.endpoint_base.rstrip("/") not in ENDPOINTS:
            raise ValueError("服务地址不在本地百炼白名单，请先验证地域与地址后扩展适配器")
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            def complete(messages, tools):
                response = client.post(config.endpoint_base.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + key},
                                       json={"model": config.model_id, "messages": messages, "tools": tools,
                                             "stream": False, "modalities": ["text"]})
                if response.status_code != 200:
                    raise RuntimeError(f"供应商调用未成功（HTTP {response.status_code}），未自动重试")
                return response.json()
            result = run_agent(session, complete)
    result["mode"] = "demo" if args.mock else "real"
    destination = session.output_dir / "report.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": result["mode"], "artifactCount": len(session.artifacts),
                      "conclusion": result["conclusion"], "report": str(destination)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 不打印原始模型响应、URL、请求头或包含Base64的视频输入。
        print(json.dumps({"error": "EVIDENCE_REVIEW_FAILED", "type": type(exc).__name__,
                          "message": "执行失败；没有自动重试。检查配置、输入大小、依赖及证据清单。"}), file=sys.stderr)
        raise SystemExit(1)
