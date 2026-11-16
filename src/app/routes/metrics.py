"""성과 지표 API(MET-01~04): GET /api/metrics?grain=year|half|quarter|month|week."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.deps import get_container
from domain.metrics.aggregate import METRIC_NAMES, MetricValue, aggregate, cards
from domain.metrics.ai_quality import ai_quality
from domain.metrics.periods import Grain
from storage.repositories import SqliteEventRecorder

router = APIRouter(prefix="/api")


def _value_json(value: MetricValue) -> dict[str, Any]:
    return {
        "value": value.value,
        "numerator": value.numerator,
        "denominator": value.denominator,
        "sample": value.sample,
        "reference_only": value.reference_only,
    }


@router.get("/metrics/ai-quality")
def ai_quality_metrics(request: Request) -> JSONResponse:
    """AI 해결 방안 '도움 됨' 비율·AI 호출 성공률(최근 30일, 결정기록 0021). 개수만 낸다."""
    container = get_container(request)
    quality = ai_quality(SqliteEventRecorder(container.db, container.clock), container.clock.now())
    return JSONResponse(
        {
            "window_days": quality.window_days,
            "helpful": quality.helpful,
            "not_helpful": quality.not_helpful,
            "helpful_rate": quality.helpful_rate,
            "ai_success": quality.ai_success,
            "ai_failure": quality.ai_failure,
            "ai_success_rate": quality.ai_success_rate,
            "reference_only": quality.reference_only,
        }
    )


@router.get("/metrics")
def metrics(request: Request, grain: Grain = "month") -> JSONResponse:
    container = get_container(request)
    series = aggregate(container.ledger.list_entries(), container.clock.now(), grain)
    return JSONResponse(
        {
            "grain": grain,
            "target_due_rate": container.settings.metrics_due_target,
            "target_confirmed": False,  # 도입 초기 4주 기준값 측정 후 IE팀 확정
            "names": METRIC_NAMES,
            "buckets": [
                {
                    "label": b.bucket.label,
                    "start": b.bucket.start.isoformat(),
                    "partial": b.partial,
                    "values": {m: _value_json(v) for m, v in b.values.items()},
                }
                for b in series
            ],
            "cards": [
                {
                    "metric": c.metric,
                    "name": METRIC_NAMES[c.metric],
                    "current": _value_json(c.current),
                    "previous": _value_json(c.previous),
                    "delta": c.delta,
                    "trend": c.trend,
                }
                for c in cards(series)
            ],
        }
    )
