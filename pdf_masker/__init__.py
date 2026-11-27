"""PDF·엑셀 개인정보 마스킹 도구 (로컬 전용, 외부 전송 없음)."""

from .patterns import KINDS, Match, find_pii
from .masker import MaskOptions, MaskResult, mask_file, mask_pdf, scan_file

__all__ = ["KINDS", "Match", "find_pii", "MaskOptions", "MaskResult", "mask_file", "mask_pdf", "scan_file"]
__version__ = "1.3.0"
