"""합성 메일 세트로 업무 추출 정확도를 측정한다(07단계 검증). 수치만 출력한다.

사용법(저장소 루트에서):
    .venv\\Scripts\\python scripts\\measure_extraction.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collectors.demo_data import DEMO_TASK_THREADS, demo_messages  # noqa: E402
from config.rules import load_mail_rules  # noqa: E402
from domain.clock import KST  # noqa: E402
from domain.mail import group_threads  # noqa: E402
from domain.tasks.extract import TaskExtractor  # noqa: E402
from integrations.ai.fake import FakeKnowledgeAI  # noqa: E402
from security.masking import MaskedText  # noqa: E402


class _Classifier:
    def __init__(self) -> None:
        self._ai = FakeKnowledgeAI()

    def classify_task(self, summary: MaskedText, task_id: int | None = None) -> object:
        return self._ai.classify_task(summary)


def main() -> int:
    if isinstance(sys.stdout, __import__("io").TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    base = datetime(2026, 11, 5, tzinfo=KST)
    threads = load_mail_rules().filter_threads(
        group_threads(demo_messages(base - timedelta(days=0)))
    )
    for label, extractor in (
        ("규칙만", TaskExtractor(None)),
        ("규칙+AI(가짜)", TaskExtractor(_Classifier())),  # type: ignore[arg-type]
    ):
        found = {t.thread_id for t in threads if extractor.extract(t) is not None}
        hit = len(found & DEMO_TASK_THREADS)
        precision = hit / len(found) if found else 0.0
        recall = hit / len(DEMO_TASK_THREADS)
        print(
            f"{label}: 추출 {len(found)}건, 정답 {len(DEMO_TASK_THREADS)}건, "
            f"정밀도 {precision:.0%}, 재현율 {recall:.0%}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
