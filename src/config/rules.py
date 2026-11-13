"""규칙 파일(TOML) 로딩. 형식 오류는 시작 시 `ConfigError`로 실패시킨다."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

from config.settings import ConfigError
from domain.errors import UserInputError
from domain.mail_rules import ExclusionRules
from domain.tasks.categories import CategoryRules
from domain.tasks.summarize import TitleRules
from domain.weekly.report import WikiTemplate
from domain.weekly.title_rule import validate_title_rule

RULES_DIR: Final = Path(__file__).resolve().parent / "rules"
DEFAULT_MAIL_RULES: Final = RULES_DIR / "mail_exclusions.toml"
DEFAULT_WIKI_TEMPLATE: Final = RULES_DIR / "wiki_template.toml"
DEFAULT_CATEGORY_RULES: Final = RULES_DIR / "task_categories.toml"
DEFAULT_TITLE_RULES: Final = RULES_DIR / "title_rules.toml"
MAX_RULE_ITEMS: Final = 500


def read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"규칙 파일이 없습니다: {path.name}") from None
    except tomllib.TOMLDecodeError:
        raise ConfigError(f"규칙 파일 형식 오류: {path.name}") from None


def str_list(data: dict[str, object], key: str, source: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{source}: {key}는 문자열 목록이어야 합니다")
    if len(value) > MAX_RULE_ITEMS:
        raise ConfigError(f"{source}: {key} 항목이 너무 많습니다")
    return tuple(v.strip() for v in value if v.strip())


def load_mail_rules(path: Path = DEFAULT_MAIL_RULES) -> ExclusionRules:
    data = read_toml(path)
    known = {
        "exclude_auto",
        "subject_keywords",
        "sender_patterns",
        "excluded_senders",
        "excluded_folders",
    }
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{path.name}: 알 수 없는 키 {sorted(unknown)}")
    exclude_auto = data.get("exclude_auto", True)
    if not isinstance(exclude_auto, bool):
        raise ConfigError(f"{path.name}: exclude_auto는 true/false여야 합니다")
    return ExclusionRules(
        subject_keywords=str_list(data, "subject_keywords", path.name),
        sender_patterns=str_list(data, "sender_patterns", path.name),
        excluded_senders=str_list(data, "excluded_senders", path.name),
        excluded_folders=str_list(data, "excluded_folders", path.name),
        exclude_auto=exclude_auto,
    )


def load_category_rules(
    path: Path = DEFAULT_CATEGORY_RULES,
) -> tuple[CategoryRules, tuple[str, ...]]:
    """전자결재 판별 규칙과 제목 말머리 목록. 형식 오류는 시작 시 실패."""
    data = read_toml(path)
    known = {
        "approval_category",
        "approval_subject_keywords",
        "approval_sender_patterns",
        "title_noise_prefixes",
    }
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{path.name}: 알 수 없는 키 {sorted(unknown)}")
    category = data.get("approval_category", "전자결재")
    if not isinstance(category, str) or not 1 <= len(category.strip()) <= 20:
        raise ConfigError(f"{path.name}: approval_category는 1~20자 문자열이어야 합니다")
    rules = CategoryRules(
        approval_category=category.strip(),
        subject_keywords=str_list(data, "approval_subject_keywords", path.name),
        sender_patterns=str_list(data, "approval_sender_patterns", path.name),
    )
    return rules, str_list(data, "title_noise_prefixes", path.name)


def load_title_rules(path: Path = DEFAULT_TITLE_RULES) -> TitleRules:
    """업무 제목 일반 문구 규칙(결정기록 0015). 형식 오류는 시작 시 실패."""
    data = read_toml(path)
    unknown = set(data) - {"min_chars", "generic_phrases", "polite_endings"}
    if unknown:
        raise ConfigError(f"{path.name}: 알 수 없는 키 {sorted(unknown)}")
    min_chars = data.get("min_chars", 8)
    if not isinstance(min_chars, int) or isinstance(min_chars, bool) or not 1 <= min_chars <= 50:
        raise ConfigError(f"{path.name}: min_chars는 1~50 정수여야 합니다")
    return TitleRules(
        min_chars=min_chars,
        generic_phrases=str_list(data, "generic_phrases", path.name),
        polite_endings=str_list(data, "polite_endings", path.name),
    )


def load_wiki_template(path: Path = DEFAULT_WIKI_TEMPLATE) -> WikiTemplate:
    data = read_toml(path)
    defaults = WikiTemplate()
    values: dict[str, object] = {}
    for name in WikiTemplate.__dataclass_fields__:
        if name not in data:
            continue
        expected = type(getattr(defaults, name))
        if not isinstance(data[name], expected) or isinstance(data[name], bool):
            raise ConfigError(f"{path.name}: {name} 형식 오류")
        values[name] = data[name]
    unknown = set(data) - set(WikiTemplate.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"{path.name}: 알 수 없는 키 {sorted(unknown)}")
    template = WikiTemplate(**values)  # type: ignore[arg-type]
    try:
        validate_title_rule(template.page_title_format, template.title_date_offset_days)
    except UserInputError as exc:
        raise ConfigError(f"{path.name}: 제목 규칙 오류 - {exc}") from None
    return template
