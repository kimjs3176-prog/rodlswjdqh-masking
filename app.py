"""exe 로 묶을 때의 시작점. 더블클릭하면 창이 열린다."""

import sys


def selftest() -> int:
    """빌드 검증용: 샘플 PDF 를 만들어 마스킹이 되는지 확인한다."""
    import tempfile
    from pathlib import Path

    import pymupdf

    from pdf_masker.masker import mask_pdf

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "a.pdf", Path(tmp) / "b.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 100), "홍길동 900101-1234567", fontname="korea")
        doc.save(src)
        doc.close()
        result = mask_pdf(src, dst)
        with pymupdf.open(dst) as out:
            text = out[0].get_text()
    ok = len(result.findings) == 1 and result.remaining == 0 and "1234567" not in text
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    from pdf_masker.gui import run

    run()
