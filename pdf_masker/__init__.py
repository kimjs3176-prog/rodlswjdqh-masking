"""PDF 개인정보 마스킹 도구 (로컬 전용, 외부 전송 없음)."""

from .patterns import KINDS, Match, find_pii
from .masker import MaskOptions, MaskResult, mask_pdf

__all__ = ["KINDS", "Match", "find_pii", "MaskOptions", "MaskResult", "mask_pdf"]
__version__ = "1.0.0"
