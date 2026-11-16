"""데모(공모전 시연)용 가상 과거 사례·가상 G-Knowledge 문서 넣기(결정기록 0021).

- 데모 모드 + SMARTMAIL_DEMO_SEED=true일 때만, 시작할 때 메모리 DB·가짜 저장소에 넣는다.
- 내용은 모두 합성 데이터다(collectors/demo_scenarios.py). 실제 메일·사람 정보 없음.
- 과거 사례는 '완료된 수동 업무 + 히스토리 카드'로 넣어 '유사 이력' 검색과
  사례 근거 제안을 보여 준다. 이번 주 메일 스레드의 카드는 넣지 않는다
  (시연에서 '히스토리 카드' 버튼으로 직접 만드는 흐름 유지).
"""

from __future__ import annotations

from typing import Final

from collectors.demo_scenarios import DEMO_HISTORY, DEMO_KNOWLEDGE
from domain.history_service import CardDraft, HistoryService
from domain.knowledge import KnowledgeStore, MaskedDraft
from domain.models import TaskStatus
from domain.tasks.service import TaskService
from security.masking import mask

PAST_PREFIX: Final = "DEMO-PAST-"


def seed_demo(tasks: TaskService, history: HistoryService, store: KnowledgeStore) -> None:
    for case in DEMO_HISTORY:
        if not case.thread_id.startswith(PAST_PREFIX):
            continue
        task = tasks.add_manual(case.title)
        if task.id is None:
            continue
        tasks.change_status(task.id, TaskStatus.DONE)
        history.save(
            task.id, CardDraft(case.problem, case.cause, case.action, case.tcode), case.tags
        )
    for doc in DEMO_KNOWLEDGE:
        store.register(MaskedDraft(mask(doc.title), mask(doc.title), ()))
