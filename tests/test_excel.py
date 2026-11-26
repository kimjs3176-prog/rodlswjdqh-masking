import openpyxl
import pytest
from openpyxl.comments import Comment

from pdf_masker.cli import main
from pdf_masker.excel import mask_xlsx, scan_xlsx
from pdf_masker.masker import MaskOptions, mask_file


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "명단.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "명단"
    ws.append(["성명", "주민등록번호", "휴대전화", "이메일", "출원번호", "금액"])
    ws.append(["홍길동", "900101-1234567", "010-1234-5678", "hong@example.com", "10-2023-0012345", 1234567890])
    ws.append(["김철수", 8502021234567, 1098765432, "비고: 02-123-4567 로 연락", "=A2&B2", 3.14])
    ws["A2"].comment = Comment("본인 연락처 010-1111-2222", "작성자")
    ws["D2"].hyperlink = "mailto:hong@example.com"
    ws.oddFooter.center.text = "담당 hong@example.com"
    hidden = wb.create_sheet("숨김")
    hidden.sheet_state = "hidden"
    hidden["B5"] = "계좌 110-123-456789"
    wb.properties.creator = "홍길동"
    wb.save(path)
    return path


def cells(path, sheet="명단"):
    ws = openpyxl.load_workbook(path)[sheet]
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_full_mask(book, tmp_path):
    out = tmp_path / "out.xlsx"
    result = mask_xlsx(book, out, MaskOptions(keywords=["홍길동", "김철수"]))
    assert result.remaining == 0 and result.pages == 2

    rows = cells(out)
    assert rows[0] == ["성명", "주민등록번호", "휴대전화", "이메일", "출원번호", "금액"]
    assert rows[1] == ["***", "******-*******", "***-****-****", "****@*******.***", "10-2023-0012345", 1234567890]
    # 숫자로 저장된 주민등록번호·전화번호도 가린다 (전화번호는 맨 앞 0 을 되살림)
    assert rows[2][:4] == ["***", "*************", "***********", "비고: **-***-**** 로 연락"]
    assert rows[2][4:] == ["=A2&B2", 3.14]
    assert cells(out, "숨김")[4][1] == "계좌 ***-***-******"

    wb = openpyxl.load_workbook(out)
    ws = wb["명단"]
    assert "1111" not in ws["A2"].comment.text
    assert ws["D2"].hyperlink is None
    assert "hong" not in ws.oddFooter.center.text
    assert not wb.properties.creator
    # 원본은 그대로
    assert cells(book)[1][1] == "900101-1234567"


def test_partial_mask(book, tmp_path):
    out = tmp_path / "out.xlsx"
    assert mask_xlsx(book, out, MaskOptions(partial=True)).remaining == 0
    row = cells(out)[1]
    assert row[1:4] == ["900101-*******", "010-****-5678", "****@example.com"]
    assert cells(out)[2][1] == "850202*******"


def test_scan_does_not_modify(book):
    before = book.read_bytes()
    result = scan_xlsx(book)
    assert book.read_bytes() == before
    assert {f.where for f in result.findings} >= {"명단!B2", "명단!A2 메모", "숨김!B5", "명단!머리글·바닥글"}
    assert "개 시트" in result.summary()


def test_dispatch_and_cli(book, tmp_path, capsys):
    assert mask_file(book, tmp_path / "a.xlsx").remaining == 0
    with pytest.raises(ValueError, match="xlsx 로 다시 저장"):
        mask_file(tmp_path / "old.xls", tmp_path / "b.xls")
    assert main([str(book), "-o", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "명단_마스킹.xlsx").exists()


def test_bad_file(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="열 수 없습니다"):
        scan_xlsx(bad)
