import pymupdf as fitz
import pytest

from pdf_masker.cli import main
from pdf_masker.masker import MaskOptions, mask_pdf, scan_pdf

LINES = [
    "성명: 홍길동",
    "주민등록번호: 900101-1234567",
    "휴대전화: 010-1234-5678",
    "이메일: hong@example.com",
    "작성일: 2024-01-15",
]


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(LINES):
        page.insert_text((72, 100 + i * 30), line, fontname="korea", fontsize=12)
    doc.new_page()  # 글자가 없는 쪽
    doc.set_metadata({"author": "홍길동"})
    doc.save(path)
    doc.close()
    return path


def text_of(path):
    with fitz.open(path) as doc:
        return doc[0].get_text()


def test_full_mask_removes_text(sample, tmp_path):
    out = tmp_path / "out.pdf"
    result = mask_pdf(sample, out, MaskOptions(keywords=["홍길동"]))

    assert result.counts == {"custom": 1, "rrn": 1, "mobile": 1, "email": 1}
    assert result.remaining == 0
    assert result.image_only_pages == [2]

    text = text_of(out)
    for secret in ("홍길동", "900101", "1234567", "010-1234-5678", "hong@example.com"):
        assert secret not in text
    # 개인정보가 아닌 내용은 그대로
    assert "주민등록번호:" in text and "2024-01-15" in text
    # 파일 원본 바이트에도 남지 않음, 문서 속성 삭제
    with fitz.open(out) as doc:
        assert not doc.metadata.get("author")
    # 원본은 그대로
    assert "900101-1234567" in text_of(sample)


def test_partial_mask_keeps_visible_part(sample, tmp_path):
    out = tmp_path / "out.pdf"
    result = mask_pdf(sample, out, MaskOptions(partial=True))
    assert result.remaining == 0

    text = text_of(out)
    assert "900101" in text and "1234567" not in text
    assert "010" in text and "5678" in text and "1234" not in text
    assert "@example.com" in text and "hong" not in text


def test_scan_does_not_modify(sample):
    before = sample.read_bytes()
    result = scan_pdf(sample)
    assert len(result.findings) == 3
    assert sample.read_bytes() == before


def test_refuses_overwrite(sample):
    with pytest.raises(ValueError):
        mask_pdf(sample, sample)


def test_cli(sample, tmp_path, capsys):
    out_dir = tmp_path / "out"
    assert main([str(sample), "-o", str(out_dir), "-w", "홍길동"]) == 0
    out = out_dir / "sample_마스킹.pdf"
    assert out.exists() and "홍길동" not in text_of(out)
    assert "총 4건" in capsys.readouterr().out
