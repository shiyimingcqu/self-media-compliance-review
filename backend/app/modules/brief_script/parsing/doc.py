"""真实格式（XLSX/DOCX）共用的解析产物与压缩包安全门（t-38）。

设计要点（A 12:51/13:01 收口）：
- location 精确到**真实单元格/行列**：XLSX=`sheet!D12`（每非空 cell 一个块，口播 D12 就指 D12）、
  DOCX 段落 `paragraph:N`、表格单元格 `table:T:rRcC`。绝不用行首单元格冒充整行。
- 未识读图片不吞且**不重复计数**：WPS 同一张图既有 media 文件又有 cellimages 引用时，按"一个媒体
  + 其引用位置"表述（dedupe by 目标文件）。
- coverage 三态：full（文本完整）/ partial（有未纳入内容）/ unknown（历史行缺元数据，不补 false）。
- 压缩包安全门：成员数上限、单成员解压上限、总解压上限、路径穿越拒绝（openpyxl 官方明确默认
  不防御 XML 炸弹，需 defusedxml；此处再叠 zip 层边界）。
"""

from dataclasses import dataclass, field
import zipfile

# 压缩包边界：防 zip 炸弹/成员爆炸（真实脚本/Brief 远小于此）
MAX_MEMBERS = 2_000
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024

COVERAGE_FULL = "full"
COVERAGE_PARTIAL = "partial"
COVERAGE_UNKNOWN = "unknown"


class DocFormatError(Exception):
    """真实格式解析失败（固定安全文案，不回显本地路径/内容）。"""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


@dataclass(frozen=True)
class DocBlock:
    paragraph_id: str      # p1..pN（与 TXT 一致的对外段号）
    ordinal: int
    text: str
    location: str          # 精确结构化定位：如 "脚本!D12" / "paragraph:12" / "table:0:r2c1"


@dataclass
class ParsedDoc:
    blocks: list[DocBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uncovered_media: list[str] = field(default_factory=list)  # 去重后的"媒体 + 引用位置"
    uncovered_other: list[str] = field(default_factory=list)  # 非图片类未覆盖（无缓存公式/超限截断等）
    partial: bool = False                                     # 有未覆盖内容 → True
    coverage: str = COVERAGE_FULL                             # full / partial（引擎侧）

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


def open_zip_guarded(payload: bytes) -> zipfile.ZipFile:
    """带边界的 zip 打开：成员数/解压大小/路径穿越全部前置校验。"""
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise DocFormatError("EXTRACTION_FAILED", "文件不是有效的 OOXML 压缩包") from None
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise DocFormatError("EXTRACTION_FAILED", "压缩包成员数量超出安全上限")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
            raise DocFormatError("EXTRACTION_FAILED", "压缩包包含非法成员路径")
        if info.file_size > MAX_MEMBER_BYTES:
            raise DocFormatError("EXTRACTION_FAILED", "压缩包单个成员解压体积超出安全上限")
        total += info.file_size
    if total > MAX_TOTAL_BYTES:
        raise DocFormatError("EXTRACTION_FAILED", "压缩包解压总量超出安全上限")
    return zf


def safe_xml(zf: zipfile.ZipFile, name: str):
    """用 defusedxml 解析成员 XML（防实体膨胀/外部实体），不存在则返回 None。"""
    try:
        data = zf.read(name)
    except KeyError:
        return None
    from defusedxml.ElementTree import fromstring

    try:
        return fromstring(data)
    except Exception:
        raise DocFormatError("EXTRACTION_FAILED", "OOXML 结构无法解析或含不安全实体") from None


def finalize_media(doc: ParsedDoc) -> None:
    """收尾：按"媒体去重 + 引用位置"表述未覆盖图片，并置 coverage。

    **不抹掉已有 partial 原因**：解析器可能因无缓存公式、超限截断等先置 partial=True，
    这里只做追加，不得把它重置成 bool(media)。
    """
    doc.uncovered_media = list(dict.fromkeys(doc.uncovered_media))
    doc.uncovered_other = list(dict.fromkeys(doc.uncovered_other))
    if doc.uncovered_media or doc.uncovered_other:
        doc.partial = True                       # 有任一未覆盖即 partial
    doc.coverage = COVERAGE_PARTIAL if doc.partial else COVERAGE_FULL


def mark_partial(doc: ParsedDoc, reason: str) -> None:
    """记录非图片类未覆盖原因（无缓存公式、超限截断等），保持 partial 不被后续覆盖。"""
    doc.partial = True
    if reason not in doc.uncovered_other:
        doc.uncovered_other.append(reason)
