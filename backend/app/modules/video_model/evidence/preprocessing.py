"""上传后全片转码：保留原件/时序/音轨，模型副本与原件取证分离。"""
import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path
from uuid import uuid4

from app.core.errors import ApiError
from app.modules.foundation.files_service import get_asset

PRESETS = {"balanced": (1280, 23), "compact": (960, 26)}
_lock = threading.Lock()


def probe(path: Path, ffmpeg: str) -> dict:
    result = subprocess.run([ffmpeg, '-hide_banner', '-i', str(path)], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=20)
    duration = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', result.stderr)
    stream = next((line for line in result.stderr.splitlines() if 'Video:' in line and 'Stream ' in line), '')
    size = re.search(r'\b(\d{2,5})x(\d{2,5})\b', stream)
    fps = re.search(r'([\d.]+) fps', stream)
    if not duration or not size:
        raise ApiError(422, 'VIDEO_PROBE_FAILED', '无法读取视频时长或尺寸')
    h, m, s = map(float, duration.groups())
    return {'durationMs': round((h * 3600 + m * 60 + s) * 1000), 'width': int(size[1]),
            'height': int(size[2]), 'fps': float(fps[1]) if fps else None,
            'audioTracks': sum('Stream ' in line and 'Audio:' in line for line in result.stderr.splitlines())}


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare_video(settings, file_id: str, preset: str = 'balanced', *, max_inline_bytes: int | None = None) -> tuple[dict, Path]:
    if max_inline_bytes is None:
        from .console_service import settings_view
        max_inline_bytes = settings_view(settings).max_inline_bytes
    if preset not in {*PRESETS, 'original'}:
        raise ApiError(400, 'VALIDATION_ERROR', '未知压缩档位')
    asset, original = get_asset(settings, file_id)
    if asset.purpose != 'video' or asset.extension != 'mp4':
        raise ApiError(415, 'UNSUPPORTED_MEDIA', '当前压缩入口只支持完整 MP4')
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    root = settings.data_dir / 'prepared-videos' / file_id / preset
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / 'metadata.json'
    with _lock:
        source_hash = digest(original)
        if source_hash != asset.sha256:
            raise ApiError(409, 'SOURCE_CHANGED', '原视频哈希不一致，已停止处理')
        output = original if preset == 'original' else root / 'review.mp4'
        if manifest.exists() and output.exists():
            cached = json.loads(manifest.read_text(encoding='utf-8'))
            if cached['sourceSha256'] == source_hash and cached['preparedSha256'] == digest(output):
                cached['maxInlineBytes'] = max_inline_bytes
                cached['canSend'] = max_inline_bytes is None or output.stat().st_size <= max_inline_bytes
                return cached, output
        before = probe(original, ffmpeg)
        if before['durationMs'] <= 0 or before['durationMs'] > 600_000:
            raise ApiError(422, 'VIDEO_TOO_LONG', '本地压缩仅支持10分钟内视频，不会裁短原片')
        if preset != 'original':
            edge, crf = PRESETS[preset]
            temporary = root / (uuid4().hex + '.mp4')
            # 不使用 -r、fps 滤镜、-ss 或 -t；passthrough 保持输入帧时间戳。
            scale = f"scale=w='min({edge},iw)':h='min({edge},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
            command = [ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(original), '-map', '0:v:0',
                       '-map', '0:a?', '-vf', scale, '-fps_mode', 'passthrough', '-c:v', 'libx264',
                       '-preset', 'fast', '-crf', str(crf), '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                       '-b:a', '96k', '-movflags', '+faststart', str(temporary)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=180)
                if result.returncode or not temporary.exists():
                    raise ApiError(422, 'COMPRESSION_FAILED', '视频转码失败，原件保留')
                after = probe(temporary, ffmpeg)
                if abs(before['durationMs'] - after['durationMs']) > 250 or before['audioTracks'] != after['audioTracks']:
                    raise ApiError(422, 'COMPRESSION_INTEGRITY_FAILED', '副本时长或音轨数量与原片不一致，已停止使用')
                temporary.replace(output)
            except subprocess.TimeoutExpired:
                raise ApiError(422, 'COMPRESSION_TIMEOUT', '本地压缩超时，原件保留；未调用模型') from None
            finally:
                temporary.unlink(missing_ok=True)
        after = probe(output, ffmpeg)
        metadata = {'fileId': file_id, 'preset': preset, 'originalBytes': asset.byte_size,
                    'preparedBytes': output.stat().st_size, 'original': before, 'prepared': after,
                    'sourceSha256': source_hash, 'preparedSha256': digest(output),
                    'canSend': max_inline_bytes is None or output.stat().st_size <= max_inline_bytes,
                    'timelinePreserved': True, 'frameRatePolicy': 'passthrough',
                    'evidenceFromOriginal': True, 'maxInlineBytes': max_inline_bytes}
        manifest.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
        return metadata, output
