"""작업 센터(U1, 결정기록 0035): 여러 백그라운드 작업의 상태를 한 목록으로 합친다.

새로 저장하는 것은 없다. 각 작업이 이미 메모리에 가진 상태만 읽는다.
대상 문구는 마스킹된 업무 제목뿐이며, 실패 사유는 코드 값으로만 내보낸다(화면이 문구로 바꾼다).
"""

from __future__ import annotations

from typing import Any, Final

from app.container import Container
from app.gk_job import ACTIVE as GK_ACTIVE
from domain.clock import KST

MAX_JOBS: Final = 50
RUNNING: Final = "running"
DONE: Final = "done"
FAILED: Final = "failed"
WAITING: Final = "waiting"


def _iso(value: Any) -> str | None:
    return None if value is None else value.astimezone(KST).isoformat()


def _gk_group(state: str) -> str:
    if state in GK_ACTIVE:
        return RUNNING
    return DONE if state == "requested" else FAILED


def _gk_rows(container: Container) -> list[dict[str, Any]]:
    rows = []
    for status in container.gk_queue.recent():
        state = status.state
        rows.append(
            {
                "kind": "gk",
                "id": f"gk-{status.task_id}",
                "task_id": status.task_id,
                "target": status.title,  # 마스킹된 제목(메모리)
                "state": state,
                "group": _gk_group(state),
                "step": status.step,
                "reason": status.reason,
                "at": _iso(status.at),
                "retry": state in ("failed_unsent", "unknown"),
            }
        )
    return rows


def _summary_row(container: Container) -> dict[str, Any] | None:
    job = container.summary_job
    state = job.state
    pending = len(job.pending_ids())
    if not state.running and not pending:
        return None
    ready = job.ready()
    return {
        "kind": "summary",
        "id": "summary",
        "state": RUNNING if state.running else WAITING,
        "group": RUNNING if state.running else WAITING,
        "total": state.total,
        "completed": state.completed,
        "pending": pending,
        # 로그인이 없으면 조용히 건너뛴다(창을 띄우지 않음, 결정기록 0034)
        "reason": None if ready else "login_required",
        "retry": not state.running and ready,
    }


def _mail_row(container: Container) -> dict[str, Any]:
    syncing = container.mail_lock.locked()
    return {
        "kind": "mail",
        "id": "mail",
        "state": RUNNING if syncing else DONE,
        "group": RUNNING if syncing else DONE,
        "at": _iso(container.mail.last_synced_at),
        "retry": not syncing,
    }


def _company_row(container: Container) -> dict[str, Any] | None:
    job = container.company_job
    if not job.running and not job.last_saved:
        return None
    return {
        "kind": "company",
        "id": "company",
        "state": RUNNING if job.running else DONE,
        "group": RUNNING if job.running else DONE,
        "saved": job.last_saved,
        "at": _iso(job.last_run_at),
        "retry": False,
    }


def collect_jobs(container: Container) -> dict[str, Any]:
    """작업 센터 목록과 진행 중 건수."""
    rows: list[dict[str, Any]] = _gk_rows(container)
    for extra in (_summary_row(container), _mail_row(container), _company_row(container)):
        if extra is not None:
            rows.append(extra)
    rows = rows[:MAX_JOBS]
    return {"jobs": rows, "active": sum(1 for r in rows if r["group"] == RUNNING)}
