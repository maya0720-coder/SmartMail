"""외부 메일 요청부서 = 회사명 테스트(TODO-01·MAIL-03, 결정기록 0027).

합성 도메인(example.* / .invalid)과 가짜 AI만 쓴다. 실제 사이트에 접속하지 않는다.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.company_job import CompanyJob
from domain.ai import AiUnavailableError
from domain.clock import FixedClock
from domain.models import Task, TaskSource, TaskStatus
from domain.tasks.requester import (
    FREEMAIL_LABEL,
    KIND_EXTERNAL,
    KIND_FREEMAIL,
    KIND_INTERNAL,
    CompanyResolver,
    RequesterClassifier,
    normalize_domain,
    valid_company_name,
)
from fixtures.app_factory import app_client, make_container
from integrations.ai.fake import FakeKnowledgeAI
from integrations.ai.gateway import AiGateway, PayloadRejectedError
from security.logging_policy import Site
from security.masking import MaskedText, mask
from security.sessions import SessionStore
from storage.company_repo import SqliteCompanyDirectory
from storage.db import Database
from storage.knowledge_repos import SqliteAiCallLog
from storage.repositories import SqliteEventRecorder, SqliteTaskRepository

INTERNAL = ("corp.example",)
FREE = ("gmail.com", "naver.com")


def _classifier() -> RequesterClassifier:
    return RequesterClassifier(INTERNAL, FREE)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Kim <kim@Partner.Example.CO.KR>", "partner.example.co.kr"),
        ("a@예시.한국", "예시.한국".encode("idna").decode("ascii")),
        ("not-an-address", None),
        ("a@localhost", None),
        ("a@10.0.0.1", None),
        ("a@-bad-.com", None),
    ],
)
def test_todo01_normalize_domain(value: str, expected: str | None) -> None:
    assert normalize_domain(value) == expected


def test_todo01_classify_internal_external_freemail() -> None:
    classifier = _classifier()
    internal = classifier.classify("a@mail.corp.example")  # 하위 도메인도 내부
    assert internal is not None and internal.kind == KIND_INTERNAL and internal.domain is None
    free = classifier.classify("someone@gmail.com")
    assert free is not None and free.kind == KIND_FREEMAIL and free.domain is None
    external = classifier.classify("b@sales.vendor.example")
    assert external is not None and external.kind == KIND_EXTERNAL
    assert external.domain == "sales.vendor.example"


def test_todo01_classifier_disabled_without_internal_domains() -> None:
    assert RequesterClassifier((), FREE).classify("a@vendor.example") is None


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ("예시상사", True),
        ("https://vendor.example", False),
        ("info@vendor.example", False),
        ("12345", False),
        ("모름", False),
        ("가" * 51, False),
        ("<b>회사</b>", False),
    ],
)
def test_todo01_company_name_validation(raw: str, ok: bool) -> None:
    assert (valid_company_name(raw) is not None) is ok


def _resolver(tmp_path: Path, clock: FixedClock) -> tuple[CompanyResolver, SqliteCompanyDirectory]:
    db = Database(tmp_path / "c.db")
    cache = SqliteCompanyDirectory(db, clock)
    return CompanyResolver({"vendor.example": "예시상사"}, cache), cache


def test_todo01_mapping_subdomain_and_user_priority(tmp_path: Path, clock: FixedClock) -> None:
    resolver, cache = _resolver(tmp_path, clock)
    assert resolver.label(KIND_EXTERNAL, "mail.vendor.example") == ("예시상사", "rule")
    assert resolver.label(KIND_EXTERNAL, "other.example") == ("외부(other.example)", "domain")
    assert resolver.label(KIND_FREEMAIL, None) == (FREEMAIL_LABEL, "freemail")
    assert resolver.label(KIND_INTERNAL, None) == (None, None)
    cache.save("other.example", "AI추정회사", "ai")
    assert resolver.label(KIND_EXTERNAL, "other.example") == ("AI추정회사", "ai")
    # 사용자 확정값은 AI 값과 매핑보다 우선한다
    resolver.confirm("other.example", "확정회사")
    resolver.confirm("vendor.example", "새이름상사")
    assert resolver.label(KIND_EXTERNAL, "other.example") == ("확정회사", "user")
    assert resolver.label(KIND_EXTERNAL, "mail.vendor.example") == ("새이름상사", "user")
    with pytest.raises(ValueError):
        resolver.confirm("other.example", "https://x.example")


class RecordingNamer:
    def __init__(self, answer: str | None = "예시물산", fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.sent: list[str] = []

    def company_name(self, domain: MaskedText) -> str | None:
        self.sent.append(domain.value)
        if self.fail:
            raise AiUnavailableError("로그인 필요")
        return self.answer


def _job(
    tmp_path: Path, clock: FixedClock, namer: RecordingNamer, tasks: list[Task]
) -> tuple[CompanyJob, SqliteCompanyDirectory, CompanyResolver]:
    resolver, cache = _resolver(tmp_path, clock)

    class Lister:
        def list_tasks(self, statuses: object = None) -> list[Task]:
            return tasks

    store = SessionStore(clock, timedelta(minutes=30))
    job = CompanyJob(Lister(), resolver, cache, namer, store, clock, always_ready=True)
    return job, cache, resolver


def _task(kind: str | None, domain: str | None, clock: FixedClock) -> Task:
    return Task(
        "합성 업무",
        TaskStatus.SUGGESTED,
        TaskSource.MAIL,
        clock.now(),
        requester_kind=kind,
        requester_domain=domain,
    )


def test_todo01_company_job_asks_domain_only_and_caches(tmp_path: Path, clock: FixedClock) -> None:
    namer = RecordingNamer()
    tasks = [
        _task(KIND_EXTERNAL, "other.example", clock),
        _task(KIND_EXTERNAL, "mail.vendor.example", clock),  # 매핑으로 이미 찾음
        _task(KIND_FREEMAIL, None, clock),  # 개인 메일: AI 호출 없음
        _task(KIND_INTERNAL, None, clock),
    ]
    job, cache, resolver = _job(tmp_path, clock, namer, tasks)
    assert job.run_once() == 1
    assert namer.sent == ["other.example"]  # 도메인 문자열만
    assert resolver.label(KIND_EXTERNAL, "other.example") == ("예시물산", "ai")
    assert job.run_once() == 0 and namer.sent == ["other.example"]  # 캐시 후 다시 묻지 않음


def test_todo01_company_job_failure_keeps_domain_label(tmp_path: Path, clock: FixedClock) -> None:
    namer = RecordingNamer(fail=True)
    job, _cache, resolver = _job(tmp_path, clock, namer, [_task(KIND_EXTERNAL, "x.example", clock)])
    assert job.run_once() == 0
    assert resolver.label(KIND_EXTERNAL, "x.example") == ("외부(x.example)", "domain")
    unknown = RecordingNamer(answer=None)
    job2, _c, _r = _job(tmp_path / "b", clock, unknown, [_task(KIND_EXTERNAL, "y.example", clock)])
    job2.run_once()
    job2.run_once()
    assert unknown.sent == ["y.example"]  # 모른다는 답은 잠시 쉬었다가 다시 묻는다


def test_todo01_gateway_rejects_non_domain_and_bad_answers(
    tmp_path: Path, clock: FixedClock
) -> None:
    db = Database(tmp_path / "g.db")
    fake = FakeKnowledgeAI()
    gateway = AiGateway(
        fake, SqliteAiCallLog(db), SqliteEventRecorder(db, clock), clock, timeout_seconds=5
    )
    assert gateway.company_name(mask("vendor.example")) == "VENDOR(가짜)"
    assert [m.value for m in fake.received] == ["vendor.example"]
    for bad in ("홍길동 vendor.example", "a@vendor.example", "제목: 견적 요청"):
        with pytest.raises(PayloadRejectedError):
            gateway.company_name(mask(bad))

    class BadAnswers:
        name = "bad"

        def __init__(self, answer: str) -> None:
            self.answer = answer

        def company_name(self, domain: MaskedText) -> str | None:
            return self.answer

    for answer in ("https://vendor.example", "sales@vendor.example", "가" * 51, "2024"):
        gw = AiGateway(
            BadAnswers(answer),  # type: ignore[arg-type]
            SqliteAiCallLog(db),
            SqliteEventRecorder(db, clock),
            clock,
            timeout_seconds=5,
        )
        assert gw.company_name(mask("vendor.example")) is None


def test_todo01_ingest_sets_requester_kind_and_api_label(tmp_path: Path, clock: FixedClock) -> None:
    container = make_container(tmp_path, clock)
    with app_client(container) as client:
        client.post("/api/mail/collect", json={})
        tasks = client.get("/api/tasks").json()
    # 데모 도메인은 내부로 본다: 가상 부서명이 그대로 보인다
    mail_tasks = [t for t in tasks if t["source"] == "mail"]
    assert mail_tasks and all(t["requester_kind"] == KIND_INTERNAL for t in mail_tasks)
    assert all(t["requester_label"] == t["requester_dept"] for t in mail_tasks)
    # 외부 도메인 업무는 회사명(또는 외부(도메인))으로 표시되고 확정 API가 같은 도메인에 적용된다
    repo = SqliteTaskRepository(container.db)
    task = repo.add(_task(KIND_EXTERNAL, "vendor2.example", clock))
    with app_client(container) as client:
        body = client.get(f"/api/tasks/{task.id}").json()
        assert body["requester_label"] == "외부(vendor2.example)"
        saved = client.put(
            "/api/companies", json={"domain": "vendor2.example", "company": "확정상사"}
        )
        assert saved.status_code == 200
        assert client.get(f"/api/tasks/{task.id}").json()["requester_label"] == "확정상사"
        bad = client.put("/api/companies", json={"domain": "vendor2.example", "company": "12"})
        assert bad.status_code == 400
        listed = client.get("/api/companies").json()
        assert listed == [{"domain": "vendor2.example", "company": "확정상사", "source": "user"}]
        gone = client.request("DELETE", "/api/companies/vendor2.example", json={})
        assert gone.status_code == 200
    assert container.sessions.current(Site.AIGROUND) is None  # AI 로그인 없이도 동작(가짜 AI)


def test_ret_wipe_all_includes_company_cache(tmp_path: Path, clock: FixedClock) -> None:
    db = Database(tmp_path / "w.db")
    SqliteCompanyDirectory(db, clock).save("vendor.example", "예시상사", "user")
    db.wipe_all()
    assert SqliteCompanyDirectory(db, clock).list_all() == []
