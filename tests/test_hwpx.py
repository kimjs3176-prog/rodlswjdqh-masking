import zipfile

import pytest

from pdf_masker.cli import main
from pdf_masker.hwpx import mask_hwpx, scan_hwpx
from pdf_masker.masker import MaskOptions, mask_file

NS = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
LINESEG = '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" horzsize="42520"/></hp:linesegarray>'


def p(*runs):
    body = "".join(f'<hp:run charPrIDRef="{i}">{r}</hp:run>' for i, r in enumerate(runs))
    return f'<hp:p id="1234567890" paraPrIDRef="0">{body}{LINESEG}</hp:p>'


SECTION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    f"<hs:sec {NS}>"
    + p("<hp:t>성명: 홍길동 &amp; 김철수</hp:t>")
    # 서식이 달라 여러 조각으로 나뉜 주민등록번호
    + p("<hp:t>주민등록번호: 9001</hp:t>", "<hp:t>01-12</hp:t>", "<hp:t>34567</hp:t>")
    # 글 사이에 탭이 들어간 경우
    + p("<hp:t>연락처<hp:tab width=\"4000\"/>010-1234-5678</hp:t>")
    + p("<hp:t>출원번호 10-2023-0012345, 작성일 2024-01-15</hp:t>")
    # 표: 바깥 문단 안에 칸마다 문단이 들어 있다
    + p(
        "<hp:tbl><hp:tr><hp:tc><hp:subList>"
        + p("<hp:t>hong@example.com</hp:t>")
        + "</hp:subList></hp:tc><hp:tc><hp:subList>"
        + p("<hp:t>110-123-456789</hp:t>")
        + "</hp:subList></hp:tc></hp:tr></hp:tbl><hp:t/>"
    )
    # 하이퍼링크 내용
    + p(
        '<hp:ctrl><hp:fieldBegin type="HYPERLINK"><hp:parameters>'
        '<hp:stringParam name="Command">mailto:kim@example.com</hp:stringParam>'
        "</hp:parameters></hp:fieldBegin></hp:ctrl><hp:t>메일 보내기</hp:t>"
    )
    + "</hs:sec>"
)
HPF = (
    '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"><opf:metadata><opf:title>보고서</opf:title>'
    '<opf:meta name="creator" content="text">홍길동</opf:meta>'
    '<opf:meta name="lastsaveby" content="text">홍길동</opf:meta>'
    '<opf:meta name="CreatedDate" content="text">2024-01-15T00:00:00Z</opf:meta>'
    "</opf:metadata></opf:package>"
)
PREVIEW = "성명: 홍길동 & 김철수\r\n주민등록번호: 900101-1234567\r\n"


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "신청서.hwpx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip")  # 압축하지 않음
        z.writestr("Contents/header.xml", "<hh:head/>")
        z.writestr("Contents/section0.xml", SECTION)
        z.writestr("Preview/PrvText.txt", PREVIEW.encode("utf-8"))
        z.writestr("Preview/PrvImage.png", b"\x89PNG fake thumbnail showing the first page")
        z.writestr("BinData/image1.png", b"picture")
        z.writestr("Contents/content.hpf", HPF)
    return path


def read(path, name):
    return zipfile.ZipFile(path).read(name).decode("utf-8")


def test_full_mask(doc, tmp_path):
    out = tmp_path / "out.hwpx"
    result = mask_hwpx(doc, out, MaskOptions(keywords=["홍길동", "김철수"]))
    assert result.remaining == 0 and result.pages == 1
    assert result.counts == {"custom": 2, "rrn": 1, "mobile": 1, "email": 2, "account": 1}

    body = read(out, "Contents/section0.xml")
    assert "<hp:t>성명: *** &amp; ***</hp:t>" in body
    # 조각이 나뉜 번호도 조각 구조를 유지한 채 가린다
    assert "<hp:t>주민등록번호: ****</hp:t>" in body and "<hp:t>**-**</hp:t>" in body and "<hp:t>*****</hp:t>" in body
    assert '<hp:t>연락처<hp:tab width="4000"/>***-****-****</hp:t>' in body
    assert "출원번호 10-2023-0012345, 작성일 2024-01-15" in body
    assert "<hp:t>****@*******.***</hp:t>" in body and "<hp:t>***-***-******</hp:t>" in body
    assert "mailto:***@*******.***" in body
    # 글자 외에는 아무것도 바뀌지 않는다 (번호처럼 보이는 속성값 포함)
    assert len(body) == len(SECTION) and body.count('id="1234567890"') == SECTION.count('id="1234567890"')

    preview = read(out, "Preview/PrvText.txt")
    assert "홍길동" not in preview and "1234567" not in preview
    with zipfile.ZipFile(out) as z:
        assert z.read("Preview/PrvImage.png").startswith(b"\x89PNG\r\n\x1a\n")
        assert b"thumbnail" not in z.read("Preview/PrvImage.png")
        assert z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert z.read("Contents/header.xml") == b"<hh:head/>"
    hpf = read(out, "Contents/content.hpf")
    assert "홍길동" not in hpf and "CreatedDate" in hpf and "<opf:title>보고서</opf:title>" in hpf
    assert "그림 1개" in result.summary() and "개 구역" in result.summary()
    # 원본은 그대로
    assert read(doc, "Contents/section0.xml") == SECTION


def test_partial_mask(doc, tmp_path):
    out = tmp_path / "out.hwpx"
    assert mask_hwpx(doc, out, MaskOptions(partial=True)).remaining == 0
    body = read(out, "Contents/section0.xml")
    assert "<hp:t>주민등록번호: 9001</hp:t>" in body and "<hp:t>01-**</hp:t>" in body and "<hp:t>*****</hp:t>" in body
    assert "010-****-5678" in body and "****@example.com" in body


def test_scan_does_not_modify(doc):
    before = doc.read_bytes()
    assert len(scan_hwpx(doc).findings) == 5
    assert doc.read_bytes() == before


def test_dispatch_cli_and_errors(doc, tmp_path):
    assert mask_file(doc, tmp_path / "a.hwpx").remaining == 0
    assert main([str(doc), "-o", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "신청서_마스킹.hwpx").exists()
    with pytest.raises(ValueError, match="hwpx 로 저장"):
        mask_file(tmp_path / "old.hwp", tmp_path / "b.hwp")

    bad = tmp_path / "bad.hwpx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="열 수 없습니다"):
        scan_hwpx(bad)

    locked = tmp_path / "locked.hwpx"
    with zipfile.ZipFile(locked, "w") as z:
        z.writestr("Contents/section0.xml", b"\x8f\x01\xfe\x99 encrypted bytes")
    with pytest.raises(ValueError, match="암호"):
        scan_hwpx(locked)
