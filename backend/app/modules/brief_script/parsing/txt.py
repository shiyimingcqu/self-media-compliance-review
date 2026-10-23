"""纯 TXT 解析（t-17 首批，A 22:43 收窄）：可靠编码判定 + 段落 sourceMap 纯逻辑。
无 AI、无规则提取；空白/乱码明确失败，绝不把空提取当解析成功。
可靠编码=严格 UTF-8 系列与带 BOM 的 UTF-16；无 BOM 的 UTF-16/GB18030 宽容解码会把随机字节
解成合法字符（实测冒充成功），本切片不纳入——按“编码不可靠识别”明确失败，后续若需 GB 系
另立带启发式与人工核对的增量。PDF/DOC/DOCX 不在本模块能力内，由注册层显式返回未实现。"""

from dataclasses import dataclass

ENCODINGS = ("utf-8-sig", "utf-8")


class ParseFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


@dataclass(frozen=True)
class TextBlock:
    ordinal: int
    paragraph_id: str
    start_offset: int
    end_offset: int
    text: str


@dataclass(frozen=True)
class ParsedText:
    encoding: str
    text: str
    blocks: tuple[TextBlock, ...]


def decode_reliable(payload: bytes) -> tuple[str, str]:
    """BOM 判定 UTF-16/UTF-8-BOM；否则严格 UTF-8。全部失败=编码不可靠，不猜测、不替换字符、不宽容解码。"""
    if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
        try:
            return "utf-16", payload.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ParseFailure("EXTRACTION_FAILED", f"UTF-16 BOM 存在但解码失败：{exc}") from exc
    if payload.startswith(b"\xef\xbb\xbf"):
        try:
            return "utf-8-bom", payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParseFailure("EXTRACTION_FAILED", "UTF-8 BOM 存在但正文解码失败") from exc
    try:
        return "utf-8", payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    raise ParseFailure("EXTRACTION_FAILED", "文本编码不可靠识别（乱码或混合编码），请重新提交 UTF-8 或带 BOM 的 UTF-16/UTF-8 版本")


def looks_garbled(text: str) -> bool:
    """替换符/控制字符比例过高按乱码处理（gb18030 宽容解码的兜底判定）。"""
    if not text:
        return False
    sample = text[:20000]
    bad = sum(1 for ch in sample if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r"))
    return bad / max(len(sample), 1) > 0.05


def split_paragraphs(text: str) -> list[TextBlock]:
    """空行分段；段内连续行合并。start/end=原文字符闭开区间，可据 text[start:end] 回验。"""
    blocks: list[TextBlock] = []
    cursor = 0
    start: int | None = None

    def flush(begin: int, end: int) -> None:
        blocks.append(TextBlock(len(blocks), f"p{len(blocks) + 1}", begin, end, text[begin:end]))

    last_end = 0
    for raw in text.split("\n"):
        line_start = cursor
        cursor += len(raw) + 1
        stripped = raw.strip()
        if stripped:
            if start is None:
                start = line_start + raw.index(stripped[0])
            last_end = line_start + raw.index(stripped[0]) + len(stripped)
        elif start is not None:
            flush(start, last_end)
            start = None
    if start is not None:
        flush(start, last_end)
    return blocks


def parse_txt(payload: bytes) -> ParsedText:
    encoding, text = decode_reliable(payload)
    if not text.strip():
        raise ParseFailure("EXTRACTION_FAILED", "文件内容为空白，不构成可审核 Brief")
    if looks_garbled(text):
        raise ParseFailure("EXTRACTION_FAILED", "解码结果乱码比例过高，按解析失败处理")
    blocks = tuple(split_paragraphs(text))
    if not blocks:
        raise ParseFailure("EXTRACTION_FAILED", "未提取到任何段落，不视为解析成功")
    return ParsedText(encoding=encoding, text=text, blocks=blocks)
