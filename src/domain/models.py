"""업무·이벤트·히스토리·지표 원장의 도메인 모델(기획서 7장).

순수 데이터 구조만 둔다. 저장·외부 호출은 `domain.ports`의 인터페이스를 통해서만 한다.
메일 본문·첨부·자격증명 필드는 두지 않는다(MAIL-08, AUTH-03).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TaskStatus(StrEnum):
    """업무 상태(TODO-02·03)."""

    SUGGESTED = "suggested"  # 제안됨
    WAITING = "waiting"  # 대기
    IN_PROGRESS = "in_progress"  # 진행 중
    DONE_CANDIDATE = "done_candidate"  # 완료 후보(회신 감지, TODO-05)
    DONE = "done"  # 완료
    ON_HOLD = "on_hold"  # 보류
    DISMISSED = "dismissed"  # 무시(제안 거절)


# 보관기간 계산 대상(종결) 상태. 종결 시각(closed_at)부터 보관 기간을 센다(RET-01).
CLOSED_STATUSES: frozenset[TaskStatus] = frozenset({TaskStatus.DONE, TaskStatus.DISMISSED})

STATUS_LABELS: dict[TaskStatus, str] = {
    TaskStatus.SUGGESTED: "제안됨",
    TaskStatus.WAITING: "대기",
    TaskStatus.IN_PROGRESS: "진행 중",
    TaskStatus.DONE_CANDIDATE: "완료 후보",
    TaskStatus.DONE: "완료",
    TaskStatus.ON_HOLD: "보류",
    TaskStatus.DISMISSED: "무시",
}


class TaskSource(StrEnum):
    MAIL = "mail"
    MANUAL = "manual"


class MailSource(StrEnum):
    OUTLOOK = "outlook"
    GROUPWARE = "groupware"


class TaskEventType(StrEnum):
    """업무 이벤트 종류(기획서 4.9 이벤트 기록)."""

    RECEIVED = "received"  # 수신
    SUGGESTED = "suggested"  # 제안
    APPROVED = "approved"  # 승인(수정 없이)
    APPROVED_EDITED = "approved_edited"  # 수정 후 승인
    EDITED = "edited"  # 수정
    DISMISSED = "dismissed"  # 무시
    STATUS_CHANGED = "status_changed"  # 상태 변경(detail: 새 상태)
    REPLIED = "replied"  # 회신 감지
    COMPLETED = "completed"  # 완료 확정
    GK_REGISTERED = "gk_registered"  # G-Knowledge 등록
    SEARCH_RESOLVED = "search_resolved"  # 유사 검색 해결 단계(detail: ResolutionStage)
    FEEDBACK = "feedback"  # 도움 됨/안 됨(detail: helpful|not_helpful)
    DRAFT_CREATED = "draft_created"  # 주간보고 초안 생성
    PUBLISHED = "published"  # 위키 게시
    AI_CALLED = "ai_called"  # g-llm 호출(detail: success|failure)


class ResolutionStage(StrEnum):
    """유사 업무 검색이 해결된 단계(SRCH-01~03, MET-04)."""

    APP = "app"
    GKNOWLEDGE = "gknowledge"
    AI = "ai"
    UNRESOLVED = "unresolved"


class Visibility(StrEnum):
    """히스토리 카드 공개 범위(HIST-05). 기본값은 팀."""

    PRIVATE = "private"
    TEAM = "team"
    COMPANY = "company"


@dataclass(frozen=True, slots=True)
class MailRef:
    """메일 참조. 본문·첨부는 담지 않는다(MAIL-08)."""

    source: MailSource
    entry_id: str
    message_id: str
    thread_id: str
    received_at: datetime
    summary: str | None = None  # 사용자 확인분만
    task_id: int | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class Task:
    title: str
    status: TaskStatus
    source: TaskSource
    created_at: datetime
    requester_dept: str | None = None
    requested_at: datetime | None = None
    due_at: datetime | None = None
    module_tag: str | None = None
    thread_id: str | None = None
    closed_at: datetime | None = None
    gk_link: str | None = None
    gk_registered_at: datetime | None = None
    shared: bool = False  # TODO-08: 웹 배포 단계용(로컬 버전 비활성)
    id: int | None = None

    @property
    def gk_registered(self) -> bool:
        return self.gk_link is not None


@dataclass(frozen=True, slots=True)
class TaskEvent:
    kind: TaskEventType
    occurred_at: datetime
    task_id: int | None = None
    detail: str | None = None  # 열거형 값만(자유 텍스트 금지)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class HistoryCard:
    """히스토리 카드(HIST-01). 사용자 확인 후 저장한다(HIST-02)."""

    task_id: int
    problem: str
    cause: str
    action: str
    created_at: datetime
    tcode: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    visibility: Visibility = Visibility.TEAM
    id: int | None = None


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """지표 원장 1행(기획서 4.9). 제목·본문·상대방 정보는 담지 않는다."""

    task_id: int
    completed_at: datetime
    assignee: str
    has_due: bool
    due_met: bool | None
    gk_registered: bool = False
    resolution_stage: ResolutionStage | None = None
    id: int | None = None
