"""성과 지표 테스트(MET-01~04, 기획서 4.9)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from domain.clock import KST, FixedClock
from domain.metrics.aggregate import aggregate, cards, compute
from domain.metrics.periods import DISPLAY_COUNT, GRAINS, bucket_of, recent_buckets
from domain.models import LedgerEntry, ResolutionStage
from fixtures.app_factory import app_client, make_container


def _entry(
    n: int,
    when: datetime,
    *,
    due: bool | None = None,
    gk: bool = False,
    stage: ResolutionStage | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        n,
        when,
        "본인",
        has_due=due is not None,
        due_met=due,
        gk_registered=gk,
        resolution_stage=stage,
    )


def test_week_boundary_wednesday_to_thursday() -> None:
    wed = datetime(2026, 11, 18, 23, 59, 59, tzinfo=KST)
    thu = datetime(2026, 11, 19, 0, 0, tzinfo=KST)
    assert bucket_of(wed, "week").start != bucket_of(thu, "week").start
    assert bucket_of(thu, "week").start == thu


def test_half_and_year_boundaries() -> None:
    june = datetime(2026, 6, 30, 23, 59, tzinfo=KST)
    july = datetime(2026, 7, 1, 0, 0, tzinfo=KST)
    assert bucket_of(june, "half").label == "2026 상반기"
    assert bucket_of(july, "half").label == "2026 하반기"
    dec = datetime(2026, 12, 31, 23, 59, tzinfo=KST)
    jan = datetime(2027, 1, 1, tzinfo=KST)
    assert bucket_of(dec, "year").label == "2026" and bucket_of(jan, "year").label == "2027"
    assert bucket_of(dec, "quarter").label == "2026 Q4"
    assert bucket_of(jan, "month").label == "2027.01"


@pytest.mark.parametrize("grain", GRAINS)
def test_display_counts_and_continuity(grain: str) -> None:
    now = datetime(2026, 11, 12, 10, tzinfo=KST)
    buckets = recent_buckets(now, grain)  # type: ignore[arg-type]
    assert len(buckets) == DISPLAY_COUNT[grain]  # type: ignore[index]
    assert all(a.end == b.start for a, b in zip(buckets, buckets[1:], strict=False))
    assert buckets[-1].start <= now < buckets[-1].end


def test_zero_denominator_is_none() -> None:
    values = compute([])
    assert values["MET-02"].value is None and values["MET-04"].value is None
    assert values["MET-01"].value == 0


def test_ratio_pools_numerators_not_weekly_average() -> None:
    """주1: 1/1(100%), 주2: 1/9(11%) → 평균 55.6%가 아니라 합산 2/10 = 20%."""
    base = datetime(2026, 11, 2, tzinfo=KST)
    rows = [_entry(1, base, due=True)]
    rows += [_entry(10 + i, base + timedelta(days=7), due=(i == 0)) for i in range(9)]
    value = compute(rows)["MET-02"]
    assert value.value == pytest.approx(0.2)
    assert value.denominator == 10


def test_exclusions_from_denominators() -> None:
    base = datetime(2026, 11, 2, tzinfo=KST)
    rows = [
        _entry(1, base, due=None, stage=None),
        _entry(2, base, due=True, stage=ResolutionStage.APP, gk=True),
        _entry(3, base, due=False, stage=ResolutionStage.AI),
    ]
    values = compute(rows)
    assert (values["MET-02"].numerator, values["MET-02"].denominator) == (1, 2)
    assert (values["MET-03"].numerator, values["MET-03"].denominator) == (1, 3)
    assert (values["MET-04"].numerator, values["MET-04"].denominator) == (1, 2)
    assert values["MET-02"].reference_only  # 표본 5건 미만


def test_cards_compare_last_complete_periods() -> None:
    now = datetime(2026, 11, 12, 10, tzinfo=KST)  # 11월은 진행 중
    rows = [_entry(i, datetime(2026, 10, 5, tzinfo=KST), due=True) for i in range(6)]
    rows += [_entry(10 + i, datetime(2026, 9, 5, tzinfo=KST), due=(i < 3)) for i in range(6)]
    rows.append(_entry(99, datetime(2026, 11, 3, tzinfo=KST), due=False))
    series = aggregate(rows, now, "month")
    assert series[-1].partial and not series[-2].partial
    card = {c.metric: c for c in cards(series)}
    assert card["MET-02"].current.value == 1.0 and card["MET-02"].previous.value == 0.5
    assert card["MET-02"].trend == "개선"
    assert card["MET-01"].trend == "참고"


def test_metrics_api_and_grain_validation(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        for grain in GRAINS:
            body = client.get("/api/metrics", params={"grain": grain}).json()
            assert len(body["buckets"]) == DISPLAY_COUNT[grain]
            assert len(body["cards"]) == 4
        assert client.get("/api/metrics", params={"grain": "day"}).status_code == 422
        assert client.get("/api/metrics", params={"grain": "<script>"}).status_code == 422
