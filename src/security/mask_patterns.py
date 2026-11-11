"""마스킹 대상 정규식과 치환 라벨.

입력은 `masking.mask()`에서 NFKC 정규화(전각 영문·숫자·기호 → 반각)를 거친 뒤 적용된다.
모든 패턴은 적대적 입력에서도 선형 시간이 되도록 시작 경계(lookbehind)를 반복 가능한
문자 집합과 일치시킨다(ReDoS 방지, tests/unit/test_masking_redos.py).

아래 S105 예외는 비밀값이 아니라 탐지용 키워드·치환 라벨이므로 허용한다.
"""

from __future__ import annotations

import re
from typing import Final

LABEL_RRN: Final = "[주민등록번호]"
LABEL_CARD: Final = "[카드번호]"
LABEL_PHONE: Final = "[전화번호]"
LABEL_ACCOUNT: Final = "[계좌번호]"
LABEL_EMAIL: Final = "[이메일]"
LABEL_SECRET: Final = "[비밀값]"  # noqa: S105
LABEL_TOKEN: Final = "[토큰]"  # noqa: S105

# 계좌번호로 볼 하이픈 묶음의 총 자릿수 범위(날짜 등 짧은 묶음 제외).
ACCOUNT_MIN_DIGITS: Final = 10
ACCOUNT_MAX_DIGITS: Final = 16

# --- 비밀번호·인증번호 문맥 --------------------------------------------------
# 긴 키워드는 접두어가 붙어도(db_password, userPassword) 잡는다.
_SECRET_LONG = r"password|passwd|passphrase|패스워드|비밀번호|비번|암호|인증번호|인증\s*코드"  # noqa: S105
# 짧은 키워드는 다른 단어 일부(opinion, spin)와 구분하려고 영숫자 경계를 요구한다.
_SECRET_SHORT = r"(?<![A-Za-z0-9])(?:pwd|pw|p/w|pass|pin|otp)"  # noqa: S105
_SECRET_KEY = rf"(?:{_SECRET_LONG}|{_SECRET_SHORT})"

# 값: 따옴표로 감싼 값은 닫는 따옴표까지, 아니면 공백·구분 문자 전까지.
_VALUE = r"""(?:"[^"\r\n]{1,200}"|'[^'\r\n]{1,200}'|[^\s,;"'()\[\]{}]{1,200})"""

# 구분자: (닫는 따옴표) + ':' '=' 또는 공백 뒤 '-' + 선택 괄호.
SECRET_CONTEXT = re.compile(
    rf"(?P<key>{_SECRET_KEY})"
    r"(?P<sep>[\"']?\s*(?:[:=]|-(?=\s))\s*[(\[]?\s*)"
    rf"(?P<val>{_VALUE})",
    re.IGNORECASE,
)
# 구분자 없이 공백·조사(은/는/이/가/를)·괄호만 있는 경우(예: '비밀번호는 a1b2',
# '인증번호 [482913]')는 숫자가 섞인 값만 마스킹해 '비밀번호를 변경해 주세요' 같은
# 일반 문장을 보존한다. 숫자 없는 비밀번호는 이 형태에서 놓칠 수 있다(알려진 한계).
SECRET_SPACED = re.compile(
    rf"(?P<key>{_SECRET_KEY})(?P<sep>\s*(?:은|는|이|가|를)?\s*[(\[\"']?\s*)"
    r"(?P<val>(?=[^\s)\]\"']*\d)[^\s,;)\]\"']{1,200})",
    re.IGNORECASE,
)

# --- 토큰·키 문맥 ------------------------------------------------------------
_TOKEN_KEY = (
    r"api[_\-\s]?key|access[_\-\s]?key|private[_\-\s]?key|secret(?:[_\-\s]?key)?"  # noqa: S105
    r"|client[_\-\s]?secret|credential|access[_\-\s]?token|refresh[_\-\s]?token"
    r"|auth[_\-\s]?token|session[_\-\s]?token|token|토큰|jsessionid|sid"
)
TOKEN_CONTEXT = re.compile(
    rf"(?P<key>(?<![A-Za-z0-9])(?:{_TOKEN_KEY})|(?-i:(?<=[a-z_])(?:Token|Secret|Key)))"
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    rf"(?P<val>{_VALUE})",
    re.IGNORECASE,
)
# 인증 헤더·쿠키 헤더는 줄 끝까지 값 전체를 가린다.
AUTH_HEADER = re.compile(
    r"(?P<key>(?<![A-Za-z0-9\-])(?:authorization|proxy-authorization|set-cookie|cookie))"
    r"(?P<sep>\s*:\s*)(?P<val>[^\r\n]{1,4000})",
    re.IGNORECASE,
)
BEARER = re.compile(
    r"(?P<key>(?<![A-Za-z0-9])(?:Bearer|Basic))(?P<sep>\s+)(?P<val>[A-Za-z0-9._~+/=\-]{8,})",
    re.IGNORECASE,
)

# 형식만으로 식별 가능한 토큰(접두사 기반). 시작 경계는 반복 문자 집합과 같게 둔다.
KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_\-])(?:"
    r"sk-[A-Za-z0-9_\-]{20,}"  # OpenAI/Anthropic 계열 키
    r"|AKIA[0-9A-Z]{16}"  # AWS 액세스 키 ID
    r"|AIza[0-9A-Za-z_\-]{35}"  # Google API 키
    r"|gh[pousr]_[A-Za-z0-9]{36,}"  # GitHub 토큰
    r"|github_pat_[A-Za-z0-9_]{22,}"  # GitHub fine-grained 토큰
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"  # Slack 토큰
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"  # JWT
    r")"
)

# --- 개인 식별 숫자 ------------------------------------------------------------
_SEP = r"[ \-–—._]"

# 주민등록번호·외국인등록번호: YYMMDD-[1-8]NNNNNN (생년월일 범위 검사 포함)
RRN = re.compile(
    rf"(?<!\d)\d{{2}}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s?{_SEP}?\s?[1-8]\d{{6}}(?!\d)"
)

# 카드번호: 4자리 4묶음(구분자 혼용·두 칸 허용, 끝 4자리가 *로 가려진 형태 포함)
CARD_GROUPED = re.compile(
    r"(?<![\d*])\d{4}(?:[ \-.]{1,2}\d{4}){2}[ \-.]{1,2}(?:\d{4}(?:[ \-.]{1,2}\d{1,3})?|[*xX#]{4})"
    r"(?![\d*])"
)
# 연속 13~19자리(Luhn 검증은 masking에서 수행)
CARD_PLAIN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

# 전화번호: 휴대전화, 지역번호(괄호 포함), 대표번호, 국가번호(+82)
_PSEP = r"[ \-–—.]"
PHONE = re.compile(
    r"(?<![\d\-])(?:"
    rf"(?:\+82{_PSEP}?(?:0)?|\(0(?:2|[3-6][1-5])\)\s?|0(?:2|[3-6][1-5]|70)\)\s?)"
    rf"(?:1[016789]|2|[3-6][1-5]|70)?{_PSEP}?\d{{3,4}}{_PSEP}?\d{{4}}"
    rf"|01[016789]{_PSEP}?\d{{3,4}}{_PSEP}?\d{{4}}"
    rf"|0(?:2|[3-6][1-5]|70){_PSEP}?\d{{3,4}}{_PSEP}?\d{{4}}"
    rf"|1[5689]\d{{2}}{_PSEP}\d{{4}}"
    r")(?![\d\-])"
)

# 계좌번호: 하이픈으로 나뉜 3~4개 숫자 묶음 또는 '계좌' 문맥 뒤 숫자
ACCOUNT_GROUPED = re.compile(r"(?<![\d\-])\d{2,6}(?:-\d{2,7}){2,3}(?![\d\-])")
ACCOUNT_CONTEXT = re.compile(
    r"(?P<key>계좌(?:\s*번호)?|account(?:\s*(?:no|number))?)"
    r"(?P<sep>\s*(?:[:]|은|는)?\s*)(?P<val>\d[\d\-\s]{6,22}\d)",
    re.IGNORECASE,
)

# 이메일: 시작 경계를 로컬파트 문자 집합과 같게 둬 '%%%…' 같은 입력에서 O(n²)를 막는다.
EMAIL = re.compile(r"(?<![\w.%+\-])[\w.%+\-]{1,64}@[\w\-]{1,63}(?:\.[\w\-]{1,63}){1,8}(?![\w@])")
