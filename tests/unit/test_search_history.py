"""히스토리 카드·3단계 이력 조회 테스트(HIST-01~06, SRCH-01~05, RET-01·02)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from collectors.fake import FakeMailCollector
from domain.ai import AiAnswer, AiUnavailableError
from domain.clock import FixedClock
from domain.history_service import CardDraft, HistoryService
from domain.knowledge_service import KnowledgeService
from domain.mail import MailKey, MailMessage
from domain.mail_rules import ExclusionRules
from domain.mail_service import MailService
from domain.models import MailSource, ResolutionStage, TaskEventType, TaskStatus
from domain.search.pipeline import NO_SOLUTION, SearchPipeline
from domain.tasks.extract import TaskExtractor
from domain.tasks.service import TaskService
from fixtures.synthetic import synthetic_rrn
from integrations.gknowledge.fake import FakeKnowledgeStore
from security.masking import MaskedText
from storage.db import Database
from storage.knowledge_repos import SqliteHistoryRepository, SqliteLedgerRepository
from storage.repositories import (
    SqliteEventRecorder,
    SqliteMailRefRepository,
    SqliteTaskRepository,
)
from storage.retention import RetentionJob


class CountingAdvisor:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.received: list[MaskedText] = []

    def suggest_solution(
        self,
        summary: MaskedText,
        similar_cases: Sequence[MaskedText] = (),
        task_id: int | None = None,
    ) -> AiAnswer:
        self.calls += 1
        self.received.append(summary)
        if self.fail:
            raise AiUnavailableError("확인 필요")
        return AiAnswer("AI 제안")


class Env:
    def __init__(
        self, db: Database, clock: FixedClock, advisor: CountingAdvisor | None = None
    ) -> None:
        self.clock = clock
        self.db = db
        self.events = SqliteEventRecorder(db, clock)
        self.collector = FakeMailCollector()
        self.mail = MailService(
            self.collector, SqliteMailRefRepository(db), self.events, ExclusionRules(), clock
        )
        self.tasks = TaskService(
            SqliteTaskRepository(db),
            SqliteMailRefRepository(db),
            self.events,
            SqliteLedgerRepository(db),
            clock,
            TaskExtractor(None),
            "본인",
        )
        self.store = FakeKnowledgeStore()
        self.knowledge = KnowledgeService(
            self.store, self.tasks, SqliteHistoryRepository(db), "본인"
        )
        self.history = HistoryService(SqliteHistoryRepository(db), self.tasks, self.mail, clock)
        self.advisor = advisor or CountingAdvisor()
        self.pipeline = SearchPipeline(
            self.history, self.knowledge, self.advisor, self.events, SqliteLedgerRepository(db)
        )

    def done_task_with_card(self, title: str, action: str) -> int:
        task = self.tasks.add_manual(title)
        assert task.id is not None
        self.tasks.change_status(task.id, TaskStatus.DONE)
        self.history.save(task.id, CardDraft(title, "원인", action, None), ("FI",))
        return task.id


@pytest.fixture
def env(db: Database, clock: FixedClock) -> Env:
    return Env(db, clock)


def test_srch01_app_hit_stops_before_gk_and_ai(env: Env) -> None:
    env.done_task_with_card("전표 역분개 오류", "OB52 오픈")
    outcome = env.pipeline.search("역분개")
    assert outcome.resolved is ResolutionStage.APP
    assert env.store.search_calls == 0 and env.advisor.calls == 0
    assert [s.ran for s in outcome.stages] == [True, False, False]


def test_srch02_gk_hit_skips_ai(env: Env) -> None:
    task_id = env.done_task_with_card("출하 인터페이스 실패", "재처리")
    env.knowledge.register(task_id, env.knowledge.preview(task_id))
    env.history._history = SqliteHistoryRepository(Database.in_memory())  # 앱 기록 없음 상황
    outcome = env.pipeline.search("출하 인터페이스")
    assert outcome.resolved is ResolutionStage.GKNOWLEDGE
    assert env.advisor.calls == 0


def test_srch03_ai_when_nothing_found_and_masked(env: Env) -> None:
    rrn = synthetic_rrn()
    outcome = env.pipeline.search(f"배치 지연 {rrn}")
    assert outcome.resolved is ResolutionStage.AI
    assert env.advisor.calls == 1
    assert rrn not in env.advisor.received[0].value


def test_all_stages_fail_reports_no_solution(db: Database, clock: FixedClock) -> None:
    env = Env(db, clock, CountingAdvisor(fail=True))
    outcome = env.pipeline.search("없는 업무")
    assert outcome.resolved is ResolutionStage.UNRESOLVED
    assert outcome.message == NO_SOLUTION
    assert outcome.stages[2].notice


def test_srch04_force_next_stage(env: Env) -> None:
    env.done_task_with_card("전표 역분개 오류", "OB52 오픈")
    outcome = env.pipeline.search("역분개", start_from="ai")
    assert outcome.resolved is ResolutionStage.AI
    assert [s.ran for s in outcome.stages] == [False, False, True]


def test_resolution_recorded_in_events_and_ledger(env: Env, db: Database) -> None:
    task_id = env.done_task_with_card("전표 역분개 오류", "OB52 오픈")
    env.pipeline.search("역분개", task_id=task_id)
    env.pipeline.feedback(task_id, helpful=True)
    details = [e.detail for e in env.events.list_events(kinds=[TaskEventType.SEARCH_RESOLVED])]
    assert details == ["app"]
    assert SqliteLedgerRepository(db).list_entries()[0].resolution_stage is ResolutionStage.APP
    assert env.events.list_events(kinds=[TaskEventType.FEEDBACK])[0].detail == "helpful"


def test_ret01_after_purge_found_in_gk(env: Env, db: Database, clock: FixedClock) -> None:
    task_id = env.done_task_with_card("원가 보고서 권한", "KSB1 권한 부여")
    env.knowledge.register(task_id, env.knowledge.preview(task_id))
    clock.advance(timedelta(days=7))
    RetentionJob(db, clock, retention_days=7, keep_ledger=False).run()
    assert env.history.search("원가") == []
    outcome = env.pipeline.search("원가 보고서")
    assert outcome.resolved is ResolutionStage.GKNOWLEDGE


def test_hist02_draft_from_reply(env: Env, clock: FixedClock) -> None:
    from domain.models import MailRef

    task = env.tasks.add_manual("전표 오류")
    assert task.id is not None
    reply = MailMessage(
        key=MailKey(MailSource.OUTLOOK, "R1", "<r1@x.invalid>"),
        thread_id="T",
        subject="RE: 전표 오류",
        sender_name="본인",
        received_at=clock.now(),
        folder="보낸 편지함",
        sent_by_me=True,
        body="확인 결과 전기기간이 닫혀 있었습니다.\nOB52에서 기간 오픈 후 재처리했습니다.",
    )
    env.collector.add(reply)
    ref = SqliteMailRefRepository(env.db).add(
        MailRef(MailSource.OUTLOOK, "R1", "<r1@x.invalid>", "T", clock.now(), task_id=task.id)
    )
    assert ref.id
    draft = env.history.draft(task.id)
    assert "전기기간" in draft.cause
    assert "재처리" in draft.action
    assert draft.tcode == "OB52"


def test_hist03_filters_and_card_masked(env: Env) -> None:
    rrn = synthetic_rrn()
    task_id = env.done_task_with_card("FI 결산", f"주민번호 {rrn} 고객 처리")
    card = env.history.get(task_id)
    assert card is not None and rrn not in card.action
    assert env.history.search("결산", tag="FI")
    assert env.history.search("결산", tag="MM") == []
    assert env.history.search("결산", module="SD") == []


def test_hist01_save_requires_action(env: Env) -> None:
    task = env.tasks.add_manual("업무")
    assert task.id is not None
    with pytest.raises(ValueError):
        env.history.save(task.id, CardDraft("문제", "", " ", None))
