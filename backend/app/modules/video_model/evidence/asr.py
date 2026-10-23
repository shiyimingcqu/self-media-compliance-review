"""线路 A：本地 ASR 音频证据通道（第一阶段：只加证据，不参与 verdict）。

设计约束（与 docs/VIDEO_ASR_EVIDENCE 方案一致）：
- 确定性优先：转写只是证据，required_speech / forbidden_expression 的结论仍由
  component_runtime 计算；本模块不改写任何 verdict。
- 不伪造：依赖缺失、无音轨、抽音频失败、转写异常，一律返回 available=False 并给出
  真实原因；绝不产生空 segments 冒充"听完了"。
- 不新增付费调用：faster-whisper 本地推理，零边际成本；运行一次即缓存进 manifest。
- 完整性可核对：coverageMs/gapsMs 让"是否覆盖了全片音轨"第一次成为结构属性，
  而不是模型的 coverage_complete 布尔自述。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MAX_SEGMENTS = 400
MAX_SEGMENT_TEXT_CHARS = 2_000
DEFAULT_MAX_AUDIO_SECONDS = 610  # 与冻结组件参数（视频 ≤600s）保持同一上界


def enabled() -> bool:
    """本地 ASR 默认关闭：实测转写质量不足以支撑判定，口播改为依赖模型听到的原声音轨与画面字幕。

    REVIEW_VIDEO_ASR=on 可重新启用（仍需安装 backend/requirements-asr.txt）。
    """
    return os.getenv('REVIEW_VIDEO_ASR', 'off').strip().lower() in ('on', '1', 'true', 'yes')


def unavailable(reason: str, *, source_sha256: str | None = None) -> dict:
    """显式不可用；消费方（人工复核页）据此显示"音频证据未就绪"，不得当作无问题。"""
    return {"available": False, "reason": reason, "segments": [], "coverageMs": [], "gapsMs": [],
            "hasAudio": False, "truncated": False, "durationMs": None, "language": None,
            "sourceSha256": source_sha256, "engine": None}


def merge(intervals: list[list[int]]) -> list[list[int]]:
    result: list[list[int]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def gaps(coverage: list[list[int]], duration_ms: int) -> list[list[int]]:
    """音轨时长内未被任何转写段覆盖的区间——这就是断档，也是 coverageVerified 未来置真的前提。"""
    result, cursor = [], 0
    for start, end in coverage:
        if start > cursor:
            result.append([cursor, start])
        cursor = max(cursor, end)
    if duration_ms > cursor:
        result.append([cursor, duration_ms])
    return result


def probe_duration_ms(video: Path, ffmpeg: str) -> int | None:
    import re
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(video)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not match:
        return None
    h, m, s = map(float, match.groups())
    return int((h * 3600 + m * 60 + s) * 1000)


def extract_audio(video: Path, wav: Path, ffmpeg: str, max_seconds: int) -> bool:
    """整条音轨无损抽取为 16kHz 单声道 wav；-t 截断由调用方按原片时长标记，不静默丢弃。"""
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-t", str(max_seconds), "-c:a", "pcm_s16le", str(wav)],
        capture_output=True, timeout=300)
    return completed.returncode == 0 and wav.exists() and wav.stat().st_size > 0


def load_engine():
    """懒加载本地引擎；未安装 faster-whisper 时显式失败，由 transcribe 降级。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    return WhisperModel("base", device="cpu", compute_type="int8")


def transcribe(video: Path, ffmpeg: str, *, source_sha256: str, engine=None,
               output_dir: Path | None = None, language: str | None = None) -> dict:
    """转写整条音轨，返回带区间/断档的证据结构；任何失败都是 available=False + 真实原因。

    engine 可注入（测试传假引擎即可，无需模型权重）；默认懒加载 faster-whisper。
    vad_filter 必开：真机实测证明 base 模型对纯音乐/含糊音轨会成段幻觉重复文本，
    VAD 把非语音区划入断档，让 gapsMs 成为"未检测到人声"而非"未转写"的诚实信号。
    language 可提示（中文内容建议 "zh"）；未给定时读 ASR_LANGUAGE 环境变量，仍无则自动检测，
    自动检测置信度会记录在 detectedLanguage，供消费方判断转写可信度。
    """
    import os
    language = language or os.environ.get("ASR_LANGUAGE") or None
    video = Path(video)
    try:
        duration_ms = probe_duration_ms(video, ffmpeg)
        if duration_ms is None:
            return unavailable("无法读取视频时长，音频证据未生成", source_sha256=source_sha256)
        if engine is None:
            engine = load_engine()
            if engine is None:
                return unavailable("本地 ASR 依赖未安装（faster-whisper）；安装 backend/requirements-asr.txt 后重新发起审核",
                                   source_sha256=source_sha256)
        wav = (output_dir or video.parent) / f"{video.stem}-audio-{source_sha256[:12]}.wav"
        if not extract_audio(video, wav, ffmpeg, DEFAULT_MAX_AUDIO_SECONDS):
            wav.unlink(missing_ok=True)
            return unavailable("抽取音轨失败或视频不含音轨；口播与禁用词检查以人工核对为准",
                               source_sha256=source_sha256)
        audio_ms = probe_duration_ms(wav, ffmpeg) or 0
        truncated = duration_ms > DEFAULT_MAX_AUDIO_SECONDS * 1000
        try:
            # faster-whisper 的真实返回顺序是 (segments, info)；此处曾有反序缺陷，由真机端到端测试发现。
            segments, info = engine.transcribe(str(wav), language=language, vad_filter=True, beam_size=5)
        finally:
            wav.unlink(missing_ok=True)
        items = []
        for segment in segments:
            if len(items) >= MAX_SEGMENTS:
                truncated = True
                break
            text = (segment.text or "").strip()
            if not text:
                continue
            start = max(0, min(audio_ms - 1, int(round(segment.start * 1000))))
            end = min(audio_ms, max(start + 1, int(round(segment.end * 1000))))
            items.append({"startMs": start, "endMs": end, "text": text[:MAX_SEGMENT_TEXT_CHARS],
                          "confidence": round(max(0.0, min(1.0, 1 - float(getattr(segment, "no_speech_prob", 0.0) or 0.0))), 3)})
        coverage = merge([[s["startMs"], s["endMs"]] for s in items])
        result = {"available": True, "reason": None, "segments": items, "coverageMs": coverage,
                  "gapsMs": gaps(coverage, audio_ms), "hasAudio": True, "truncated": truncated,
                  "durationMs": audio_ms, "language": getattr(info, "language", None),
                  "languageProbability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
                  "languageHint": language,
                  "sourceSha256": source_sha256,
                  "engine": {"name": "faster-whisper", "model": "base", "device": "cpu", "computeType": "int8", "vadFilter": True},
                  "segmentCap": MAX_SEGMENTS, "maxAudioSeconds": DEFAULT_MAX_AUDIO_SECONDS}
        if truncated:
            result["note"] = "音轨超出本地处理上限被截断；断档不代表未发声，结论须保持待确认"
        elif language is None and (result["languageProbability"] or 0) < 0.5:
            result["note"] = "语言自动检测置信度低，转写文本可能不可靠；口播与禁用词判定须人工核对，或设 ASR_LANGUAGE 后重跑"
        return result
    except Exception as exc:
        # 不回显路径细节之外的堆栈；原因保持可读、可判（与工具失败信息同风格）。
        return unavailable(f"音频证据生成失败（{type(exc).__name__}）；不伪造转写结果", source_sha256=source_sha256)
