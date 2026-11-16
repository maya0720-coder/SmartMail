"""테스트·데모용 가짜 지식 AI. 외부로 아무것도 보내지 않고 고정 규칙으로 답한다.

데모(공모전 시연)에서 실제 AI 없이도 구조화 요약·해결 방안 형식을 보여 주도록,
입력(마스킹된 요약·사례)에서 규칙으로 문장을 골라 같은 형식으로 답한다.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from domain.ai import AiAnswer, AiUnavailableError, Classification, RequestDigest
from domain.tasks.modules import guess_module
from security.masking import MaskedText

_REQUEST: Final = re.compile(r"요청|부탁|확인|처리|검토|바랍니다|주세요|필요")
_DUE: Final = re.compile(
    r"(\d{1,2}/\d{1,2}\s*(?:까지|중)?|금일\s*중|오늘\s*중|내일|이번\s*주\s*\S+요일|금주\s*\S+요일|"
    r"다음\s*주\s*\S*|차주\s*\S*)"
)
_TCODE: Final = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,4}\d{1,3}[A-Z]?(?![A-Za-z0-9])")
_BULLET: Final = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•·])\s*(.+)$")
_CASE_ACTION: Final = re.compile(r"조치:\s*([^/]+)")
_SUBJECT: Final = re.compile(r"^제목:\s*(.+)$", re.MULTILINE)
# 전달·회신 머리말("아래 건 확인 부탁드립니다")처럼 내용 없는 문장
_COVER: Final = re.compile(
    r"^\s*(?:(?:추가|변경)[·\s]*(?:추가|변경)?\s*사항|"
    r"(?:아래|하기|위|첨부)\s*(?:건|내용|사항|메일|파일)?\s*(?:을|를)?\s*\S{0,6}\s*"
    r"(?:확인|검토|공유|참고))"
)
_ENDING: Final = re.compile(
    r"\s*(?:을|를)?\s*(?:요청|부탁|확인 부탁|검토 부탁)?\s*"
    r"(?:드립니다|드려요|바랍니다|주세요|합니다|해 주세요)\.?$"
)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text) if s.strip()]


def _meaningful(sentence: str) -> bool:
    """머리말·짧은 요청 어미('확인 요청드립니다.')가 아닌, 내용이 있는 문장."""
    return not _COVER.match(sentence) and len(_noun(sentence)) >= 10


def _noun(sentence: str) -> str:
    """요청 문장 끝의 존댓말 어미를 떼어 목록 제목처럼 만든다(가짜 AI 전용 간이 규칙)."""
    return _ENDING.sub("", sentence).strip(" .,")


class FakeKnowledgeAI:
    name = "fake"

    def __init__(self, *, fail: bool = False, delay_seconds: float = 0.0) -> None:
        self.fail = fail
        self.delay_seconds = delay_seconds
        self.received: list[MaskedText] = []
        self.peak_parallel = 0  # summarize_many에서 동시에 진행된 묶음 수의 최댓값(테스트 확인용)
        self._running = 0
        self._count_lock = threading.Lock()

    def _maybe_fail(self) -> None:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.fail:
            raise AiUnavailableError("AI 응답 오류(가짜)")

    def classify_task(self, summary: MaskedText) -> Classification:
        self.received.append(summary)
        self._maybe_fail()
        text = summary.value
        is_task = any(word in text for word in ("요청", "부탁", "확인", "처리", "필요"))
        return Classification(is_task, guess_module(text), 0.6 if is_task else 0.3)

    def summarize_requests(self, items: Sequence[MaskedText]) -> list[RequestDigest | None]:
        """요청 표현이 있는 문장·번호 목록을 골라 구조화 요약 형식으로 돌려준다(외부 전송 없음)."""
        self.received.extend(items)
        self._maybe_fail()
        return [self._digest(item.value) for item in items]

    def summarize_many(
        self, batches: Sequence[Sequence[MaskedText]]
    ) -> list[list[RequestDigest | None] | AiUnavailableError]:
        """여러 묶음을 동시에 처리하는 경로를 흉내 낸다(실제 어댑터의 여러 탭과 같은 모양)."""

        def one(batch: Sequence[MaskedText]) -> list[RequestDigest | None] | AiUnavailableError:
            with self._count_lock:
                self._running += 1
                self.peak_parallel = max(self.peak_parallel, self._running)
            try:
                return self.summarize_requests(batch)
            except AiUnavailableError as exc:
                return exc
            finally:
                with self._count_lock:
                    self._running -= 1

        if not batches:
            return []
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            return list(pool.map(one, batches))

    def _digest(self, text: str) -> RequestDigest | None:
        subject_match = _SUBJECT.search(text)
        subject = subject_match.group(1).strip() if subject_match else ""
        body = text.split("본문:", 1)[1] if "본문:" in text else text
        # 전달 메일은 원래 메일·후속 메일에 실제 요청이 있다
        focus = body.split("[원래 메일]", 1)[1] if "[원래 메일]" in body else body
        lines = focus.splitlines()
        bullets = [m.group(1).strip() for line in lines if (m := _BULLET.match(line))]
        focus = focus.split("후속 메일:", 1)[0]
        sentences = [s for s in _sentences(focus) if _meaningful(s)]
        asks = [s for s in sentences if _REQUEST.search(s)]
        followup = body.split("후속 메일:", 1)[1] if "후속 메일:" in body else ""
        extra = [s for s in _sentences(followup) if _meaningful(s) and s not in sentences]
        pick = asks[0] if asks else (sentences[0] if sentences else "")
        title = subject if len(subject) >= 8 and not _COVER.match(subject) else _noun(pick)
        if not title:
            return None
        requests = [b[:60] for b in bullets[:4]] or [s[:60] for s in (asks or sentences)[:2]]
        requests += [f"(후속) {s[:55]}" for s in extra[:1]]
        due = _DUE.search(body)
        tcode = _TCODE.search(body)
        action = f"{tcode.group(0)}에서 " if tcode else ""
        return RequestDigest(
            title=title[:40],
            requests=tuple(requests[:5]) or (pick[:60],),
            due=due.group(0).strip() if due else None,
            action=f"{action}요청 내용 확인·처리 후 요청자에게 결과 회신",
        )

    def company_name(self, domain: MaskedText) -> str | None:
        """가짜 회사명: 도메인 첫 마디로 만든다(외부 전송 없음, 결정기록 0027)."""
        self.received.append(domain)
        self._maybe_fail()
        first = domain.value.split(".")[0]
        return f"{first.upper()}(가짜)" if first.isalpha() else None

    def summarize_progress(self, items: Sequence[MaskedText]) -> str:
        """가짜 진행 요약: 받은·보낸 메일 수로 3줄을 만든다(결정기록 0028)."""
        self.received.extend(items)
        self._maybe_fail()
        received = sum(1 for item in items if " 받음 " in f" {item.value} ")
        sent = sum(1 for item in items if " 보냄 " in f" {item.value} ")
        first = items[0].value[:80] if items else "없음"
        left = "회신 대기" if received > sent else "완료 확인"
        return (
            f"요청 사항: {first}\n처리 경과: 받은 메일 {received}통, 보낸 회신 {sent}통\n"
            f"남은 일: {left}"
        )

    def suggest_solution(
        self, summary: MaskedText, similar_cases: Sequence[MaskedText]
    ) -> AiAnswer:
        self.received.append(summary)
        self.received.extend(similar_cases)
        self._maybe_fail()
        module = guess_module(summary.value) or "일반"
        codes = list(dict.fromkeys(_TCODE.findall(summary.value)))[:3]
        cases = "없음"
        fixes: list[str] = []
        lines = [f"원인 추정: [{module}] 설정·마스터 데이터 또는 최근 변경 사항 영향(추정)."]
        if similar_cases:
            cases = ", ".join(str(n) for n in range(1, len(similar_cases) + 1))
            first = similar_cases[0].value
            lines[0] = (
                f"원인 추정: [{module}] 비슷한 과거 사례와 같은 원인일 가능성이 높습니다(추정)."
            )
            lines.append(f"참고 사례 요지: {first[:120]}")
            done = _CASE_ACTION.search(first)
            if done:
                fixes.append(f"- 과거 사례 조치 적용 검토: {done.group(1).strip()[:100]}")
            codes += [c for c in _TCODE.findall(first) if c not in codes][:2]
        lines += [
            "확인 순서:",
            "1. 요청 메일의 대상(문서 번호·사용자·기간)과 오류 메시지 번호를 확인",
            "2. 테스트 환경에서 같은 조건으로 재현",
            "3. 관련 설정·권한·최근 변경 이력(트랜스포트) 확인",
            "조치 방안:",
            *fixes,
            "- 원인 확인 후 조치하고 요청자에게 결과 회신",
            "- 반복되는 유형이면 히스토리 카드로 남겨 다음 처리 시간을 줄이기",
            f"관련 T-code: {', '.join(codes) or '없음'}",
            f"참고한 사례: {cases}",
        ]
        return AiAnswer(text="\n".join(lines), references=())
