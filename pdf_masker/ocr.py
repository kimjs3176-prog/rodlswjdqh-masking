"""스캔(이미지) 쪽의 글자 인식(OCR).

PyMuPDF 에 내장된 Tesseract 엔진을 쓰므로 별도 프로그램 설치가 필요 없다.
언어 데이터(kor, eng)는 pdf_masker/tessdata 에 함께 들어 있어 인터넷 없이 동작한다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pymupdf as fitz

LANGUAGE = "kor+eng"
DPI = 300
_BUNDLED = Path(__file__).parent / "tessdata"
_FILES = ("kor.traineddata", "eng.traineddata")


def _ascii_copy(src: Path) -> Path | None:
    """Tesseract 는 Windows 에서 한글이 들어간 경로를 읽지 못할 수 있어
    (예: 사용자 이름이 한글) 영문 경로로 언어 데이터를 복사해 둔다."""
    for base in (os.environ.get("PROGRAMDATA"), os.environ.get("PUBLIC"), "C:\\"):
        if not base or not base.isascii():
            continue
        dst = Path(base) / "PDF-Masker" / "tessdata"
        try:
            dst.mkdir(parents=True, exist_ok=True)
            for name in _FILES:
                s, d = src / name, dst / name
                if not d.exists() or d.stat().st_size != s.stat().st_size:
                    shutil.copyfile(s, d)
            return dst
        except OSError:
            continue
    return None


def tessdata_dir() -> str | None:
    """언어 데이터 폴더. 없으면 None."""
    if not all((_BUNDLED / name).exists() for name in _FILES):
        return None
    if os.name == "nt" and not str(_BUNDLED).isascii():
        return str(_ascii_copy(_BUNDLED) or _BUNDLED)
    return str(_BUNDLED)


def needs_ocr(page: fitz.Page) -> bool:
    """글자가 없거나, 이미지가 쪽의 절반 이상을 덮는 쪽은 스캔으로 본다."""
    images = page.get_image_info()
    if not images:
        return False
    if not page.get_text().strip():
        return True
    page_area = abs(page.rect)
    covered = sum(abs(fitz.Rect(img["bbox"]) & page.rect) for img in images)
    return page_area > 0 and covered / page_area >= 0.5


def ocr_textpage(page: fitz.Page) -> fitz.TextPage:
    """쪽 전체를 그림으로 바꿔 글자를 인식한다. 실패하면 예외."""
    tessdata = tessdata_dir()
    if tessdata is None:
        raise RuntimeError("OCR 언어 데이터(tessdata)를 찾을 수 없습니다.")
    return page.get_textpage_ocr(
        flags=fitz.TEXT_PRESERVE_WHITESPACE,
        language=LANGUAGE,
        dpi=DPI,
        full=True,
        tessdata=tessdata,
    )
