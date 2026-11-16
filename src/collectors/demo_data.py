"""데모 모드·테스트용 합성 메일. 이름·주소·내용은 모두 가상이다(실제 인물·메일 아님).

기본 9건(DEMO-T1~T7)은 기존 테스트 호환을 위해 그대로 두고, 확장 시나리오는
`demo_scenarios`·`demo_scenarios2`의 명세(MailSpec)를 조립해 덧붙인다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from collectors.demo_scenarios import DOMAIN, SCENARIO_MAILS_1, MailSpec
from collectors.demo_scenarios2 import SCENARIO_MAILS_2
from domain.mail import MailKey, MailMessage
from domain.models import MailSource

# 합성 메일 중 업무로 승인해야 할 스레드(추출 정확도 측정용 정답)
DEMO_TASK_THREADS = frozenset(
    {
        "DEMO-T1",
        "DEMO-T2",
        "DEMO-T3",
        "DEMO-T4",
        "DEMO-T7",
        "DEMO-T8",
        "DEMO-T9",
        "DEMO-T10",
        "DEMO-T11",
        "DEMO-T12",
        "DEMO-T13",
        "DEMO-T14",
        "DEMO-T15",
        "DEMO-T16",
        "DEMO-T17",
    }
)


def _message(
    n: int,
    thread: str,
    subject: str,
    sender: str,
    when: datetime,
    body: str,
    *,
    sent_by_me: bool = False,
    to_me: bool = True,
    cc_me: bool = False,
    address: str | None = None,
    is_auto: bool = False,
    attachments: tuple[str, ...] = (),
) -> MailMessage:
    return MailMessage(
        key=MailKey(MailSource.OUTLOOK, f"DEMO-ENTRY-{n:04d}", f"<demo-{n:04d}@{DOMAIN}>"),
        thread_id=thread,
        subject=subject,
        sender_name=sender,
        sender_address=address or f"user{n:02d}@{DOMAIN}",
        received_at=when,
        folder="보낸 편지함" if sent_by_me else "받은 편지함",
        sent_by_me=sent_by_me,
        to_me=to_me and not sent_by_me,
        cc_me=cc_me,
        is_auto=is_auto,
        body=body,
        attachment_names=attachments,
    )


def _from_spec(week_start: datetime, spec: MailSpec) -> MailMessage:
    return _message(
        spec.n,
        spec.thread,
        spec.subject,
        spec.sender,
        week_start + spec.at,
        spec.body,
        sent_by_me=spec.sent_by_me,
        to_me=spec.to_me,
        cc_me=spec.cc_me,
        address=spec.address,
        is_auto=spec.is_auto,
        attachments=spec.attachments,
    )


def demo_messages(week_start: datetime) -> list[MailMessage]:
    """week_start(목 00:00) 기준 한 주 분량(6일 안)의 합성 메일."""
    scenarios = [_from_spec(week_start, s) for s in (*SCENARIO_MAILS_1, *SCENARIO_MAILS_2)]
    return _base_messages(week_start) + scenarios


def _base_messages(week_start: datetime) -> list[MailMessage]:
    """기존 기본 합성 메일 9건(DEMO-T1~T7). 통합 테스트가 제목·본문에 의존하므로 바꾸지 않는다."""
    d = week_start
    return [
        _message(
            1,
            "DEMO-T1",
            "전표 역분개 시 오류 발생 문의",
            "재무팀 이예시",
            d + timedelta(days=4, hours=14),
            "안녕하세요. 전표 역분개 시 기간 마감 오류가 발생합니다.\n"
            "확인 부탁드립니다. 11/12까지 처리 가능할까요?\n재무팀 이예시 드림",
            attachments=("오류화면.png",),
        ),
        _message(
            2,
            "DEMO-T1",
            "RE: 전표 역분개 시 오류 발생 문의",
            "본인",
            d + timedelta(days=5, hours=10),
            "확인 결과 전기기간이 닫혀 있어 오류가 났습니다. "
            "전기기간(OB52) 오픈 후 재처리했습니다.",
            sent_by_me=True,
        ),
        _message(
            3,
            "DEMO-T2",
            "[요청] PO 출력물 문구 변경",
            "구매팀 김가상",
            d + timedelta(days=1, hours=9),
            "PO 하단 결제 조건 문구 변경을 요청드립니다. 다음 주 수요일까지 반영 부탁드립니다.",
        ),
        _message(
            4,
            "DEMO-T3",
            "출하 I/F 실패 건 확인 부탁",
            "물류팀 최테스트",
            d + timedelta(hours=16),
            "SD 출하 인터페이스 실패 3건이 있습니다. 확인 부탁드립니다.",
        ),
        _message(
            5,
            "DEMO-T3",
            "RE: 출하 I/F 실패 건 확인 부탁",
            "본인",
            d + timedelta(days=1, hours=11),
            "원인 데이터(출하지점 누락) 수정 후 재처리 완료했습니다.",
            sent_by_me=True,
        ),
        _message(
            6,
            "DEMO-T4",
            "야간 배치 지연 현상",
            "IT운영팀 정더미",
            d + timedelta(days=5, hours=8),
            "야간 배치가 평소보다 2시간 지연되고 있습니다. 원인 확인 요청드립니다.",
        ),
        _message(
            7,
            "DEMO-T5",
            "[공지] 사내 시스템 정기 점검 안내",
            "IT운영팀",
            d + timedelta(days=2, hours=9),
            "이번 주 토요일 정기 점검이 있습니다.",
            to_me=False,
        ),
        _message(
            8,
            "DEMO-T6",
            "주간 IT 뉴스",
            "뉴스 발송",
            d + timedelta(days=3, hours=7),
            "이번 주 IT 소식입니다.",
            address=f"noreply@{DOMAIN}",
            is_auto=True,
        ),
        _message(
            9,
            "DEMO-T7",
            "CO 원가 보고서 조회 권한 요청",
            "원가팀 박샘플",
            d + timedelta(days=2, hours=15),
            "CO 원가 보고서(KSB1) 조회 권한이 필요합니다. 금일 중 부여 부탁드립니다.",
            to_me=False,
            cc_me=True,
        ),
    ]
