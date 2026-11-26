import pytest

from pdf_masker.patterns import find_pii


def kinds(text, **kw):
    return [(m.kind, m.text) for m in find_pii(text, **kw)]


@pytest.mark.parametrize(
    "text,kind,value",
    [
        ("주민번호 900101-1234567 입니다", "rrn", "900101-1234567"),
        ("주민번호 9001011234567", "rrn", "9001011234567"),
        ("연락처: 010-1234-5678", "mobile", "010-1234-5678"),
        ("연락처 01012345678", "mobile", "01012345678"),
        ("전화 02-123-4567", "phone", "02-123-4567"),
        ("전화 063-919-1234", "phone", "063-919-1234"),
        ("메일 hong.gd@example.co.kr 로", "email", "hong.gd@example.co.kr"),
        ("카드 1234-5678-9012-3456", "card", "1234-5678-9012-3456"),
        ("면허 11-23-123456-78", "driver", "11-23-123456-78"),
        ("여권 M12345678", "passport", "M12345678"),
        ("계좌 110-123-456789", "account", "110-123-456789"),
    ],
)
def test_detects(text, kind, value):
    assert kinds(text) == [(kind, value)]


@pytest.mark.parametrize(
    "text",
    [
        "2024-01-15 회의",           # 날짜
        "문서번호 2024-123",
        "금액 1,234,567원",
        "991301-1234567",            # 13월 → 주민번호 아님
        "사업자번호 123-45-67890",
    ],
)
def test_ignores(text):
    assert kinds(text) == []


def test_partial_ranges():
    text = "900101-1234567"
    (m,) = find_pii(text)
    assert text[m.mask_start:m.mask_end] == "1234567"

    text = "010-1234-5678"
    (m,) = find_pii(text)
    assert text[m.mask_start:m.mask_end] == "1234"

    text = "hong@example.com"
    (m,) = find_pii(text)
    assert text[m.mask_start:m.mask_end] == "hong"


def test_kind_filter_and_keywords():
    text = "홍길동 010-1234-5678 hong@example.com"
    assert kinds(text, kinds=["email"]) == [("email", "hong@example.com")]
    assert kinds(text, kinds=[], keywords=["홍길동"]) == [("custom", "홍길동")]
    assert kinds(text, kinds=[], regexes=[r"\d{4}-\d{4}"]) == [("custom", "1234-5678")]


def test_multiple_in_one_line():
    text = "홍길동(900101-1234567) 010-1234-5678 / 02-123-4567"
    assert [k for k, _ in kinds(text)] == ["rrn", "mobile", "phone"]
