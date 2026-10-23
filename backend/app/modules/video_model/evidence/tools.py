from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


class ScreenshotInput(ToolInput):
    timestamp_ms: int = Field(ge=0, strict=True)
    issue_id: str = Field(min_length=1, max_length=100)


class ClipInput(ToolInput):
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(gt=0, strict=True)
    issue_id: str = Field(min_length=1, max_length=100)


class RuleInput(ToolInput):
    rule_id: str = Field(min_length=1, max_length=100)


class EvidenceSession:
    """受信调用方冻结文件和规则；模型不能提供路径、URL、shell 或转码参数。"""

    def __init__(self, *, video: Path, output_dir: Path, ffmpeg: str,
                 rules: list[dict[str, Any]], script: str = "", max_clip_ms: int = 30_000, references: list[dict] | None=None,
                 audio_track: dict[str, Any] | None = None):
        self.video = video.resolve(strict=True)
        if not self.video.is_file():
            raise ValueError("视频文件不可读")
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = ffmpeg
        # 深复制冻结输入，避免外部后续修改规则/来源影响正在进行的取证。
        self.rules = {r["id"]: r for r in json.loads(json.dumps(rules))}
        if not self.rules or len(self.rules) != len(rules):
            raise ValueError("必须提供不重复的冻结规则 ID")
        self.script = script
        self.max_clip_ms = max_clip_ms
        self.artifacts: list[dict[str, Any]] = []
        self.evidence_references: list[dict[str, str]] = []
        self.calls: list[dict[str, Any]] = []
        self.references=references or []
        self.component_results: dict[tuple,dict]={}
        # 第一阶段：音频证据通道结果只作为冻结证据挂载（由受信调用方在运行前转写一次），
        # 不参与 build_tools、不改变任何组件 verdict；为后续 ASR 对照判定预留数据。
        self.audio_track = audio_track
        self._cache: dict[str, dict] = {}
        with self.video.open("rb") as source:
            self.sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        # FFmpeg progress 输出真实容器解码时长；仅探测，不导出图片。
        probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(self.video)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
        import re
        match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
        if not match:
            raise ValueError("无法读取真实视频时长")
        h, m, s = map(float, match.groups())
        self.duration_ms = int((h * 3600 + m * 60 + s) * 1000)
        self.has_audio = bool(re.search(r"Stream .*Audio:", probe.stderr))

    def metadata(self) -> dict:
        return {"sourceSha256": self.sha256, "durationMs": self.duration_ms, "hasAudio": self.has_audio,
                "byteSize": self.video.stat().st_size}

    def rule_source(self, rule_id: str) -> dict:
        if rule_id not in self.rules:
            raise ValueError("规则不属于本次审核")
        return self.rules[rule_id]

    def approved_script(self) -> dict:
        return {"available": bool(self.script), "text": self.script}

    def screenshot(self, timestamp_ms: int, issue_id: str) -> dict:
        if not 0 <= timestamp_ms < self.duration_ms:
            raise ValueError("截图时间超出视频范围")
        return self._render("image", issue_id, timestamp_ms, timestamp_ms)

    def clip(self, start_ms: int, end_ms: int, issue_id: str) -> dict:
        if not 0 <= start_ms < end_ms <= self.duration_ms or end_ms - start_ms > self.max_clip_ms:
            raise ValueError("片段须在视频范围内，且不超过 30 秒")
        return self._render("clip", issue_id, start_ms, end_ms)

    def _render(self, kind: str, issue: str, start: int, end: int) -> dict:
        cache_key = json.dumps([kind, issue, start, end])
        if cache_key in self._cache:
            return self._cache[cache_key]
        limit = 4 if kind == "image" else 3
        if sum(a["kind"] == kind and a["issueId"] == issue for a in self.artifacts) >= limit:
            raise ValueError("该问题已达到取证数量上限")
        if len(self.artifacts) >= 60:
            raise ValueError("本次审核已达到证据总量上限")
        identity = uuid4().hex
        filename = identity + (".jpg" if kind == "image" else ".mp4")
        path = self.output_dir / filename
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(self.video),
                "-ss", f"{start / 1000:.3f}"]
        if kind == "image":
            args += ["-frames:v", "1", "-q:v", "2"]
        else:
            args += ["-t", f"{(end - start) / 1000:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                     "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart"]
        try:
            completed = subprocess.run([*args, str(path)], capture_output=True, timeout=60)
            if completed.returncode or not path.exists() or path.stat().st_size == 0:
                raise ValueError("证据生成失败，请重新定位时间")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        artifact = {"id": identity, "kind": kind, "issueId": issue, "startMs": start, "endMs": end,
                    "filename": filename, "sourceSha256": self.sha256, "hasAudio": self.has_audio if kind == "clip" else False}
        self.artifacts.append(artifact)
        self._cache[cache_key] = artifact
        self.save_manifest()
        return artifact

    def save_manifest(self):
        destination = self.output_dir / "manifest.json"
        temporary = self.output_dir / "manifest.tmp"
        temporary.write_text(json.dumps({"source": self.metadata(), "artifacts": self.artifacts,
                                         "evidenceReferences": self.evidence_references,
                                         "componentChecks":[{k:v for k,v in item.items() if not k.startswith('_')} for item in self.component_results.values()],
                                         "audioTrack": self.audio_track,
                                         "toolCalls": self.calls}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)


def build_tools(session: EvidenceSession):
    from langchain_core.tools import StructuredTool
    from .component_runtime import component_tools
    return [
        StructuredTool.from_function(session.metadata, name="get_video_metadata", args_schema=EmptyInput,
                                     description="读取当前原视频真实时长、音轨和来源哈希。"),
        StructuredTool.from_function(session.screenshot, name="capture_evidence_image", args_schema=ScreenshotInput,
                                     description="在完整视频审查发现问题后，按指定毫秒时刻截图取证，每问题最多两张。截图不代替全片审核。"),
        StructuredTool.from_function(session.clip, name="cut_evidence_clip", args_schema=ClipInput,
                                     description="裁取原视频有声片段，保留问题上下文；区间单位毫秒，最长30秒，每问题一段。"),
        StructuredTool.from_function(session.rule_source, name="get_rule_source", args_schema=RuleInput,
                                     description="获取当前冻结规则的要求、Brief原文及适用范围，不允许查询其他任务规则。"),
        StructuredTool.from_function(session.approved_script, name="get_approved_script", args_schema=EmptyInput,
                                     description="获取受信调用方提供的已通过脚本；未提供时明确不可用。"),
    ] + component_tools(session)
