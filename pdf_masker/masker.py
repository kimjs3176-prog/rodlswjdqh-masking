"""PDF 마스킹 엔진.

검은 상자를 덧씌우는 것이 아니라 PDF 의 "교정(redaction)" 기능으로 해당 글자를
실제로 삭제한 뒤 상자를 그린다. 따라서 마스킹된 글자는 복사·검색으로 복구할 수 없다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pymupdf as fitz

from .patterns import KINDS, CUSTOM_KIND, CUSTOM_LABEL, find_pii


@dataclass
class MaskOptions:
    kinds: Iterable[str] | None = None      # None 이면 전체 종류
    keywords: list[str] = field(default_factory=list)
    regexes: list[str] = field(default_factory=list)
    partial: bool = False                    # True: 일부만 가림 (예: 주민번호 뒷자리)
    color: tuple[float, float, float] = (0, 0, 0)
    clear_metadata: bool = True              # 작성자 등 문서 속성 삭제


@dataclass
class Finding:
    page: int        # 1부터 시작
    kind: str
    preview: str     # 로그용 (일부 가린 값)


@dataclass
class MaskResult:
    source: Path
    output: Path | None
    pages: int = 0
    findings: list[Finding] = field(default_factory=list)
    image_only_pages: list[int] = field(default_factory=list)  # 글자가 없는(스캔) 쪽
    remaining: int = 0                       # 저장 후 재검사에서 남은 건수

    @property
    def counts(self) -> Counter:
        return Counter(f.kind for f in self.findings)

    def summary(self) -> str:
        lines = [f"{self.source.name}: {self.pages}쪽, 총 {len(self.findings)}건 마스킹"]
        for kind, n in self.counts.items():
            label = KINDS.get(kind, CUSTOM_LABEL if kind == CUSTOM_KIND else kind)
            lines.append(f"  - {label}: {n}건")
        if self.image_only_pages:
            pages = ", ".join(map(str, self.image_only_pages))
            lines.append(
                f"  ! 글자 정보가 없는 쪽(스캔 이미지로 추정): {pages}쪽 — 자동 탐지 불가, 직접 확인 필요"
            )
        if self.remaining:
            lines.append(f"  ! 재검사에서 {self.remaining}건이 남아 있습니다. 결과 파일을 확인하세요.")
        return "\n".join(lines)


def _preview(text: str) -> str:
    """로그에 원문이 그대로 남지 않도록 앞 2글자만 보여 준다."""
    return text[:2] + "*" * max(len(text) - 2, 0)


def _page_lines(page: fitz.Page):
    """쪽의 각 줄을 (문자열, 글자별 사각형 목록) 으로 돌려준다."""
    raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            chars, boxes = [], []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    chars.append(ch["c"] if len(ch["c"]) == 1 else " ")
                    boxes.append(fitz.Rect(ch["bbox"]))
            if chars:
                yield "".join(chars), boxes


def _scan_page(page: fitz.Page, opts: MaskOptions):
    """(Match, 가릴 사각형) 목록."""
    hits = []
    for text, boxes in _page_lines(page):
        for m in find_pii(text, opts.kinds, opts.keywords, opts.regexes):
            s, e = (m.mask_start, m.mask_end) if opts.partial else (m.start, m.end)
            rect = fitz.Rect()
            for box in boxes[s:e]:
                if not box.is_empty:
                    rect |= box
            if rect.is_empty:
                continue
            # 옆 글자가 함께 지워지지 않도록 좌우를 살짝 줄인다
            if rect.width > 1:
                rect.x0 += 0.3
                rect.x1 -= 0.3
            hits.append((m, rect))
    return hits


def scan_pdf(path: str | Path, opts: MaskOptions | None = None) -> MaskResult:
    """파일을 바꾸지 않고 탐지만 한다."""
    opts = opts or MaskOptions()
    path = Path(path)
    result = MaskResult(source=path, output=None)
    with fitz.open(path) as doc:
        result.pages = doc.page_count
        for page in doc:
            if not page.get_text().strip():
                result.image_only_pages.append(page.number + 1)
            for m, _ in _scan_page(page, opts):
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

        for page in doc:
            if not page.get_text().strip():
                result.image_only_pages.append(page.number + 1)
            hits = _scan_page(page, opts)
            for m, rect in hits:
                page.add_redact_annot(rect, fill=opts.color)
                result.findings.append(Finding(page.number + 1, m.kind, _preview(m.text)))
            if hits:
                # 글자는 삭제, 겹치는 이미지는 해당 부분만 지움
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)

        if opts.clear_metadata:
            doc.set_metadata({})
            doc.del_xml_metadata()

        dst.parent.mkdir(parents=True, exist_ok=True)
        # garbage=4: 지워진 객체를 파일에서 완전히 제거
        doc.save(dst, garbage=4, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
    finally:
        doc.close()

    # 저장된 결과를 다시 열어 남은 개인정보가 없는지 확인
    result.remaining = len(scan_pdf(dst, opts).findings)
    return result


def default_output_path(src: str | Path, out_dir: str | Path | None = None) -> Path:
    src = Path(src)
    folder = Path(out_dir) if out_dir else src.parent
    return folder / f"{src.stem}_마스킹{src.suffix}"
