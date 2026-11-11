"""지표 집계(MET-01~04).

- 비율은 기간 전체의 분자·분모를 합쳐 계산한다(주별 비율 평균 금지).
- 기한 없는 업무는 MET-02 분모, 유사 검색을 하지 않은 업무는 MET-04 분모에서 뺀다.
- 표본(분모) 5건 미만은 '참고'로 표시한다. 분모가 0이면 값은 None('-').
- 카드는 최근 완료 기간 vs 직전 기간. 진행 중 기간은 partial로 표시한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from domain.metrics.periods import Bucket, Grain, recent_buckets
from domain.models import LedgerEntry, ResolutionStage

MetricId = Literal["MET-01", "MET-02", "MET-03", "MET-04"]
METRIC_NAMES: Final[dict[MetricId, str]] = {
    "MET-01": "완료 건수",
    "MET-02": "기한 준수율",
    "MET-03": "지식 등록률",
    "MET-04": "이력 재사용률",
}
REFERENCE_SAMPLE: Final = 5


@dataclass(frozen=True, slots=True)
class MetricValue:
    numerator: int
    denominator: int
    is_count: bool = False

    @property
    def value(self) -> float | None:
        if self.is_count:
            return float(self.numerator)
        return None if self.denominator == 0 else self.numerator / self.denominator

    @property
    def sample(self) -> int:
        return self.numerator if self.is_count else self.denominator

    @property
    def reference_only(self) -> bool:
        return self.sample < REFERENCE_SAMPLE


@dataclass(frozen=True, slots=True)
class BucketMetrics:
    bucket: Bucket
    partial: bool
    values: dict[MetricId, MetricValue]


@dataclass(frozen=True, slots=True)
class Card:
    metric: MetricId
    current: MetricValue  # 최근 완료 기간
    previous: MetricValue  # 그 직전 기간
    delta: float | None
    trend: Literal["개선", "악화", "변화 없음", "참고", "-"]


def _ratio(
    entries: list[LedgerEntry],
    denom: Callable[[LedgerEntry], bool],
    num: Callable[[LedgerEntry], bool],
) -> MetricValue:
    pool = [e for e in entries if denom(e)]
    return MetricValue(sum(1 for e in pool if num(e)), len(pool))


def compute(entries: Iterable[LedgerEntry]) -> dict[MetricId, MetricValue]:
    rows = list(entries)
    reused = {ResolutionStage.APP, ResolutionStage.GKNOWLEDGE}
    return {
        "MET-01": MetricValue(len(rows), len(rows), is_count=True),
        "MET-02": _ratio(rows, lambda e: e.has_due, lambda e: e.due_met is True),
        "MET-03": _ratio(rows, lambda e: True, lambda e: e.gk_registered),
        "MET-04": _ratio(
            rows, lambda e: e.resolution_stage is not None, lambda e: e.resolution_stage in reused
        ),
    }


def aggregate(entries: Iterable[LedgerEntry], now: datetime, grain: Grain) -> list[BucketMetrics]:
    rows = list(entries)
    result = []
    for bucket in recent_buckets(now, grain):
        inside = [e for e in rows if bucket.start <= e.completed_at < bucket.end]
        result.append(BucketMetrics(bucket, bucket.end > now, compute(inside)))
    return result


def _trend(metric: MetricId, current: MetricValue, previous: MetricValue) -> Card:
    if current.value is None or previous.value is None:
        return Card(metric, current, previous, None, "-")
    delta = current.value - previous.value
    if metric == "MET-01":
        return Card(metric, current, previous, delta, "참고")  # 처리량은 방향 판단 없음
    if abs(delta) < 1e-9:
        trend: Literal["개선", "악화", "변화 없음"] = "변화 없음"
    else:
        trend = "개선" if delta > 0 else "악화"
    return Card(metric, current, previous, delta, trend)


def cards(series: list[BucketMetrics]) -> list[Card]:
    """최근 '완료된' 기간과 그 직전 기간 비교(진행 중 기간은 제외)."""
    complete = [b for b in series if not b.partial]
    if len(complete) < 2:
        empty = MetricValue(0, 0)
        return [Card(m, empty, empty, None, "-") for m in METRIC_NAMES]
    current, previous = complete[-1], complete[-2]
    return [_trend(m, current.values[m], previous.values[m]) for m in METRIC_NAMES]
