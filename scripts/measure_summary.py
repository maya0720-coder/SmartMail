"""합성 메일 세트로 요약 품질(핵심어 포함률)을 잰다(결정기록 0021). 수치만 출력한다.

데모 설정(가짜 AI, 메모리 DB, 가상 메일)으로 앱을 조립해 메일 수집 → 요약을 실행하고,
업무별로 메일의 핵심어(T-code·오류 번호·기한)가 제목·구조화 요약에 남았는지 센다.
실제 메일·실제 사이트는 쓰지 않는다. 실제 AI-Ground 품질은 앱의 '도움 됨' 비율로 본다.

사용법(저장소 루트에서):
    .venv\\Scripts\\python scripts\\measure_summary.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.bootstrap import build_container  # noqa: E402
from app.mail_sync import collect_and_ingest  # noqa: E402
from config.settings import Settings  # noqa: E402
from domain.clock import SystemClock  # noqa: E402
from domain.models import TaskSource  # noqa: E402
from domain.tasks.summary_eval import evaluate  # noqa: E402


def main() -> int:
    if isinstance(sys.stdout, __import__("io").TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        # 가상 주소(example.invalid)만 쓴다. 서버는 띄우지 않는다(설정 형식만 채움)
        values: dict[str, Any] = {
            "demo_mode": True,
            "demo_seed": False,
            "db_path": Path(tmp) / "eval.db",
            "log_dir": Path(tmp) / "logs",
            "mail_source": "groupware",
            "wiki_base_url": "https://wiki.example.invalid",
            "gknowledge_base_url": "https://gk.example.invalid",
            "aiground_base_url": "https://ai.example.invalid",
            "groupware_mail_base_url": "https://gw.example.invalid",
            "app_port": 8765,
        }
        settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
        container = build_container(settings, clock=SystemClock())
        try:
            container.summary_job.auto = False  # 백그라운드 실행 대신 아래에서 직접 실행
            now = container.clock.now()
            mail = container.mail
            collect_and_ingest(
                container, lambda: mail.collect(now - timedelta(days=7), now + timedelta(minutes=1))
            )
            for _ in range(5):
                container.summary_job.run_once(max_batches=20)
            pairs = []
            for task in container.tasks.list_tasks(None):
                if task.source is not TaskSource.MAIL or task.id is None:
                    continue
                refs = container.mail.refs_for_task(task.id)
                bodies = []
                for ref in refs:
                    view = container.mail.view(ref.id) if ref.id is not None else None
                    if (
                        view is not None
                        and view.message is not None
                        and not view.message.sent_by_me
                    ):
                        bodies.append(view.message.subject + "\n" + view.message.body)
                digest = refs[0].digest if refs else None
                pairs.append(("\n".join(bodies), f"{task.title}\n{digest}" if digest else None))
            result = evaluate(pairs)
        finally:
            container.ai.close()
    print(f"업무 {result.items}건, 핵심어 {result.expected}개 중 {result.covered}개 포함")
    print(f"핵심어 포함률: {result.coverage}, 요약 없음: {result.no_digest}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
