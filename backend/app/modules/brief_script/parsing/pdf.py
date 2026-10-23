"""PDF Brief 真实解析（t-40）：pypdf 纯本地抽文本层，给**真实物理页号**与稳定段落。

设计约束（A 14:01）：
- **本地**解析，绝不发云 OCR / 不付费 / 不猜内容。
- 每页单独切段，块带 1 基**物理页号**（page_no）；TXT 无页概念保持 NULL。
- **文本层可读 ≠ 图片规则已覆盖**：某页只要含图片资源（即便是扫描件、图表、截图），
  或该页完全抽不出文本（典型扫描页），都记入 uncovered_sources（页号级）并置 partial，
  **不允许**因为"抽到了文字"就宣称整份 coverage 完整。
- 加密（is_encrypted）/损坏/超限（页数、字符数、单页字符）→ 明确拒绝，不降级猜测。
"""

from dataclasses import dataclass, field

# 安全上限：真实 Brief 远小于此，超出明确拒而不是静默截断
MAX_PAGES = 300
MAX_TOTAL_CHARS = 400_000
MAX_PAGE_CHARS = 60_000


class PdfFormatError(Exception):
    """PDF 解析失败（固定安全文案，不回显本地路径/内容）。"""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


@dataclass(frozen=True)
class PdfBlock:
    page_no: int          # 1 基物理页号
    ordinal: int
    text: str


@dataclass
class ParsedPdf:
    blocks: list[PdfBlock] = field(default_factory=list)
    page_count: int = 0
    uncovered_sources: list[str] = field(default_factory=list)  # 页号级未覆盖（图片页/无文本页）
    warnings: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.uncovered_sources)


def _page_has_images(page) -> bool:
    """保守判定：页资源里出现图片 XObject 即视为含未识读图片（不解析图片内容）。"""
    try:
        resources = page.get("/Resources")
        if resources is None:
            return False
        xobj = resources.get_object().get("/XObject") if hasattr(resources, "get_object") else resources.get("/XObject")
        if xobj is None:
            return False
        xobj = xobj.get_object() if hasattr(xobj, "get_object") else xobj
        for _, ref in xobj.items():
            try:
                obj = ref.get_object() if hasattr(ref, "get_object") else ref
                if obj.get("/Subtype") == "/Image":
                    return True
            except Exception:
                continue
        return False
    except Exception:
        # 判定失败时宁可当"含未识读内容"，也不假装干净
        return True


def parse_pdf(payload: bytes, *, allow_image_only=False) -> ParsedPdf:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PdfFormatError("MODULE_NOT_IMPLEMENTED", "PDF 解析依赖未安装（pypdf）") from None
    import io

    try:
        reader = PdfReader(io.BytesIO(payload))
    except Exception:
        raise PdfFormatError("EXTRACTION_FAILED", "PDF 结构无法解析或文件已损坏") from None
    if reader.is_encrypted:
        raise PdfFormatError("EXTRACTION_FAILED", "PDF 已加密，无法解析（不尝试破解，也不上传）")

    total_pages = len(reader.pages)
    if total_pages == 0:
        raise PdfFormatError("EXTRACTION_FAILED", "PDF 不含任何页面")
    if total_pages > MAX_PAGES:
        raise PdfFormatError("EXTRACTION_FAILED", f"PDF 页数 {total_pages} 超过安全上限 {MAX_PAGES}")

    out = ParsedPdf(page_count=total_pages)
    total_chars = 0
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        if len(raw) > MAX_PAGE_CHARS:
            raise PdfFormatError("EXTRACTION_FAILED", f"第 {page_no} 页文本超过单页上限")
        total_chars += len(raw)
        if total_chars > MAX_TOTAL_CHARS:
            raise PdfFormatError("EXTRACTION_FAILED", "PDF 文本总量超过安全上限")

        has_images = _page_has_images(page)
        paragraphs = [p.strip() for p in raw.split("\n") if p.strip()]
        for text in paragraphs:
            out.blocks.append(PdfBlock(page_no=page_no, ordinal=len(out.blocks), text=text))
        # 未覆盖判定：含图片，或整页抽不出文本（扫描页）
        if has_images or not paragraphs:
            reason = "含图片资源未识读" if has_images else "无可提取文本（疑似扫描页）"
            out.uncovered_sources.append(f"page:{page_no}")
            out.warnings.append(f"第 {page_no} 页{reason}，未纳入文本审核")

    if not out.blocks and not allow_image_only:
        raise PdfFormatError("EXTRACTION_FAILED", "PDF 未提取到任何文本（疑似纯扫描件），需人工补录")
    return out
