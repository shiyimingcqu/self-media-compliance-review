import json
import subprocess

import pytest

from app.modules.video_model.evidence import asr
from app.modules.video_model.evidence.tools import EvidenceSession


class FakeSegment:
    def __init__(self, start, end, text, no_speech_prob=0.0):
        self.start, self.end, self.text, self.no_speech_prob = start, end, text, no_speech_prob


class FakeInfo:
    language = "zh"
    language_probability = 0.97


class FakeEngine:
    """契约对齐 faster-whisper：transcribe 返回 (segments, info)，接收 language/vad_filter/beam_size。"""
    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        return [FakeSegment(*s) for s in self.segments], FakeInfo()


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """probe 返回 10 秒时长；抽取 wav / 渲染证据时落假文件。"""
    def run(args, **kwargs):
        target = str(args[-1])
        if "-vn" in args and target.endswith(".wav"):
            from pathlib import Path
            Path(target).write_bytes(b"pcm")
            return subprocess.CompletedProcess(args, 0, "", "")
        if "-frames:v" in args or "-c:v" in args:
            from pathlib import Path
            Path(target).write_bytes(b"evidence")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(args, 0, "", "Duration: 00:00:10.00\n Stream #0:1: Audio: aac")
    monkeypatch.setattr(subprocess, "run", run)


def test_merge_and_gap_pure():
    assert asr.merge([[200, 500], [100, 300], [700, 900]]) == [[100, 500], [700, 900]]
    assert asr.gaps([[100, 500], [700, 900]], 1200) == [[0, 100], [500, 700], [900, 1200]]
    assert asr.gaps([[0, 1200]], 1200) == []


def test_transcribe_produces_verifiable_track(fake_ffmpeg, tmp_path):
    engine = FakeEngine([(0.1, 2.0, "开场口播麦咖啡"), (2.0, 4.5, "展示产品包装"), (4.5, 6.0, "  "), (8.0, 9.0, "结尾推荐")])
    track = asr.transcribe(tmp_path / "video.mp4", "ffmpeg", source_sha256="a" * 64,
                           engine=engine, output_dir=tmp_path)
    assert track["available"] is True and track["hasAudio"] is True and track["truncated"] is False
    # 空文本段被丢弃，其余按毫秒区间保留并裁剪到音轨时长内
    assert [s["startMs"] for s in track["segments"]] == [100, 2000, 8000]
    assert track["coverageMs"] == [[100, 4500], [8000, 9000]]
    # 断档是结构属性：6.0-8.0s 之间没有任何转写
    assert [5000, 8000] in track["gapsMs"] or [4500, 8000] in track["gapsMs"]
    assert track["sourceSha256"] == "a" * 64 and track["engine"]["name"] == "faster-whisper"
    assert not (tmp_path / "video-audio-aaaaaaaaaaaa.wav").exists()  # 中间 wav 用后即删


def test_transcribe_without_engine_is_explicit_unavailable(fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "load_engine", lambda: None)
    track = asr.transcribe(tmp_path / "video.mp4", "ffmpeg", source_sha256="b" * 64, output_dir=tmp_path)
    assert track["available"] is False and "faster-whisper" in track["reason"]
    assert track["segments"] == [] and track["gapsMs"] == []


def test_transcribe_extraction_failure_is_honest(fake_ffmpeg, tmp_path, monkeypatch):
    def failing_extract(*args, **kwargs):
        return False
    monkeypatch.setattr(asr, "extract_audio", failing_extract)
    track = asr.transcribe(tmp_path / "video.mp4", "ffmpeg", source_sha256="c" * 64,
                           engine=FakeEngine([(0, 1, "x")]), output_dir=tmp_path)
    assert track["available"] is False and "音轨" in track["reason"]


def test_transcribe_engine_crash_never_raises(fake_ffmpeg, tmp_path):
    class Boom:
        def transcribe(self, path):
            raise RuntimeError("model exploded")
    track = asr.transcribe(tmp_path / "video.mp4", "ffmpeg", source_sha256="d" * 64,
                           engine=Boom(), output_dir=tmp_path)
    assert track["available"] is False and "不伪造" in track["reason"]


def test_segment_cap_marks_truncated(fake_ffmpeg, tmp_path):
    engine = FakeEngine([(i * 0.1, i * 0.1 + 0.09, f"词{i}") for i in range(asr.MAX_SEGMENTS + 5)])
    track = asr.transcribe(tmp_path / "video.mp4", "ffmpeg", source_sha256="e" * 64,
                           engine=engine, output_dir=tmp_path)
    assert len(track["segments"]) == asr.MAX_SEGMENTS and track["truncated"] is True
    assert "上限" in track["note"]


def test_session_manifest_carries_audio_track(tmp_path, fake_ffmpeg):
    video = tmp_path / "original.mp4"
    video.write_bytes(b"test-original")
    track = asr.unavailable("依赖未安装", source_sha256="f" * 64)
    session = EvidenceSession(video=video, output_dir=tmp_path / "output", ffmpeg="ffmpeg",
                              rules=[{"id": "r1", "text": "产品露出"}], audio_track=track)
    session.screenshot(1000, "issue-1")
    manifest = json.loads((session.output_dir / "manifest.json").read_text())
    assert manifest["audioTrack"]["available"] is False
    assert manifest["audioTrack"]["reason"] == "依赖未安装"


def test_local_asr_is_off_by_default(monkeypatch):
    """用户反馈本地转写质量不足以支撑判定，默认不开；REVIEW_VIDEO_ASR=on 才启用。"""
    monkeypatch.delenv('REVIEW_VIDEO_ASR', raising=False)
    assert asr.enabled() is False
    for value in ('on', '1', 'true', 'yes'):
        monkeypatch.setenv('REVIEW_VIDEO_ASR', value)
        assert asr.enabled() is True
    monkeypatch.setenv('REVIEW_VIDEO_ASR', 'off')
    assert asr.enabled() is False
