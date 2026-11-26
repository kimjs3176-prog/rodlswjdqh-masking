"""엑셀(.xlsx, .xlsm) 마스킹.

셀 안의 개인정보 글자를 '*' 로 바꿔 새 파일로 저장한다.
검사 대상: 모든 시트(숨김 포함)의 셀 값, 메모, 머리글·바닥글, 하이퍼링크.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .masker import Finding, MaskOptions, MaskResult, _preview
from .patterns import find_pii, mask_text

SUFFIXES = (".xlsx", ".xlsm")


def _load(path: Path):
    import openpyxl

    try:
        return openpyxl.load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm")
    except zipfile.BadZipFile:
        raise ValueError(
            "엑셀 파일을 열 수 없습니다. 암호가 걸려 있거나 손상된 파일입니다. "
            "암호를 해제한 뒤 다시 시도하세요."
        ) from None


def _find(text: str, opts: MaskOptions):
    return find_pii(text, opts.kinds, opts.keywords, opts.regexes)


def _number_text(value) -> list[str]:
    """숫자로 저장된 셀을 글자로. 전화번호는 맨 앞 0 이 사라지므로 0 을 붙인 것도 본다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return []
    if isinstance(value, float) and not value.is_integer():
        return []
    digits = str(abs(int(value)))
    if len(digits) < 9:
        return []
    return [digits, "0" + digits] if len(digits) in (9, 10) else [digits]


class _Walker:
    """통합 문서를 돌며 개인정보를 찾고, apply 가 참이면 가린다."""

    def __init__(self, wb, opts: MaskOptions, result: MaskResult, apply: bool):
        self.wb, self.opts, self.result, self.apply = wb, opts, result, apply

    def text(self, value: str, sheet_no: int, where: str) -> str:
        """글자에서 개인정보를 찾아 기록하고, 가린 글자를 돌려준다."""
        matches = _find(value, self.opts)
        for m in matches:
            self.result.findings.append(Finding(sheet_no, m.kind, _preview(m.text), where))
        return mask_text(value, matches, self.opts.partial) if matches else value

    def run(self) -> None:
        for no, ws in enumerate(self.wb.worksheets, start=1):
            name = ws.title
            if _find(name, self.opts):
                self.result.notes.append(
                    f"시트 이름 '{_preview(name)}' 에 개인정보로 보이는 글자가 있습니다. 이름은 직접 바꿔 주세요."
                )
            images = len(getattr(ws, "_images", []))
            if images:
                self.result.notes.append(f"'{name}' 시트의 그림 {images}개는 검사하지 않았습니다.")

            for row in ws.iter_rows():
                for cell in row:
                    self.cell(cell, no, f"{name}!{cell.coordinate}")

            for part_name in ("oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"):
                part = getattr(ws, part_name, None)
                for side in ("left", "center", "right"):
                    section = getattr(part, side, None)
                    if section is not None and section.text:
                        new = self.text(section.text, no, f"{name}!머리글·바닥글")
                        if self.apply:
                            section.text = new

    def cell(self, cell, no: int, where: str) -> None:
        value = cell.value
        if isinstance(value, str):
            new = self.text(value, no, where)
            if self.apply and new != value:
                cell.value = new
        else:
            for digits in _number_text(value):
                matches = _find(digits, self.opts)
                if matches:
                    new = self.text(digits, no, where)
                    if self.apply:
                        cell.value = new
                        cell.number_format = "@"
                    break

        if cell.comment is not None and cell.comment.text:
            new = self.text(cell.comment.text, no, f"{where} 메모")
            if self.apply:
                cell.comment.text = new

        link = cell.hyperlink
        if link is not None:
            for attr in ("target", "tooltip", "location"):
                target = getattr(link, attr, None)
                if target and _find(target, self.opts):
                    self.text(target, no, f"{where} 링크")
                    if self.apply:
                        cell.hyperlink = None
                    break


def scan_xlsx(path: str | Path, opts: MaskOptions | None = None) -> MaskResult:
    """파일을 바꾸지 않고 탐지만 한다."""
    opts = opts or MaskOptions()
    path = Path(path)
    result = MaskResult(source=path, output=None, unit="개 시트")
    wb = _load(path)
    result.pages = len(wb.worksheets)
    _Walker(wb, opts, result, apply=False).run()
    wb.close()
    return result


def mask_xlsx(src: str | Path, dst: str | Path, opts: MaskOptions | None = None) -> MaskResult:
    """src 의 개인정보를 가려 dst 로 저장한다. 원본은 바꾸지 않는다."""
    opts = opts or MaskOptions()
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("원본 파일을 덮어쓸 수 없습니다. 다른 저장 위치를 지정하세요.")

    result = MaskResult(source=src, output=dst, unit="개 시트")
    wb = _load(src)
    result.pages = len(wb.worksheets)
    _Walker(wb, opts, result, apply=True).run()

    if opts.clear_metadata:
        from openpyxl.packaging.core import DocumentProperties

        wb.properties = DocumentProperties(creator="", lastModifiedBy="")

    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    wb.close()

    # 저장된 결과를 다시 열어 남은 개인정보가 없는지 확인
    result.remaining = len(scan_xlsx(dst, opts).findings)
    return result
