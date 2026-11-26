"""PDF 마스킹 엔진.

검은 상자를 덧씌우는 것이 아니라 PDF 의 "교정(redaction)" 기능으로 해당 글자를
실제로 삭제한 뒤 상자를 그린다. 따라서 마스킹된 글자는 복사·검색으로 복구할 수 없다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

import pymupdf as fitz

from . import ocr
from .patterns import KINDS, CUSTOM_KIND, CUSTOM_LABEL, find_pii

MAX_OCR_PASSES = 3
SUPPORTED = (".pdf", ".xlsx", ".xlsm")


@dataclass
class MaskOptions:
    kinds: Iterable[str] | None = None      # None 이면 전체 종류
    keywords: list[str] = field(default_factory=list)
    regexes: list[str] = field(default_factory=list)
    partial: bool = False                    # True: 일부만 가림 (예: 주민번호 뒷자리)
    color: tuple[float, float, float] = (0, 0, 0)
    clear_metadata: bool = True              # 작성자 등 문서 속성 삭제
    ocr: bool = True                         # 스캔(이미지) 쪽을 글자 인식으로 처리


@dataclass
class Finding:
    page: int        # 1부터 시작
    kind: str
    preview: str     # 로그용 (일부 가린 값)
    where: str = ""  # 엑셀: 시트!셀


@dataclass
class MaskResult:
    source: Path
    output: Path | None
    pages: int = 0
    findings: list[Finding] = field(default_factory=list)
    image_only_pages: list[int] = field(default_factory=list)  # 글자가 없는데 OCR 도 못 한 쪽
    ocr_pages: list[int] = field(default_factory=list)         # OCR 로 처리한 쪽
    ocr_error: str = ""
    unit: str = "쪽"                          # 엑셀은 "개 시트"
    notes: list[str] = field(default_factory=list)             # 사용자에게 알릴 사항
    remaining: int = 0                       # 저장 후 재검사에서 남은 건수

    @property
    def counts(self) -> Counter:
        return Counter(f.kind for f in self.findings)

    def summary(self) -> str:
        lines = [f"{self.source.name}: {self.pages}{self.unit}, 총 {len(self.findings)}건 마스킹"]
        for kind, n in self.counts.items():
            label = KINDS.get(kind, CUSTOM_LABEL if kind == CUSTOM_KIND else kind)
            lines.append(f"  - {label}: {n}건")
        if self.ocr_pages:
            pages = ", ".join(map(str, self.ocr_pages))
            lines.append(
                f"  * 글자 인식(OCR)으로 처리한 쪽: {pages}쪽 — 인식 오류로 빠질 수 있으니 직접 확인 필요"
            )
        for note in self.notes:
            lines.append(f"  ! {note}")
        if self.ocr_error:
            lines.append(f"  ! 글자 인식(OCR) 실패: {self.ocr_error}")
        if self.image_only_pages:
            pages = ", ".join(map(str, self.image_only_pages))
            lines.append(
                f"  ! 글자 정보가 없는 쪽(스캔 이미지로 추정): {pages}쪽 — 탐지되지 않음, 직접 확인 필요"
            )
        if self.remaining:
            lines.append(f"  ! 재검사에서 {self.remaining}건이 남아 있습니다. 결과 파일을 확인하세요.")
        return "\n".join(lines)


def _preview(text: str) -> str:
    """로그에 원문이 그대로 남지 않도록 앞 2글자만 보여 준다."""
    return text[:2] + "*" * max(len(text) - 2, 0)


def _page_lines(page: fitz.Page, textpage: fitz.TextPage | None = None):
    """쪽의 각 줄을 (문자열, 글자별 사각형 목록) 으로 돌려준다."""
    raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE, textpage=textpage)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            chars, boxes = [], []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    chars.append(ch["c"] if len(ch["c"]) == 1 else " ")
                    boxes.append(fitz.Rect(ch["bbox"]))
            if chars:
                yield "".join(chars), boxes


def _hits(page: fitz.Page, opts: MaskOptions, textpage: fitz.TextPage | None = None):
    """(Match, 가릴 사각형) 목록. textpage 가 있으면 OCR 결과에서 찾는다."""
    hits = []
    for text, boxes in _page_lines(page, textpage):
        for m in find_pii(text, opts.kinds, opts.keywords, opts.regexes):
            s, e = (m.mask_start, m.mask_end) if opts.partial else (m.start, m.end)
            rect = fitz.Rect()
            for box in boxes[s:e]:
                if not box.is_empty:
                    rect |= box
            if rect.is_empty:
                continue
            if textpage is not None:
                # OCR 의 글자 위치는 대략적이므로 조금 넉넉하게 가린다
                rect += (-1, -1.5, 1, 1.5)
            elif rect.width > 1:
                # 옆 글자가 함께 지워지지 않도록 좌우를 살짝 줄인다
                rect.x0 += 0.3
                rect.x1 -= 0.3
            hits.append((m, rect))
    return hits


def _scan_page(page: fitz.Page, opts: MaskOptions, result: MaskResult):
    """쪽에서 가릴 곳을 찾는다. 스캔 쪽은 OCR 로 찾고 result 에 기록한다."""
    number = page.number + 1
    hits = _hits(page, opts)
    has_text = bool(page.get_text().strip())

    if opts.ocr and not result.ocr_error and ocr.needs_ocr(page):
        try:
            textpage = ocr.ocr_textpage(page)
        except Exception as exc:  # OCR 을 못 써도 나머지 처리는 계속
            result.ocr_error = str(exc) or type(exc).__name__
        else:
            result.ocr_pages.append(number)
            for m, rect in _hits(page, opts, textpage):
                if not any(rect.intersects(r) for _, r in hits):
                    hits.append((m, rect))
            has_text = True

    if not has_text and page.get_image_info():
        result.image_only_pages.append(number)
    return hits


def scan_pdf(
    path: str | Path, opts: MaskOptions | None = None, password: str | None = None
) -> MaskResult:
    """파일을 바꾸지 않고 탐지만 한다."""
    opts = opts or MaskOptions()
    path = Path(path)
    result = MaskResult(source=path, output=None)
    with fitz.open(path) as doc:
        if doc.needs_pass and not doc.authenticate(password or ""):
            raise PermissionError("암호가 걸린 PDF 입니다. 올바른 암호가 필요합니다.")
        result.pages = doc.page_count
        for page in doc:
            for m, _ in _scan_page(page, opts, result):
                result.findings.append(Finding(page.number + 1, m.kind, _preview(m.text)))
    return result


def mask_pdf(
    src: str | Path,
    dst: str | Path,
    opts: MaskOptions | None = None,
    password: str | None = None,
) -> MaskResult:
    """src 의 개인정보를 가려 dst 로 저장한다. 원본은 바꾸지 않는다."""
    opts = opts or MaskOptions()
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("원본 파일을 덮어쓸 수 없습니다. 다른 저장 위치를 지정하세요.")

    result = MaskResult(source=src, output=dst)
    doc = fitz.open(src)
    try:
        if doc.needs_pass and not doc.authenticate(password or ""):
            raise PermissionError("암호가 걸린 PDF 입니다. 올바른 암호가 필요합니다.")
        result.pages = doc.page_count
        done: dict[int, list[fitz.Rect]] = {}

        for page in doc:
            done[page.number] = rects = []
            is_ocr = clean = False
            # 스캔 쪽은 가린 뒤 다시 인식해서, 처음에 놓친 것이 보이면 또 가린다
            for attempt in range(MAX_OCR_PASSES):
                scratch = result if attempt == 0 else MaskResult(source=src, output=None)
                hits = [h for h in _scan_page(page, opts, scratch) if not _covered(h[1], rects)]
                is_ocr = page.number + 1 in result.ocr_pages
                if not hits:
                    clean = True
                    break
                for m, rect in hits:
                    page.add_redact_annot(rect, fill=opts.color)
                    rects.append(rect)
                    result.findings.append(Finding(page.number + 1, m.kind, _preview(m.text)))
                # 글자는 삭제, 겹치는 이미지는 해당 부분의 픽셀을 지움
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
                if not is_ocr:
                    break  # 글자 정보가 있는 쪽은 저장 후 재검사로 확인
            if is_ocr and not clean:
                scratch = MaskResult(source=src, output=None)
                result.remaining += sum(
                    not _covered(rect, rects) for _, rect in _scan_page(page, opts, scratch)
                )

        if opts.clear_metadata:
            doc.set_metadata({})
            doc.del_xml_metadata()

        dst.parent.mkdir(parents=True, exist_ok=True)
        # garbage=4: 지워진 객체를 파일에서 완전히 제거
        doc.save(dst, garbage=4, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
    finally:
        doc.close()

    # 저장된 결과를 다시 열어 남은 개인정보가 없는지 확인 (스캔 쪽은 위에서 이미 확인)
    result.remaining += _count_remaining(dst, replace(opts, ocr=False), done)
    return result


def _covered(rect: fitz.Rect, done: list[fitz.Rect]) -> bool:
    """이미 가린 자리인지. OCR 이 검은 상자를 글자로 잘못 읽는 경우를 걸러 낸다."""
    area = abs(rect)
    return area <= 0 or max((abs(rect & r) for r in done), default=0) / area >= 0.5


def _count_remaining(path: Path, opts: MaskOptions, done: dict[int, list[fitz.Rect]]) -> int:
    """결과 파일에 아직 남아 있는 개인정보 건수 (이미 가린 자리는 제외)."""
    scratch = MaskResult(source=path, output=None)
    with fitz.open(path) as doc:
        return sum(
            not _covered(rect, done.get(page.number, []))
            for page in doc
            for _, rect in _scan_page(page, opts, scratch)
        )


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED


def mask_file(src, dst, opts: MaskOptions | None = None, password: str | None = None) -> MaskResult:
    """파일 종류(PDF·엑셀)에 맞는 방법으로 마스킹한다."""
    suffix = Path(src).suffix.lower()
    if suffix == ".pdf":
        return mask_pdf(src, dst, opts, password)
    if suffix in SUPPORTED:
        from .excel import mask_xlsx

        return mask_xlsx(src, dst, opts)
    raise ValueError(_unsupported(suffix))


def scan_file(src, opts: MaskOptions | None = None, password: str | None = None) -> MaskResult:
    """파일을 바꾸지 않고 탐지만 한다."""
    suffix = Path(src).suffix.lower()
    if suffix == ".pdf":
        return scan_pdf(src, opts, password)
    if suffix in SUPPORTED:
        from .excel import scan_xlsx

        return scan_xlsx(src, opts)
    raise ValueError(_unsupported(suffix))


def _unsupported(suffix: str) -> str:
    if suffix == ".xls":
        return "옛 엑셀 형식(.xls)은 지원하지 않습니다. 엑셀에서 .xlsx 로 다시 저장한 뒤 시도하세요."
    return f"지원하지 않는 파일 형식입니다: {suffix or '(확장자 없음)'}"


def default_output_path(src: str | Path, out_dir: str | Path | None = None) -> Path:
    src = Path(src)
    folder = Path(out_dir) if out_dir else src.parent
    return folder / f"{src.stem}_마스킹{src.suffix}"
