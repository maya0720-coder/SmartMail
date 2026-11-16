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
        self.cases: list[list[MaskedText]] = []

    def suggest_solution(
        self,
        summary: MaskedText,
        similar_cases: Sequence[MaskedText] = (),
        task_id: int | None = None,
    ) -> AiAnswer:
        self.calls += 1
        self.received.append(summary)
        self.cases.append(list(similar_cases))
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


def test_srch01_app_hit_is_attached_to_ai_suggestion(env: Env) -> None:
    """앱 이력을 찾으면 그 사례를 근거로 붙여 AI 해결 방안까지 만든다(결정기록 0021)."""
    env.done_task_with_card("전표 역분개 오류", "OB52 오픈")
    outcome = env.pipeline.search("역분개")
    assert outcome.resolved is ResolutionStage.APP  # 해결 단계는 가장 앞에서 찾은 단계
    assert [s.ran for s in outcome.stages] == [True, True, True]
    assert env.advisor.calls == 1 and outcome.stages[2].cases_used >= 1
    assert "OB52" in env.advisor.cases[0][0].value  # 사례(조치)가 AI 입력에 들어감


def test_srch01_find_only_does_not_call_ai(env: Env) -> None:
    env.done_task_with_card("전표 역분개 오류", "OB52 오픈")
    outcome = env.pipeline.search("역분개", suggest=False)
    assert outcome.resolved is ResolutionStage.APP and env.advisor.calls == 0
    assert [s.ran for s in outcome.stages] == [True, True, False]


def test_srch02_gk_hit_attached_when_no_app_history(env: Env) -> None:
    task_id = env.done_task_with_card("출하 인터페이스 실패", "재처리")
    env.knowledge.register(task_id, env.knowledge.preview(task_id))
    env.history._history = SqliteHistoryRepository(Database.in_memory())  # 앱 기록 없음 상황
    outcome = env.pipeline.search("출하 인터페이스")
    assert outcome.resolved is ResolutionStage.GKNOWLEDGE
    assert env.advisor.calls == 1
    assert env.advisor.cases[0][0].value.startswith("[G-Knowledge 문서]")


def test_hist04_partial_word_match_and_tcode_weight(env: Env) -> None:
    """업무 제목 전체로 검색해도(모든 단어 일치 아님) 이력을 찾고, T-code가 같으면 위로."""
    env.done_task_with_card("원가 보고서 조회 권한", "KSB1 역할 부여")
    env.done_task_with_card("전표 조회 권한 요청", "FB03 역할 부여")
    hits = env.history.search("KSB1 원가센터 보고서 조회 권한 부여 요청")
    assert hits and hits[0].card.problem == "원가 보고서 조회 권한"
    assert env.history.search("완전히 무관한 단어 모음") == []


def test_ai_context_uses_task_digest_not_only_title(env: Env, clock: FixedClock) -> None:
    """업무 맥락(구조화 요약·메일 발췌, 마스킹)이 AI 입력으로 가고, 제목만 가지 않는다."""
    from domain.models import MailRef
    from domain.search.context import TaskContextBuilder

    rrn = synthetic_rrn()
    task = env.tasks.add_manual("배치 지연 확인")
    assert task.id is not None
    env.collector.add(
        MailMessage(
            key=MailKey(MailSource.OUTLOOK, "C1", "<c1@x.invalid>"),
            thread_id="TC",
            subject="야간 배치 지연",
            sender_name="가상",
            received_at=clock.now(),
            folder="받은 편지함",
            body=f"안녕하세요.\nSM37에서 ZJOB_NIGHT가 2시간 지연됩니다. 대상 {rrn}\n감사합니다.",
        )
    )
    refs = SqliteMailRefRepository(env.db)
    ref = refs.add(
        MailRef(MailSource.OUTLOOK, "C1", "<c1@x.invalid>", "TC", clock.now(), task_id=task.id)
    )
    assert ref.id is not None
    refs.set_digest(ref.id, "요청 사항:\n- 배치 지연 원인 확인\n기한: 금일 중")
    builder = TaskContextBuilder(env.tasks, refs, env.mail)
    env.pipeline._context = builder.build
    env.pipeline.search("배치 지연 확인", task_id=task.id)
    sent = env.advisor.received[0].value
    assert "SM37" in sent and "배치 지연 원인 확인" in sent and "금일 중" in sent
    assert rrn not in sent and "감사합니다" not in sent


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
