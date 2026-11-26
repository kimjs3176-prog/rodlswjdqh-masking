"""exe 로 묶을 때의 시작점. 더블클릭하면 창이 열린다."""

import sys


def selftest() -> int:
    """빌드 검증용: 글자 PDF, 스캔(그림) PDF, 엑셀을 만들어 마스킹이 되는지 확인한다."""
    import tempfile
    from pathlib import Path

    import pymupdf

    from pdf_masker.masker import mask_pdf

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        text_pdf, scan_pdf = tmp / "text.pdf", tmp / "scan.pdf"

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Tel 010-1234-5678  ID 900101-1234567", fontsize=12)
        doc.save(text_pdf)
        scan = pymupdf.open()
        scan.new_page().insert_image(page.rect, pixmap=page.get_pixmap(dpi=150))
        scan.save(scan_pdf)
        scan.close()
        doc.close()

        r1 = mask_pdf(text_pdf, tmp / "text_out.pdf")
        with pymupdf.open(tmp / "text_out.pdf") as out:
            text = out[0].get_text()
        if len(r1.findings) != 2 or r1.remaining or "1234567" in text:
            return 1

        r2 = mask_pdf(scan_pdf, tmp / "scan_out.pdf")
        if r2.ocr_error or r2.ocr_pages != [1] or len(r2.findings) != 2 or r2.remaining:
            return 2

        import openpyxl

        from pdf_masker.masker import mask_file

        wb = openpyxl.Workbook()
        wb.active.append(["900101-1234567", 1012345678])
        wb.save(tmp / "book.xlsx")
        r3 = mask_file(tmp / "book.xlsx", tmp / "book_out.xlsx")
        row = [c.value for c in openpyxl.load_workbook(tmp / "book_out.xlsx").active[1]]
        if r3.remaining or row != ["******-*******", "***********"]:
            return 3
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    from pdf_masker.gui import run

    run()
