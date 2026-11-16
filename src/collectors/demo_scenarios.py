"""데모 모드용 합성 시나리오 데이터(1/2). 모든 이름·부서·주소·번호·내용은 가상이다.

실제 인물·회사·도메인·전화번호를 쓰지 않는다. 주소는 모두 `@example.invalid`, 내선은 '내선 000x'
형식의 명백한 가짜 값이다. 이 파일은 데이터만 정의하고, 앱 연결(시딩)은 하지 않는다.

정의하는 형식
- `MailSpec`: 합성 메일 한 건의 명세. `demo_data.demo_messages()`가 `MailMessage`로 조립한다.
    n(메일 일련번호, 메시지 ID에 쓰임), thread(스레드 ID "DEMO-T<번호>"), subject(제목),
    sender(표시 이름 "부서 이름"), at(기준 주 시작으로부터의 경과 시간), body(본문),
    sent_by_me·to_me·cc_me·is_auto(수신 구분), address(보낸 주소, 없으면 자동),
    attachments(첨부 파일명)
- `DemoHistoryCase`: 데모용 히스토리 카드(해결 사례).
    thread_id(이번 주 완료 스레드 "DEMO-T<번호>" 또는 과거 사례 "DEMO-PAST-<n>"),
    title(업무 제목), problem(문제), cause(원인), action(조치), tcode(대표 T-code), tags(태그 튜플)
- `DemoKnowledgeDoc`: 가상 G-Knowledge 지식 문서. title(제목), path("/knowledge/<숫자>")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

DOMAIN = "example.invalid"


@dataclass(frozen=True, slots=True)
class MailSpec:
    n: int
    thread: str
    subject: str
    sender: str
    at: timedelta
    body: str
    sent_by_me: bool = False
    to_me: bool = True
    cc_me: bool = False
    address: str | None = None
    is_auto: bool = False
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DemoHistoryCase:
    thread_id: str
    title: str
    problem: str
    cause: str
    action: str
    tcode: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DemoKnowledgeDoc:
    title: str
    path: str


def at(days: int, hours: int, minutes: int = 0) -> timedelta:
    """기준 주 시작(목 00:00)으로부터의 경과 시간."""
    return timedelta(days=days, hours=hours, minutes=minutes)


def addr(local: str) -> str:
    return f"{local}@{DOMAIN}"


def sign(name: str, dept: str, ext: str) -> str:
    """가상 서명(내선은 가짜 번호, 휴대폰 번호 없음)."""
    return (
        f"\n\n감사합니다.\n{name} 드림\n\n"
        "----------------------------------------\n"
        f"{name} | {dept}\n"
        f"본사 ○○동 3층 | 내선 {ext}\n"
        f"{addr('team-' + ext)}\n"
    )


# 가상 면책 문구(요청 표현이 들어가지 않게 작성)
DISCLAIMER = (
    "\n[면책 안내 - 가상 문구]\n"
    "본 메일과 첨부 파일은 지정된 수신자의 업무 목적에 한해 발송되었으며,\n"
    "비공개 정보가 포함되어 있을 수 있습니다. 수신 대상이 아닌 경우 발신자에게 알리고\n"
    "메일을 삭제해 주십시오. 무단 복제·배포·인용은 금지됩니다.\n"
    "This message is intended only for the named recipient (sample text).\n"
)


SCENARIO_MAILS_1: tuple[MailSpec, ...] = (
    # DEMO-T8: CO 표준원가(CK11N) — 전달(FW) 메일, 원래 요청은 Original Message 아래
    MailSpec(
        10,
        "DEMO-T8",
        "FW: [문의] 표준원가 산정(CK11N) 시 평가 단가 없음 오류",
        "원가팀 윤새벽",
        at(1, 10, 20),
        "팀장님 지시로 전달드립니다. 아래 건 확인 부탁드립니다.\n"
        "11/20까지 12월 표준원가 확정이 잡혀 있어 일정 고려해 주시면 감사하겠습니다.\n\n"
        "-----Original Message-----\n"
        f"From: ○○공장 원가담당 서다온 <{addr('seodaon')}>\n"
        "Sent: 수요일 오후 4:12\n"
        "To: 원가팀 윤새벽\n"
        "Subject: [문의] 표준원가 산정(CK11N) 시 평가 단가 없음 오류\n\n"
        "안녕하세요. ○○공장 원가담당 서다온입니다.\n"
        "12월 적용 표준원가를 CK11N으로 산정하는데 반제품 3건에서 오류가 납니다.\n"
        "오류: '자재 SF-2010 평가 단가를 찾을 수 없습니다' (메시지 번호 CK 465)\n"
        "원가 산정 변형 PPC1, 플랜트 P100 기준입니다.\n"
        "BOM·라우팅은 이상 없어 보이는데, 구매 정보 레코드 단가 문제인지\n"
        "확인 요청드립니다. 대상 자재는 첨부 목록에 정리했습니다."
        + sign("서다온", "○○공장 원가담당", "0001"),
        address=addr("yunsaebyeok"),
        attachments=("오류화면.png", "대상목록.xlsx"),
    ),
    MailSpec(
        11,
        "DEMO-T8",
        "RE: FW: [문의] 표준원가 산정(CK11N) 시 평가 단가 없음 오류",
        "원가팀 윤새벽",
        at(2, 9, 5),
        "추가로 말씀드립니다.\n"
        "P200 플랜트에서도 같은 오류(메시지 번호 CK 465)가 2건 더 확인되었습니다.\n"
        "같이 봐 주시고, CK40N 일괄 산정에서도 재현되는지 확인 부탁드립니다.",
        address=addr("yunsaebyeok"),
    ),
    # DEMO-T9: MM 입고(MIGO) — 전달(FW) 메일(보낸 사람: 형식), 이후 요청 추가
    MailSpec(
        12,
        "DEMO-T9",
        "FW: 입고 처리(MIGO) 시 허용 수량 초과 오류",
        "구매팀 나은솔",
        at(0, 13, 40),
        "아래 건 확인 부탁드립니다. 금주 금요일까지 회신 주시면 공장에 안내하겠습니다.\n\n"
        "보낸 사람: ○○공장 자재팀 표지운\n"
        "보낸 날짜: 목요일 오전 11:02\n"
        "받는 사람: 구매팀 나은솔\n"
        "제목: 입고 처리(MIGO) 시 허용 수량 초과 오류\n\n"
        "수고 많으십니다. ○○공장 자재팀 표지운입니다.\n"
        "구매오더 기준 입고(이동유형 101)를 MIGO로 처리하면 저장이 안 됩니다.\n"
        "  - 메시지 번호 M7 022: 구매오더 품목의 허용 입고 수량을 초과했습니다\n"
        "  - 대상: 구매오더 4500001234 품목 10, 4500001240 품목 20\n"
        "ME23N으로 보면 납품 완료 표시가 되어 있는데 실제 잔량은 남아 있습니다.\n"
        "ME22N에서 납품 완료 지시자를 해제해도 되는지 확인 바랍니다."
        + sign("표지운", "○○공장 자재팀", "0002")
        + DISCLAIMER,
        address=addr("naeunsol"),
        attachments=("오류화면.png",),
    ),
    MailSpec(
        13,
        "DEMO-T9",
        "RE: FW: 입고 처리(MIGO) 시 허용 수량 초과 오류",
        "구매팀 나은솔",
        at(2, 15, 30),
        "추가 요청드립니다.\n"
        "공장에서 반품 입고(이동유형 122)도 같은 메시지(M7 022)로 막힌다고 합니다.\n"
        "함께 확인 부탁드리고, 원인이 정리되면 공장 담당자에게 공유할 수 있게\n"
        "짧게 정리해 주세요.",
        address=addr("naeunsol"),
    ),
    # DEMO-T10: SD 대금청구(VF01)·출고(VL02N) — 여러 요청(번호 목록), 긴 인사말, 내가 회신해 완료
    MailSpec(
        14,
        "DEMO-T10",
        "대금청구(VF01) 생성 오류 및 출고 정정 문의",
        "영업관리팀 도하린",
        at(0, 9, 15),
        "안녕하세요, 영업관리팀 도하린입니다.\n"
        "날씨가 부쩍 쌀쌀해졌는데 감기 조심하시고요, 월말 앞두고 늘 챙겨 주셔서\n"
        "감사하다는 말씀 먼저 드립니다. 바쁘신 와중에 죄송하지만 오늘 중 처리가\n"
        "필요한 건이 있어 몇 가지 한꺼번에 여쭙니다.\n\n"
        "1. VF01로 대금청구 생성 시 '가격 결정 오류: 필수 조건 PR00 없음'\n"
        "   (메시지 번호 VF 059)이 납니다. 원인 확인 부탁드립니다.\n"
        "2. 납품 80001234는 수량이 잘못 출고 전기되었습니다. VL02N에서 바로 고칠 수\n"
        "   있는지, 출고 취소(VL09)부터 해야 하는지 알려 주세요.\n"
        "3. 위 두 건 정리 후 첨부 목록의 미청구 납품 12건을 일괄 재처리해 주세요.\n"
        + sign("도하린", "영업관리팀", "0003")
        + DISCLAIMER,
        address=addr("doharin"),
        attachments=("대상목록.xlsx", "오류화면.png"),
    ),
    MailSpec(
        15,
        "DEMO-T10",
        "RE: 대금청구(VF01) 생성 오류 및 출고 정정 문의",
        "본인",
        at(0, 16, 40),
        "도하린 님, 요청하신 3건 결과 공유드립니다.\n"
        "1. 확인 결과 VK13에서 가격 조건 PR00의 유효기간이 10월 말로 끝나 있어"
        " 가격 결정이 실패한 것이 원인이었습니다.\n"
        "영업 단가 담당자 확인 후 VK12로 유효기간을 변경했습니다.\n"
        "2. 납품 80001234는 VL09로 출고 취소 후 VL02N에서 수량을 수정하고"
        " 다시 출고 전기했습니다.\n"
        "3. VF01로 미청구 12건 재생성 완료했고, VF03에서 회계 전표 생성까지 확인했습니다.\n"
        "단가 만료 1주 전 점검 항목에 추가해 같은 문제가 반복되지 않게 하겠습니다.",
        sent_by_me=True,
    ),
    # DEMO-T11: PP MRP(MD04) — 여러 요청, 이후 후속 메일로 요청 추가·기한 변경
    MailSpec(
        16,
        "DEMO-T11",
        "MRP 실행 후 계획오더 미생성 건 확인 요청",
        "생산관리팀 온새미",
        at(1, 14, 0),
        "안녕하세요, 생산관리팀 온새미입니다.\n"
        "어제 MRP를 돌렸는데 일부 자재에서 계획오더가 생성되지 않았습니다.\n"
        "아래 사항 확인 부탁드립니다.\n"
        "1. MD04 재고/소요량 리스트에 소요량은 있는데 계획오더가 없는 원인\n"
        "2. MD01N 실행 시 플랜트 P100 계획 범위·처리 키 설정이 맞는지 점검\n"
        "3. 원인 조치 후 재실행 가능한 시점 안내\n"
        "대상 자재: FG-1001, FG-1002, SF-2010\n"
        "급하지는 않고, 다음 주 초 생산계획 회의 때까지만 알면 됩니다."
        + sign("온새미", "생산관리팀", "0004"),
        address=addr("onsaemi"),
    ),
    MailSpec(
        17,
        "DEMO-T11",
        "RE: MRP 실행 후 계획오더 미생성 건 확인 요청",
        "생산관리팀 온새미",
        at(3, 10, 30),
        "추가·변경 사항 말씀드립니다.\n"
        "- 대상 자재에 SF-2011, SF-2012 두 건이 추가됩니다.\n"
        "- 생산계획 회의가 앞당겨져서 11/18까지 회신 부탁드립니다.\n"
        "- 3번(재실행 시점)은 저희가 MD03 단일 자재 MRP로 돌려 볼 테니 빼셔도 됩니다.",
        address=addr("onsaemi"),
    ),
    # DEMO-T12: BC 권한(SU01/PFCG) — 표 형태 데이터, 긴 서명·면책 문구
    MailSpec(
        18,
        "DEMO-T12",
        "[요청] 신규 인원 SAP 계정 생성 및 역할 부여",
        "회계팀 한가람",
        at(2, 10, 0),
        "안녕하세요. 회계팀 한가람입니다.\n"
        "이번 달 합류 인원의 SAP 계정 생성을 요청드립니다(전자결재 승인 완료).\n"
        "11/19까지 SU01 계정 생성과 PFCG 역할 부여 부탁드립니다.\n\n"
        "No | 사용자ID | 소속 | 요청 역할 | 비고\n"
        "---|---|---|---|---\n"
        "1 | DEMO_FI01 | 회계팀 | Z_FI_DISPLAY, Z_FI_POSTING | 전표 입력 담당\n"
        "2 | DEMO_FI02 | 회계팀 | Z_FI_DISPLAY | 조회 전용\n"
        "3 | DEMO_CO01 | 원가팀 | Z_CO_DISPLAY, Z_CO_KSB1 | 원가 보고서 조회\n\n"
        "초기 접속 안내는 사내 절차대로 각자에게 따로 전달해 주세요."
        + sign("한가람", "회계팀", "0005")
        + DISCLAIMER,
        address=addr("hangaram"),
        attachments=("계정신청서.pdf",),
    ),
    # DEMO-T13: BC 덤프(ST22) — 내가 회신해 완료, 이후 단순 감사 회신
    MailSpec(
        19,
        "DEMO-T13",
        "보고서 실행 시 덤프 발생 문의 (TIME_OUT)",
        "자금팀 여울빛",
        at(0, 10, 30),
        "안녕하세요. 자금팀 여울빛입니다.\n"
        "월별 자금 집계 보고서(ZFIR0420)를 전체 기간으로 실행하면\n"
        "1시간쯤 지나 덤프가 납니다. 화면에는 '런타임 오류 TIME_OUT'이 나옵니다.\n"
        "급한 건은 아니니 원인과 회피 방법 확인 부탁드립니다." + sign("여울빛", "자금팀", "0006"),
        address=addr("yeoulbit"),
        attachments=("오류화면.png",),
    ),
    MailSpec(
        20,
        "DEMO-T13",
        "RE: 보고서 실행 시 덤프 발생 문의 (TIME_OUT)",
        "본인",
        at(1, 11, 20),
        "여울빛 님, ST22에서 덤프 내용 확인했습니다.\n"
        "확인 결과 선택 조건 없이 전체 기간을 조회하면서 대화식 실행 시간 제한을"
        " 넘긴 것이 원인입니다.\n"
        "당장은 SE38에서 백그라운드로 실행하면 시간 제한 없이 결과를 받을 수 있습니다.\n"
        "근본 조치로 회계연도·기간을 필수 입력으로 수정했고, STMS로 운영 반영 완료했습니다.",
        sent_by_me=True,
    ),
    MailSpec(
        21,
        "DEMO-T13",
        "RE: 보고서 실행 시 덤프 발생 문의 (TIME_OUT)",
        "자금팀 여울빛",
        at(2, 9, 0),
        "덕분에 백그라운드로 잘 받았습니다. 빠르게 봐 주셔서 감사합니다!",
        address=addr("yeoulbit"),
    ),
)


# 이번 주 완료 스레드의 히스토리 카드 3건 + 과거 해결 사례 6건(유사 이력 검색용).
# DEMO-T1(역분개)은 통합 시나리오 테스트가 '역분개' 이력을 직접 만들고 지우는 흐름을 검증하므로
# 일부러 넣지 않았다. 같은 이유로 '야간 배치 지연' 관련 문구도 넣지 않았다.
DEMO_HISTORY: tuple[DemoHistoryCase, ...] = (
    DemoHistoryCase(
        "DEMO-T3",
        "출하 I/F 실패 건 확인 부탁",
        "SD 출하 인터페이스 실패 3건",
        "출하 문서에 출하지점이 누락되어 인터페이스 전송이 실패함",
        "원인 데이터(출하지점) 수정 후 재처리",
        "VL02N",
        ("SD", "인터페이스"),
    ),
    DemoHistoryCase(
        "DEMO-T10",
        "대금청구(VF01) 생성 오류 및 출고 정정 문의",
        "VF01 대금청구 생성 시 가격 결정 오류(메시지 번호 VF 059)",
        "가격 조건 PR00의 유효기간 만료",
        "VK12로 유효기간 변경 후 VF01 재생성, 오출고 건은 VL09 취소 후 VL02N 수정",
        "VK13",
        ("SD", "대금청구", "가격결정"),
    ),
    DemoHistoryCase(
        "DEMO-T13",
        "보고서 실행 시 덤프 발생 문의 (TIME_OUT)",
        "자금 집계 보고서 전체 기간 실행 시 TIME_OUT 덤프",
        "선택 조건 없이 전체 기간 조회로 대화식 실행 시간 제한 초과",
        "백그라운드 실행 안내, 회계연도·기간 필수 입력으로 수정 후 STMS 반영",
        "ST22",
        ("BC", "덤프", "ABAP"),
    ),
    DemoHistoryCase(
        "DEMO-PAST-1",
        "반제품 표준원가 산정 오류(CK11N)",
        "CK11N 원가 산정 시 평가 단가 없음(메시지 번호 CK 465)",
        "구매 정보 레코드 단가가 산정 기준일 이전에 만료되어 평가 단가를 찾지 못함",
        "ME12로 정보 레코드 단가 유효기간 연장 후 CK11N 재산정",
        "CK11N",
        ("CO", "표준원가"),
    ),
    DemoHistoryCase(
        "DEMO-PAST-2",
        "MIGO 입고 시 허용 수량 초과 오류",
        "구매오더 입고 시 메시지 번호 M7 022로 저장 불가",
        "구매오더 품목에 납품 완료 지시자가 잘못 설정되어 잔량 입고가 막힘",
        "ME22N에서 납품 완료 지시자 해제 후 MIGO 재입고",
        "MIGO",
        ("MM", "입고"),
    ),
    DemoHistoryCase(
        "DEMO-PAST-3",
        "구매오더 IDoc 상태 51 다건 발생",
        "ORDERS IDoc 상태 51, 파트너 프로필 없음(메시지 번호 E0 332)",
        "신규 협력사의 파트너 프로필(WE20) 미등록",
        "WE20에 파트너 프로필 등록 후 BD87로 IDoc 재처리",
        "WE02",
        ("인터페이스", "IDoc", "MM"),
    ),
    DemoHistoryCase(
        "DEMO-PAST-4",
        "트랜스포트 가져오기 리턴 코드 8",
        "STMS 운영 가져오기 시 리턴 코드 8, 프로그램 구문 오류",
        "선행 트랜스포트 요청이 운영에 반영되지 않은 상태에서 후행 요청을 먼저 가져옴",
        "선행 요청부터 순서대로 재가져오기 후 구문 점검",
        "STMS",
        ("BC", "트랜스포트"),
    ),
    DemoHistoryCase(
        "DEMO-PAST-5",
        "원가 보고서 조회 권한 추가",
        "KSB1 원가센터 실제 개별항목 조회 시 권한 없음",
        "사용자 역할에 원가센터 조회 권한 오브젝트(K_CSKS) 누락",
        "PFCG에서 조회 역할 추가 후 SU01로 사용자에 부여",
        "PFCG",
        ("BC", "권한", "CO"),
    ),
    DemoHistoryCase(
        "DEMO-PAST-6",
        "MRP 실행 후 계획오더 미생성",
        "MD04에 소요량은 있으나 계획오더가 생성되지 않음",
        "자재 마스터 MRP 유형이 'ND(계획 안 함)'로 설정됨",
        "MM02로 MRP 유형 변경 후 MD03 단일 자재 MRP 재실행",
        "MD04",
        ("PP", "MRP"),
    ),
)


# 가상 G-Knowledge 지식 문서(이번 주 메일 주제와 일부 겹침).
DEMO_KNOWLEDGE: tuple[DemoKnowledgeDoc, ...] = (
    DemoKnowledgeDoc("CK11N 표준원가 산정 오류 유형별 점검 가이드", "/knowledge/1101"),
    DemoKnowledgeDoc("MIGO 입고 오류 메시지(M7 계열) 대응 방법", "/knowledge/1102"),
    DemoKnowledgeDoc("대금청구(VF01) 가격 결정 오류 점검 순서", "/knowledge/1103"),
    DemoKnowledgeDoc("IDoc 상태 51 재처리 절차(WE02·BD87)", "/knowledge/1104"),
    DemoKnowledgeDoc("SAP 사용자 계정 생성·역할 부여 절차(SU01·PFCG)", "/knowledge/1105"),
    DemoKnowledgeDoc("ST22 덤프 분석 기초: TIME_OUT과 메모리 부족", "/knowledge/1106"),
    DemoKnowledgeDoc("트랜스포트 운영 이관 순서와 리턴 코드 해석(STMS)", "/knowledge/1107"),
    DemoKnowledgeDoc("MRP 결과 확인 방법(MD04·MD03)", "/knowledge/1108"),
)
