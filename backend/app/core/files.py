"""受控文件存储原语：服务端生成 key、流式计数限额、签名双验（不等同完整解码/可解析性）、Range 解析。"""

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

PURPOSE_EXTENSIONS = {
    "brief": {"pdf", "doc", "docx", "txt"},
    "script": {"doc", "docx", "xls", "xlsx", "txt", "pdf"},
    "video": {"mp4", "mov", "avi"},
}

MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
}


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    storage_key: str
    byte_size: int
    sha256: str
    signature_state: str  # verified | unverifiable；mismatch 不落库直接 415


def sanitize_original_name(raw: str) -> str:
    """只作为展示元数据；存储名一律服务端生成。剥离路径成分与控制字符。"""
    name = raw.replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))
    name = name[:120] if name else "unnamed"
    return name


def extension_of(original_name: str) -> str:
    base, dot, ext = sanitize_original_name(original_name).rpartition(".")
    return ext.lower() if dot and base else ""


def check_signature(extension: str, head: bytes) -> str:
    """扩展名与文件头双验。mismatch 由调用方转 415。txt 无稳定签名，标记 unverifiable 而非伪造 verified。"""
    if extension == "pdf":
        return "verified" if head.startswith(b"%PDF-") else "mismatch"
    if extension in {"docx", "xlsx"}:
        return "verified" if head.startswith(b"PK\x03\x04") else "mismatch"
    if extension in {"doc", "xls"}:
        return "verified" if head[:8] == bytes.fromhex("d0cf11e0a1b11ae1") else "mismatch"
    if extension in {"mp4", "mov"}:
        return "verified" if head[4:8] == b"ftyp" else "mismatch"
    if extension == "avi":
        return "verified" if head[:4] == b"RIFF" and head[8:12] == b"AVI " else "mismatch"
    if extension == "txt":
        return "unverifiable"
    return "mismatch"


class StreamCounter:
    """分块写入并计数限流的存储原语：调用方按块 feed，超限抛 OverflowError 并清理临时件。
    不把大文件读入内存；供服务层与后续受限转换器复用。"""

    def __init__(self, target_dir: Path, file_id: str, extension: str, max_bytes: int | None):
        target_dir.mkdir(parents=True, exist_ok=True)
        self._temp = target_dir / f"{file_id}.part"
        self._final = target_dir / f"{file_id}.{extension}" if extension else target_dir / file_id
        self._max = max_bytes
        self._digest = hashlib.sha256()
        self._total = 0
        self._head = b""
        self._sink = open(self._temp, "wb")

    def feed(self, chunk: bytes) -> None:
        self._total += len(chunk)
        if self._max is not None and self._total > self._max:
            self.abort()
            raise OverflowError(self._total)
        if len(self._head) < 16:
            self._head = (self._head + chunk)[:16]
        self._digest.update(chunk)
        self._sink.write(chunk)

    @property
    def head(self) -> bytes:
        return self._head

    @property
    def byte_size(self) -> int:
        return self._total

    def abort(self) -> None:
        if not self._sink.closed:
            self._sink.close()
        self._temp.unlink(missing_ok=True)

    def finish(self, file_id: str) -> StoredFile:
        self._sink.close()
        os.replace(self._temp, self._final)
        return StoredFile(file_id=file_id, storage_key=str(self._final), byte_size=self._total,
                          sha256=self._digest.hexdigest(), signature_state="pending")


def parse_range(header: str | None, total: int) -> tuple[int, int] | None | str:
    """返回 (start, end_inclusive)=有效单段；None=无 Range 或应忽略的畸形值；'unsatisfiable'=416。"""
    if not header:
        return None
    match = RANGE_RE.match(header.strip())
    if not match or "," in header:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    if not start_text:
        length = int(end_text)
        if length <= 0:
            return None
        start = max(total - length, 0)
        return (start, total - 1) if total else "unsatisfiable"
    start = int(start_text)
    if start >= total:
        return "unsatisfiable"
    end = int(end_text) if end_text else total - 1
    if end_text and end < start:
        return None
    return (start, min(end, total - 1))
