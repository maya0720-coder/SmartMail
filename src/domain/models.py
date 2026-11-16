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
    SNOOZED = "snoozed"  # 제안 '나중에'(결정기록 0015)
    TRIAGE_REVERTED = "triage_reverted"  # 결정 취소(detail: approved|snoozed|dismissed)


# 제안 이유 코드(결정기록 0015, 명세 3-2). 화면 문구는 화면에서 바꾼다.
REASON_CODES: tuple[str, ...] = (
    "request_phrase",
    "subject_request",
    "due_phrase",
    "to_me",
    "cc_only",
    "thread_waiting",
    "approval",
    "ai_judged",
    "learned_penalty",
    "ai_unavailable",  # AI 판단 실패로 확신도 낮게 남김(결정기록 0021)
)
MAX_REASONS = 6
WEEKLY_SECTIONS: tuple[str, ...] = ("done", "planned", "issues", "none")


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
    summary: str | None = None  # 요청 요약 제목(처리했으면 값이 있다)
    task_id: int | None = None
    id: int | None = None
    # 구조화 요약(마스킹된 AI 요약: 요청 사항·기한·필요 조치, 결정기록 0021). 원문 아님
    digest: str | None = None
    summary_tries: int = 0  # 요약 실패 횟수(상한에 닿으면 더 보내지 않음)
    # 요약 상태(결정기록 0029): pending|done|kept|missing. None이면 summary로 추정
    summary_state: str | None = None
    direction: str | None = None  # 메일 방향(결정기록 0028): in(받음)|out(보냄). 기존 행은 None


# 요약 상태 값(결정기록 0029). running은 저장하지 않고 API에서 합성한다.
SUMMARY_PENDING: str = "pending"
SUMMARY_DONE: str = "done"
SUMMARY_KEPT: str = "kept"
SUMMARY_MISSING: str = "missing"
MAIL_IN: str = "in"
MAIL_OUT: str = "out"


def summary_state_of(ref: MailRef) -> str:
    """저장된 요약 상태. 값이 없는(이관 전에 생긴) 행은 summary 유무로 추정한다."""
    if ref.summary_state is not None:
        return ref.summary_state
    return SUMMARY_PENDING if ref.summary is None else SUMMARY_KEPT


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
    # 제안 트리아지(결정기록 0015). 이유는 코드값만, 문장·주소는 저장하지 않는다.
    snooze_until: datetime | None = None  # 이 시각 전에는 제안 목록에서 숨김
    confidence: float | None = None  # 추출 확신도(0~1)
    reasons: tuple[str, ...] = ()  # 제안 이유 코드(REASON_CODES)
    weekly_section: str | None = None  # 주간보고 위치(사용자 선택): done|planned|issues|none
    # 스레드 최종 메일(결정기록 0028): 행 정렬·묶음 기준. 없으면 requested_at
    last_mail_at: datetime | None = None
    last_mail_direction: str | None = None  # in|out
    # 진행 요약(결정기록 0028): 마스킹된 AI 진행 요약과 요약 당시 메일 수
    progress_digest: str | None = None
    progress_ref_count: int | None = None
    # 요청자 구분(결정기록 0027): internal|external|freemail, 외부 회사 도메인(외부일 때만)
    requester_kind: str | None = None
    requester_domain: str | None = None
    # 회신 대기(결정기록 0038): 내가 보낸 질문·요청 메일 시각(보조 표시, 상태 체계와 별개)
    awaiting_reply_since: datetime | None = None
    followup_dismissed: bool = False  # 사용자가 '대기 해제'를 누름(같은 메일로 다시 켜지지 않음)
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
