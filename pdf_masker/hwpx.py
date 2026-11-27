"""한글(.hwpx) 마스킹.

hwpx 는 XML 파일들을 묶은 zip 이다. 본문 XML 을 다시 만들지 않고, 개인정보에 해당하는
글자만 같은 자리에서 '*' 로 바꾼다. 그래서 서식·표·그림 등 나머지는 원본 그대로 남는다.
검사 대상: 본문, 표, 머리말·꼬리말, 각주·미주, 메모, 글상자, 누름틀·하이퍼링크 내용, 미리보기 글.
"""

from __future__ import annotations

import html
import re
import struct
import zipfile
import zlib
from pathlib import Path

from .masker import Finding, MaskOptions, MaskResult, _preview
from .patterns import _KEEP, find_pii, mask_text

SUFFIX = ".hwpx"

_TOKEN = re.compile(r"<[^>]*>|[^<]+")
_TAG = re.compile(r"<(/?)(?:[\w.\-]+:)?([\w.\-]+)[^>]*?(/?)>$", re.S)
_CHAR = re.compile(r"&[^;\s]{1,10};|.", re.S)
_SECTION = re.compile(r"Contents/section\d+\.xml$", re.I)
_PERSON_META = re.compile(
    r'(<opf:meta\s+name="(?:creator|lastsaveby)"[^>]*?)(?:/>|>[^<]*</opf:meta>)', re.I
)
_IMAGE = re.compile(r"BinData/.+\.(?:png|jpe?g|bmp|gif|tiff?|wmf|emf)$", re.I)


def _blank_png() -> bytes:
    """미리보기 그림을 대신할 흰색 1x1 PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\xff\xff\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")


def _mask_xml(xml: str, opts: MaskOptions, result: MaskResult, section: int, where: str) -> str:
    """XML 글에서 개인정보를 찾아 기록하고, 가린 XML 을 돌려준다.

    한 문단의 글자는 서식이 바뀔 때마다 여러 조각(<hp:t>)으로 나뉘므로,
    문단 단위로 이어 붙여 찾은 뒤 각 글자의 원래 위치에서 바꾼다.
    """
    edits: list[tuple[int, int]] = []

    def flush(chars: list[tuple[str, int | None, int | None]]) -> None:
        text = "".join(c for c, _, _ in chars)
        for m in find_pii(text, opts.kinds, opts.keywords, opts.regexes):
            result.findings.append(Finding(section, m.kind, _preview(m.text), where))
            s, e = (m.mask_start, m.mask_end) if opts.partial else (m.start, m.end)
            for ch, start, end in chars[s:e]:
                if start is not None and ch not in _KEEP:
                    edits.append((start, end))

    def chars_of(raw: str, offset: int):
        for c in _CHAR.finditer(raw):
            ch = html.unescape(c.group())
            yield (ch if len(ch) == 1 else " ", offset + c.start(), offset + c.end())

    paragraphs: list[list] = []   # 중첩된 문단(표 안의 문단 등)을 위한 스택
    in_text = 0
    for tok in _TOKEN.finditer(xml):
        raw = tok.group()
        if raw[0] != "<":
            if in_text and paragraphs:
                paragraphs[-1].extend(chars_of(raw, tok.start()))
            elif raw.strip():
                flush(list(chars_of(raw, tok.start())))  # 문단 밖의 글 (누름틀·링크 내용 등)
            continue
        tag = _TAG.match(raw)
        if tag is None or raw.startswith(("<?", "<!")):
            continue
        closing, name, empty = tag.group(1) == "/", tag.group(2), tag.group(3) == "/"
        if name == "p" and not empty:
            if closing:
                if paragraphs:
                    flush(paragraphs.pop())
                in_text = 0
            else:
                if paragraphs:
                    paragraphs[-1].append(("\n", None, None))  # 바깥 문단과 이어지지 않게
                paragraphs.append([])
        elif name == "t" and not empty:
            in_text = max(in_text + (-1 if closing else 1), 0)
        elif in_text and paragraphs and not closing:
            # 글 사이의 탭·줄바꿈 등은 공백으로 본다
            paragraphs[-1].append(("\n" if name == "lineBreak" else " ", None, None))
    while paragraphs:
        flush(paragraphs.pop())

    for start, end in sorted(set(edits), reverse=True):
        xml = xml[:start] + "*" + xml[end:]
    return xml


def _decode_xml(data: bytes, name: str) -> str:
    text = data.decode("utf-8", errors="strict") if data[:3] != b"\xef\xbb\xbf" else data[3:].decode("utf-8")
    if not text.lstrip().startswith("<"):
        raise ValueError("본문을 읽을 수 없습니다.")
    return text


def _process(src: Path, dst: Path | None, opts: MaskOptions) -> MaskResult:
    result = MaskResult(source=src, output=dst, unit="개 구역")
    try:
        zin = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise ValueError("한글 파일을 열 수 없습니다. 손상되었거나 hwpx 형식이 아닙니다.") from None

    out: list[tuple[zipfile.ZipInfo, bytes]] = []
    images = 0
    with zin:
        for info in zin.infolist():
            data = zin.read(info)
            name = info.filename
            if _SECTION.search(name):
                result.pages += 1
                try:
                    xml = _decode_xml(data, name)
                except (UnicodeDecodeError, ValueError):
                    raise ValueError(
                        "암호가 걸린 한글 파일입니다. 한글에서 암호를 해제해 다시 저장한 뒤 시도하세요."
                    ) from None
                where = Path(name).stem
                data = _mask_xml(xml, opts, result, result.pages, where).encode("utf-8")
            elif name.lower() == "preview/prvtext.txt":
                # 미리보기 글에도 본문이 들어 있다 (본문에서 이미 센 것이므로 건수에는 넣지 않음)
                for encoding in ("utf-8", "utf-16"):
                    try:
                        text = data.decode(encoding)
                    except UnicodeDecodeError:
                        continue
                    lines = text.split("\n")
                    for i, line in enumerate(lines):
                        matches = find_pii(line, opts.kinds, opts.keywords, opts.regexes)
                        lines[i] = mask_text(line, matches, opts.partial)
                    data = "\n".join(lines).encode(encoding)
                    break
            elif name.lower().startswith("preview/") and name.lower().endswith(".png"):
                data = _blank_png()  # 첫 쪽이 그대로 찍힌 미리보기 그림은 빈 그림으로 바꾼다
            elif name.lower() == "contents/content.hpf" and opts.clear_metadata:
                data = _PERSON_META.sub(r"\1/>", data.decode("utf-8")).encode("utf-8")
            elif _IMAGE.search(name):
                images += 1
            out.append((info, data))

    if result.pages == 0:
        raise ValueError("한글 파일에서 본문을 찾을 수 없습니다. hwpx 형식이 맞는지 확인하세요.")
    if images:
        result.notes.append(f"문서에 들어 있는 그림 {images}개는 검사하지 않았습니다.")

    if dst is not None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w") as zout:
            for info, data in out:
                zout.writestr(info, data)  # 원래의 순서·압축 방식 유지
    return result


def scan_hwpx(path: str | Path, opts: MaskOptions | None = None) -> MaskResult:
    """파일을 바꾸지 않고 탐지만 한다."""
    return _process(Path(path), None, opts or MaskOptions())


def mask_hwpx(src: str | Path, dst: str | Path, opts: MaskOptions | None = None) -> MaskResult:
    """src 의 개인정보를 가려 dst 로 저장한다. 원본은 바꾸지 않는다."""
    opts = opts or MaskOptions()
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("원본 파일을 덮어쓸 수 없습니다. 다른 저장 위치를 지정하세요.")
    result = _process(src, dst, opts)
    # 저장된 결과를 다시 열어 남은 개인정보가 없는지 확인
    result.remaining = len(scan_hwpx(dst, opts).findings)
    return result
