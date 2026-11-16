"""요약 품질 측정: 핵심어 포함률(결정기록 0021). 합성 평가 세트에만 쓴다(실제 메일 금지).

메일(전달 메일의 원래 요청·후속 메일 포함)에 있는 핵심어가 요약(제목 + 구조화 요약)에 얼마나
남았는지 잰다. 핵심어 = T-code, 오류 메시지 번호(예: 'CK 465'), 기한 표현.
사람이 매기는 평가를 대신하지 않는다. 형식·누락을 빠르게 비교하는 회귀 지표다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

TCODE: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,4}\d{1,3}[A-Z]?(?![A-Za-z0-9])")
MESSAGE_NO: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{0,2} \d{3}(?!\d)")
DUE: Final = re.compile(
    r"\d{1,2}/\d{1,2}(?=\s*(?:까지|중))|금일 중|오늘 중|내일까지|"
    r"(?:금주|이번 주|다음 주|차주) ?[월화수목금]요일"
)


def key_terms(text: str) -> set[str]:
    terms = set(TCODE.findall(text)) | set(MESSAGE_NO.findall(text))
    terms |= {m.group(0).replace(" ", "") for m in DUE.finditer(text)}
    return terms


@dataclass(frozen=True, slots=True)
class EvalResult:
    items: int
    expected: int
    covered: int
    no_digest: int

    @property
    def coverage(self) -> float | None:
        return None if self.expected == 0 else round(self.covered / self.expected, 3)


def evaluate(pairs: Iterable[tuple[str, str | None]]) -> EvalResult:
    """(메일 본문, 요약 텍스트) 쌍으로 핵심어 포함률을 잰다. 요약이 없으면 0점."""
    items = expected = covered = missing = 0
    for source, summary in pairs:
        items += 1
        terms = key_terms(source)
        expected += len(terms)
        if not summary:
            missing += 1
            continue
        compact = summary.replace(" ", "")
        covered += sum(term.replace(" ", "") in compact for term in terms)
    return EvalResult(items, expected, covered, missing)
