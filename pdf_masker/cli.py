"""명령줄 실행: python -m pdf_masker 파일.pdf [...]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .masker import MaskOptions, default_output_path, is_supported, mask_file, scan_file
from .patterns import KINDS


def _collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if is_supported(f) and "_마스킹" not in f.stem)
        else:
            files.append(p)
    return files


def build_parser() -> argparse.ArgumentParser:
    kinds_help = ", ".join(f"{k}({v})" for k, v in KINDS.items())
    ap = argparse.ArgumentParser(
        prog="pdf_masker",
        description="PDF·엑셀·한글 개인정보 마스킹 (로컬 처리). 인자 없이 실행하면 창(GUI)이 열립니다.",
    )
    ap.add_argument("inputs", nargs="*", help="PDF·엑셀(.xlsx, .xlsm)·한글(.hwpx) 파일 또는 폴더")
    ap.add_argument("-o", "--out-dir", help="저장 폴더 (기본: 원본과 같은 폴더, 파일명_마스킹.확장자)")
    ap.add_argument("-k", "--kinds", help=f"탐지할 종류를 쉼표로 구분 (기본: 전체). {kinds_help}")
    ap.add_argument("-w", "--keyword", action="append", default=[], help="추가로 가릴 단어 (여러 번 지정 가능)")
    ap.add_argument("-r", "--regex", action="append", default=[], help="추가로 가릴 정규식 (여러 번 지정 가능)")
    ap.add_argument("--partial", action="store_true", help="부분 마스킹 (예: 주민번호 뒷자리만)")
    ap.add_argument("--keep-metadata", action="store_true", help="문서 속성(작성자 등)을 지우지 않음")
    ap.add_argument("--no-ocr", action="store_true", help="스캔(이미지) 쪽의 글자 인식(OCR)을 하지 않음")
    ap.add_argument("--password", help="암호가 걸린 PDF 의 암호")
    ap.add_argument("--dry-run", action="store_true", help="파일을 만들지 않고 탐지 결과만 출력")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.inputs:
        from .gui import run

        run()
        return 0

    kinds = None
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        unknown = [k for k in kinds if k not in KINDS]
        if unknown:
            print(f"알 수 없는 종류: {', '.join(unknown)}", file=sys.stderr)
            return 2

    opts = MaskOptions(
        kinds=kinds,
        keywords=args.keyword,
        regexes=args.regex,
        partial=args.partial,
        clear_metadata=not args.keep_metadata,
        ocr=not args.no_ocr,
    )

    failed = 0
    for src in _collect(args.inputs):
        try:
            if args.dry_run:
                result = scan_file(src, opts, args.password)
                print(result.summary().replace("마스킹", "탐지", 1))
                for f in result.findings:
                    print(f"    {f.where or str(f.page) + '쪽'} [{f.kind}] {f.preview}")
            else:
                result = mask_file(src, default_output_path(src, args.out_dir), opts, args.password)
                print(result.summary())
                print(f"  → {result.output}")
                if result.remaining:
                    failed += 1
        except Exception as exc:  # 한 파일이 실패해도 나머지는 계속
            failed += 1
            print(f"{src}: 실패 — {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
