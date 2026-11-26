"""개인정보 패턴 탐지.

한 줄의 텍스트에서 개인정보를 찾아 (종류, 전체 범위, 마스킹 범위)를 돌려준다.
마스킹 범위는 "부분 마스킹" 모드에서 가릴 구간이다 (예: 주민등록번호 뒷자리).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

HY = r"[-‐‑–—−]"  # 하이픈으로 쓰이는 여러 문자

# 종류 코드 -> 화면 표시 이름 (탐지 우선순위 순서)
KINDS: dict[str, str] = {
    "rrn": "주민등록번호·외국인등록번호",
    "mobile": "휴대전화번호",
    "phone": "일반전화번호",
    "email": "이메일 주소",
    "card": "신용카드번호",
    "driver": "운전면허번호",
    "passport": "여권번호",
    "account": "계좌번호",
}
CUSTOM_KIND = "custom"
CUSTOM_LABEL = "사용자 지정"

_PATTERNS: dict[str, re.Pattern[str]] = {
    "rrn": re.compile(
        rf"(?<!\d)(?P<a>\d{{6}})\s?{HY}?\s?(?P<m>[1-8]\d{{6}})(?!\d)"
    ),
    "mobile": re.compile(
        rf"(?<!\d)01[016789][\s.]?{HY}?[\s.]?(?P<m>\d{{3,4}})[\s.]?{HY}?[\s.]?\d{{4}}(?!\d)"
    ),
    "phone": re.compile(
        rf"(?<!\d)\(?0(?:2|[3-6]\d|70)\)?\s?(?:{HY}|\.|\s)\s?(?P<m>\d{{3,4}})\s?(?:{HY}|\.)\s?\d{{4}}(?!\d)"
    ),
    "email": re.compile(
        r"(?P<m>[A-Za-z0-9._%+\-]+)@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
    ),
    "card": re.compile(
        rf"(?<!\d)\d{{4}}(?:{HY}|\s)(?P<m>\d{{4}}(?:{HY}|\s)\d{{4}})(?:{HY}|\s)\d{{4}}(?!\d)"
    ),
    "driver": re.compile(
        rf"(?<!\d)\d{{2}}{HY}\d{{2}}{HY}(?P<m>\d{{6}}){HY}\d{{2}}(?!\d)"
    ),
    "passport": re.compile(
        r"(?<![A-Za-z0-9])(?P<m>[MSRODG](?:\d{8}|\d{3}[A-Z]\d{4}))(?![A-Za-z0-9])"
    ),
    "account": re.compile(
        rf"(?<![\d\-])(?P<m>\d{{2,6}}{HY}\d{{2,6}}{HY}\d{{2,8}}(?:{HY}\d{{1,3}})?)(?![\d\-])"
    ),
}


@dataclass(frozen=True)
class Match:
    kind: str
    start: int
    end: int
    mask_start: int
    mask_end: int
    text: str

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, CUSTOM_LABEL)


def _valid_rrn(m: re.Match[str]) -> bool:
    front = m.group("a")
    month, day = int(front[2:4]), int(front[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def _valid_account(m: re.Match[str]) -> bool:
    parts = re.split(HY, m.group(0))
    if [len(p) for p in parts] == [3, 2, 5]:  # 사업자등록번호 형식은 제외
        return False
    return 10 <= sum(map(len, parts)) <= 16


_VALIDATORS = {"rrn": _valid_rrn, "account": _valid_account}


def find_pii(
    text: str,
    kinds: Iterable[str] | None = None,
    keywords: Iterable[str] = (),
    regexes: Iterable[str] = (),
) -> list[Match]:
    """text 에서 개인정보를 찾는다. 겹치는 결과는 우선순위가 높은 것만 남긴다."""
    enabled = list(KINDS) if kinds is None else [k for k in KINDS if k in set(kinds)]
    found: list[Match] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < f.end and f.start < e for f in found)

    def add(kind: str, s: int, e: int, ms: int, me: int) -> None:
        if e > s and not overlaps(s, e):
            found.append(Match(kind, s, e, ms, me, text[s:e]))

    # 사용자 지정 항목이 가장 우선
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
            add(CUSTOM_KIND, m.start(), m.end(), m.start(), m.end())
    for rx in regexes:
        if not rx.strip():
            continue
        for m in re.finditer(rx, text):
            add(CUSTOM_KIND, m.start(), m.end(), m.start(), m.end())

    for kind in enabled:
        validator = _VALIDATORS.get(kind)
        for m in _PATTERNS[kind].finditer(text):
            if validator and not validator(m):
                continue
            add(kind, m.start(), m.end(), m.start("m"), m.end("m"))

    found.sort(key=lambda f: f.start)
    return found
