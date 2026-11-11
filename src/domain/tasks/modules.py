"""SAP 모듈 태그 추정(TODO-01). 키워드 기반이며 사용자가 승인 시 고칠 수 있다."""

from __future__ import annotations

from typing import Final

MODULE_TAGS: Final = ("FI", "CO", "MM", "SD", "PP", "HR", "Basis", "ABAP")

MODULE_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "FI": ("전표", "역분개", "회계", "결산", "OB52", "FB01", "채권", "채무"),
    "CO": ("원가", "KSB1", "코스트", "손익"),
    "MM": ("구매", "PO", "구매오더", "자재", "입고", "ME21N"),
    "SD": ("출하", "판매", "납품", "I/F", "인터페이스", "VL01N"),
    "PP": ("생산", "BOM", "작업지시"),
    "HR": ("급여", "근태", "인사"),
    "Basis": ("배치", "잡", "권한", "시스템 점검", "트랜스포트"),
    "ABAP": ("ABAP", "출력물", "양식", "스마트폼", "프로그램 수정"),
}


def guess_module(text: str) -> str | None:
    folded = text.casefold()
    for module, hints in MODULE_HINTS.items():
        if any(hint.casefold() in folded for hint in hints):
            return module
    return None
