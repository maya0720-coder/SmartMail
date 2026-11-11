"""메일 제외 규칙(MAIL-05): 공지·뉴스레터·자동발송·지정 발신자·지정 폴더."""

from __future__ import annotations

from dataclasses import dataclass

from domain.mail import MailMessage, MailThread


@dataclass(frozen=True, slots=True)
class ExclusionRules:
    subject_keywords: tuple[str, ...] = ()
    sender_patterns: tuple[str, ...] = ()  # 주소 일부(예: noreply)
    excluded_senders: tuple[str, ...] = ()  # 정확한 주소
    excluded_folders: tuple[str, ...] = ()
    exclude_auto: bool = True

    def excludes(self, message: MailMessage) -> bool:
        if message.sent_by_me:
            return False  # 내가 보낸 회신은 항상 포함(TODO-05 회신 감지)
        subject = message.subject.casefold()
        address = message.sender_address.casefold()
        folder = message.folder.casefold()
        return (
            (self.exclude_auto and message.is_auto)
            or any(k.casefold() in subject for k in self.subject_keywords)
            or any(p.casefold() in address for p in self.sender_patterns if address)
            or address in {s.casefold() for s in self.excluded_senders}
            or folder in {f.casefold() for f in self.excluded_folders}
        )

    def filter_threads(self, threads: list[MailThread]) -> list[MailThread]:
        """제외 대상 메일을 빼고, 남은 메일이 없거나 받은 메일이 모두 제외된 스레드는 버린다."""
        result = []
        for thread in threads:
            kept = tuple(m for m in thread.messages if not self.excludes(m))
            if any(not m.sent_by_me for m in kept):
                result.append(MailThread(thread.thread_id, kept))
        return result

    def summary(self) -> str:
        parts = []
        if self.exclude_auto:
            parts.append("자동발송")
        if self.subject_keywords:
            parts.append("제목 키워드 " + str(len(self.subject_keywords)) + "개")
        if self.sender_patterns or self.excluded_senders:
            parts.append(
                "발신자 " + str(len(self.sender_patterns) + len(self.excluded_senders)) + "개"
            )
        if self.excluded_folders:
            parts.append("폴더 " + str(len(self.excluded_folders)) + "개")
        return ", ".join(parts) or "제외 규칙 없음"
