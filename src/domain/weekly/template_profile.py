"""조직별 주간보고 양식 프로필: 구조 추출·칸 연결·초안 생성(N4, WIKI-02, 결정기록 0037).

- 위키에서 읽거나 붙여넣은 양식 HTML에서 **구조만** 뽑는다(순수 로직, 저장·외부 호출 없음).
  - heading(level, text): 머리글 글자는 마스킹 후 60자까지
  - table(columns, header_rows, sample_rows): 열 이름만 남기고 데이터 행은 개수만 센다.
    th 줄이 없으면 첫 줄 칸 중 열 이름 키워드와 맞는 것만 남기고 나머지는 '열N'으로 바꾼다
  - list(style): 목록 종류만(항목 글자는 버림)
  - paragraph(text): '※'·'참고' 같은 고정 안내 문구만(그 밖의 문단은 내용이라 버림)
- 칸 연결(바인딩): 머리글마다 넣을 데이터(금주 실적·차주 계획·이슈·고정 문구·비움),
  표 열마다 넣을 업무 필드. 처음에는 키워드 규칙으로 자동 제안하고 사용자가 고친다.
- 초안 만들기는 `profile_render.py`에 있다(블록·열 순서 그대로, 마스킹·이스케이프).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Final

from domain.errors import UserInputError
from domain.weekly.form import FormRules, classify_heading
from domain.weekly.rich import DROP_WITH_CONTENT
from security.masking import mask

MAX_SOURCE_BYTES: Final = 500_000  # 위키에서 읽는 양식 HTML 상한
MAX_PASTE_CHARS: Final = 80_000  # 붙여넣기 상한(한글 3바이트여도 요청 본문 256KB 안)
MAX_TAGS: Final = 20_000  # 태그 수 상한(파서·영역 탐색 비용 제한)
MAX_SAMPLE_ROWS: Final = 10_000
MAX_BLOCKS: Final = 80
MAX_COLUMNS: Final = 12
MAX_HEADING_CHARS: Final = 60
MAX_COLUMN_CHARS: Final = 40
MAX_FIXED_CHARS: Final = 200
MAX_BLOCKS_JSON: Final = 65_536  # DB CHECK와 같은 값
MIN_LEVEL: Final = 3  # 내 영역 안 머리글(h1·h2는 영역 경계라 h3으로 낮춘다)
MAX_LEVEL: Final = 4  # 편집기·정리기가 허용하는 가장 작은 머리글

SOURCES: Final = ("done", "planned", "issues", "fixed", "none")
DATA_SOURCES: Final = ("done", "planned", "issues")
ROW_FIELDS: Final = ("title", "module", "dept", "status", "due", "closed", "progress", "note")
# 고정 안내 문구로 볼 문단 시작(그 밖의 문단은 지난 보고 내용이라 저장하지 않는다)
# ('*'는 목록·강조 글머리로도 쓰여 내용 문단이 섞일 수 있어 제외)
FIXED_MARKERS: Final = (
    "※",
    "주)",
    "주:",
    "참고",
    "(단위",
    "단위:",
    "작성 기준",
    "작성기준",
)
# 열 이름 → 업무 필드(앞에서부터 첫 일치). 맞는 것이 없으면 비고(빈칸)
COLUMN_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("progress", ("진행 내용", "진행내용", "진행 사항", "진행사항", "요약", "summary")),
    ("closed", ("완료일", "종료일", "완료 일자")),
    ("status", ("상태", "진척", "진행률", "진행상황", "status", "progress")),
    ("due", ("일정", "기한", "마감", "납기", "예정일", "due")),
    ("dept", ("요청", "부서", "고객", "requester")),
    ("module", ("구분", "모듈", "분류", "유형", "module")),
    ("title", ("업무", "내용", "제목", "항목", "과제", "task", "title")),
    ("note", ("비고", "메모", "note")),
)


@dataclass(frozen=True, slots=True)
class ProfileBlock:
    kind: str  # heading | table | list | paragraph
    level: int = 0
    text: str = ""
    columns: tuple[str, ...] = ()
    header_rows: int = 0
    sample_rows: int = 0
    style: str = ""  # 목록: ul | ol

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["columns"] = list(self.columns)
        return data


@dataclass(frozen=True, slots=True)
class Bindings:
    headings: Mapping[int, str] = field(default_factory=dict)  # 머리글 블록 번호 → 데이터 원천
    columns: Mapping[int, tuple[str, ...]] = field(default_factory=dict)  # 표 블록 번호 → 열 필드

    def as_json(self) -> dict[str, Any]:
        return {
            "headings": {str(k): v for k, v in sorted(self.headings.items())},
            "columns": {str(k): list(v) for k, v in sorted(self.columns.items())},
        }


# ---- 글자 정리 ----
def _clean(text: str, limit: int) -> str:
    joined = " ".join("".join(ch for ch in text if ch.isprintable() or ch.isspace()).split())
    return mask(joined).value[:limit] if joined else ""


def is_fixed_text(text: str) -> bool:
    return text.startswith(FIXED_MARKERS)


def is_column_name(text: str) -> bool:
    """열 이름 키워드(COLUMN_RULES)와 맞는 짧은 글자인지(데이터 칸을 열 이름으로 저장하지 않음).

    키워드를 포함하고 키워드보다 2자까지만 긴 경우만 인정한다(예: '업무명'은 열 이름,
    '홍길동 담당 업무'는 데이터).
    """
    compact = "".join(text.lower().split())
    for _name, words in COLUMN_RULES:
        for word in words:
            key = "".join(word.lower().split())
            if key in compact and len(compact) <= len(key) + 2:
                return True
    return False


# ---- 구조 추출 ----
_HEADING_TAGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class _Extractor(HTMLParser):
    """양식 HTML을 한 번 훑으며 구조 블록만 모은다(스크립트 실행·외부 요청 없음)."""

    def __init__(self, heading_ok: Callable[[int, str], bool] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._heading_ok = heading_ok
        self._renamed = 0
        self.blocks: list[ProfileBlock] = []
        self._skip = 0
        self._text: list[str] | None = None  # 머리글·문단·칸 글자 모으는 중
        self._kind = ""
        self._level = 0
        self._tables = 0
        self._lists = 0
        self._rows: list[tuple[list[str], bool]] = []  # (칸 글자, 모두 th인지)
        self._cells: list[str] | None = None
        self._all_th = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "table":
            self._tables += 1
            if self._tables == 1:
                self._rows = []
        elif self._tables > 1:
            return  # 표 안의 표는 무시
        elif self._tables == 1:
            self._table_start(tag)
        elif tag in ("ul", "ol"):
            if self._lists == 0:
                self._add(ProfileBlock("list", style=tag))
            self._lists += 1
        elif self._lists:
            return
        elif tag in _HEADING_TAGS:
            self._begin("heading", int(tag[1]))
        elif tag == "p":
            self._begin("paragraph", 0)

    def _table_start(self, tag: str) -> None:
        if tag == "tr":
            self._cells, self._all_th = [], True
        elif tag in ("th", "td") and self._cells is not None:
            self._all_th = self._all_th and tag == "th"
            self._text = []

    def _begin(self, kind: str, level: int) -> None:
        self._kind, self._level, self._text = kind, level, []

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "table":
            if self._tables == 1:
                self._table_end()
            self._tables = max(0, self._tables - 1)
        elif self._tables > 1:
            return
        elif self._tables == 1:
            if tag in ("th", "td") and self._cells is not None and self._text is not None:
                self._cells.append(_clean("".join(self._text), MAX_COLUMN_CHARS))
                self._text = None
            elif tag == "tr" and self._cells is not None:
                if self._cells:
                    self._rows.append((self._cells, self._all_th))
                self._cells = None
        elif tag in ("ul", "ol"):
            self._lists = max(0, self._lists - 1)
        elif self._text is not None and (tag == "p" or tag in _HEADING_TAGS):
            self._finish_text()

    def _finish_text(self) -> None:
        raw = "".join(self._text or [])
        self._text = None
        if self._kind == "heading":
            text = _clean(raw, MAX_HEADING_CHARS)
            if not text:
                return
            if self._heading_ok is not None and not self._heading_ok(self._level, text):
                self._renamed += 1  # 이름일 수 있는 머리글: 글자 대신 자리 이름만 남긴다
                text = f"머리글 {self._renamed}"
            level = min(max(self._level, MIN_LEVEL), MAX_LEVEL)
            self._add(ProfileBlock("heading", level=level, text=text))
        else:
            text = _clean(raw, MAX_FIXED_CHARS)
            if text and is_fixed_text(text):
                self._add(ProfileBlock("paragraph", text=text))

    def _table_end(self) -> None:
        if not self._rows:
            return
        header = 0
        for _cells, all_th in self._rows:
            if not all_th:
                break
            header += 1
        names = self._rows[header - 1][0] if header else self._rows[0][0]
        if header == 0:  # th가 없으면 첫 줄은 데이터일 수 있다: 열 이름 키워드만 남긴다
            names = [c if is_column_name(c) else "" for c in names]
        header = max(header, 1)
        columns = tuple(c or f"열{n + 1}" for n, c in enumerate(names))
        self._add(
            ProfileBlock(
                "table",
                columns=columns[:MAX_COLUMNS],
                header_rows=1,  # 열 이름 줄 하나만 다시 만든다(여러 줄 머리는 마지막 줄 기준)
                sample_rows=min(len(self._rows) - header, MAX_SAMPLE_ROWS),
            )
        )
        self._rows = []

    def handle_data(self, data: str) -> None:
        if self._skip or self._text is None:
            return
        if self._tables > 1:
            return
        self._text.append(data)

    def _add(self, block: ProfileBlock) -> None:
        if len(self.blocks) >= MAX_BLOCKS:
            raise UserInputError(f"양식 구조가 너무 큽니다(블록 {MAX_BLOCKS}개 이하)")
        self.blocks.append(block)


def check_source_size(source_html: str) -> None:
    """양식 HTML 크기·태그 수 상한(파서·영역 탐색 전에 먼저 확인)."""
    if len(source_html.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise UserInputError("양식이 너무 큽니다(500KB 이하)")
    if source_html.count("<") > MAX_TAGS:
        raise UserInputError(f"양식 태그가 너무 많습니다({MAX_TAGS}개 이하)")


def extract_blocks(
    source_html: str, heading_ok: Callable[[int, str], bool] | None = None
) -> list[ProfileBlock]:
    """양식 HTML → 구조 블록. 크기 초과·구조 없음은 UserInputError.

    heading_ok(원래 단계, 글자)가 거짓이면 그 머리글 글자는 '머리글 N'으로 바꿔 저장한다
    (내 영역 머리글이 없는 붙여넣기에서 이름일 수 있는 머리글을 남기지 않기 위함).
    """
    check_source_size(source_html)
    parser = _Extractor(heading_ok)
    parser.feed(source_html)
    parser.close()
    if not parser.blocks:
        raise UserInputError("양식에서 머리글·표·목록을 찾지 못했습니다")
    return parser.blocks


def blocks_json(blocks: list[ProfileBlock]) -> str:
    text = json.dumps([b.as_json() for b in blocks], ensure_ascii=False, sort_keys=True)
    if len(text) > MAX_BLOCKS_JSON:
        raise UserInputError("양식 구조가 너무 큽니다(64KB 이하)")
    return text


BAD_STORED: Final = "저장된 양식 구조가 올바르지 않습니다. 양식을 다시 등록해 주세요"
_TEXT_LIMITS: Final = {"heading": MAX_HEADING_CHARS, "paragraph": MAX_FIXED_CHARS}


def _stored_block(b: Any) -> ProfileBlock:
    """저장값 한 블록을 추출 결과와 같은 범위로 검증한다(DB 값도 믿지 않음)."""
    if not isinstance(b, dict):
        raise UserInputError(BAD_STORED)
    block = ProfileBlock(
        str(b["kind"]),
        int(b["level"]),
        str(b["text"]),
        tuple(str(c) for c in b["columns"]),
        int(b["header_rows"]),
        int(b["sample_rows"]),
        str(b["style"]),
    )
    ok = {
        "heading": MIN_LEVEL <= block.level <= MAX_LEVEL and bool(block.text),
        "paragraph": block.level == 0 and is_fixed_text(block.text),
        "table": block.level == 0
        and 0 < len(block.columns) <= MAX_COLUMNS
        and all(0 < len(c) <= MAX_COLUMN_CHARS for c in block.columns)
        and block.header_rows == 1
        and 0 <= block.sample_rows <= MAX_SAMPLE_ROWS,
        "list": block.level == 0 and block.style in ("ul", "ol"),
    }.get(block.kind, False)
    if not ok or len(block.text) > _TEXT_LIMITS.get(block.kind, 0):
        raise UserInputError(BAD_STORED)
    if block.kind != "table" and (block.columns or block.header_rows or block.sample_rows):
        raise UserInputError(BAD_STORED)
    if block.kind != "list" and block.style:
        raise UserInputError(BAD_STORED)
    return block


def blocks_from_json(text: str) -> list[ProfileBlock]:
    try:
        raw = json.loads(text)
        if not isinstance(raw, list) or not 0 < len(raw) <= MAX_BLOCKS:
            raise UserInputError(BAD_STORED)
        return [_stored_block(b) for b in raw]
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, UserInputError):
            raise
        raise UserInputError(BAD_STORED) from None


def structure_hash(blocks: list[ProfileBlock]) -> str:
    """양식이 바뀌었는지 비교하는 지문(구조만, 데이터 행 수는 빼고 비교)."""
    shape = [(b.kind, b.level, b.text, list(b.columns), b.style) for b in blocks]
    payload = json.dumps(shape, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---- 칸 연결 ----
def suggest_field(column: str) -> str:
    lowered = column.lower()
    if column.startswith("열") and column[1:].isdigit():
        return "note"  # 이름을 알 수 없는 열
    for name, words in COLUMN_RULES:
        if any(word.lower() in lowered for word in words):
            return name
    return "note"


def suggest_bindings(blocks: list[ProfileBlock], rules: FormRules) -> Bindings:
    headings: dict[int, str] = {}
    columns: dict[int, tuple[str, ...]] = {}
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            headings[index] = classify_heading(block.text, rules) or "none"
        elif block.kind == "table":
            columns[index] = tuple(suggest_field(c) for c in block.columns)
    return Bindings(headings, columns)


def _index(key: object, blocks: list[ProfileBlock], kind: str) -> int:
    text = str(key)
    if not text.isdigit() or int(text) >= len(blocks) or blocks[int(text)].kind != kind:
        raise UserInputError("칸 연결 정보가 양식과 맞지 않습니다")
    return int(text)


def validate_bindings(blocks: list[ProfileBlock], raw: Mapping[str, Any]) -> Bindings:
    """화면이 보낸 칸 연결을 양식 구조에 맞춰 검증한다. 빠진 칸은 비움으로 둔다."""
    if not isinstance(raw, Mapping):
        raise UserInputError("칸 연결 정보 형식이 올바르지 않습니다")
    heads = raw.get("headings") or {}
    cols = raw.get("columns") or {}
    if not isinstance(heads, Mapping) or not isinstance(cols, Mapping):
        raise UserInputError("칸 연결 정보 형식이 올바르지 않습니다")
    headings: dict[int, str] = {}
    for key, value in heads.items():
        if value not in SOURCES:
            raise UserInputError("알 수 없는 데이터 원천입니다")
        headings[_index(key, blocks, "heading")] = str(value)
    columns: dict[int, tuple[str, ...]] = {}
    for key, value in cols.items():
        index = _index(key, blocks, "table")
        if not isinstance(value, list) or len(value) != len(blocks[index].columns):
            raise UserInputError("표 열 연결 수가 양식과 다릅니다")
        if any(v not in ROW_FIELDS for v in value):
            raise UserInputError("알 수 없는 열 필드입니다")
        columns[index] = tuple(str(v) for v in value)
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            headings.setdefault(index, "none")
        elif block.kind == "table":
            columns.setdefault(index, ("note",) * len(block.columns))
    return Bindings(headings, columns)


def bindings_from_json(blocks: list[ProfileBlock], text: str) -> Bindings:
    try:
        raw = json.loads(text)
    except ValueError:
        raise UserInputError("칸 연결 정보 형식이 올바르지 않습니다") from None
    return validate_bindings(blocks, raw)


def rebind(
    old_blocks: list[ProfileBlock], old: Bindings, new_blocks: list[ProfileBlock]
) -> Bindings:
    """양식이 바뀌었을 때: 같은 글자의 머리글·같은 이름의 열은 연결을 이어받고, 새 칸은 비움."""
    head_by_text = {old_blocks[i].text: s for i, s in old.headings.items()}
    old_tables = [i for i, b in enumerate(old_blocks) if b.kind == "table"]
    headings: dict[int, str] = {}
    columns: dict[int, tuple[str, ...]] = {}
    table_no = 0
    for index, block in enumerate(new_blocks):
        if block.kind == "heading":
            headings[index] = head_by_text.get(block.text, "none")
        elif block.kind == "table":
            known: dict[str, str] = {}
            if table_no < len(old_tables):
                before = old_tables[table_no]
                fields = old.columns.get(before, ())
                known = dict(zip(old_blocks[before].columns, fields, strict=False))
            columns[index] = tuple(known.get(c, "note") for c in block.columns)
            table_no += 1
    return Bindings(headings, columns)
