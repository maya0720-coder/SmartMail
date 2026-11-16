"""외부 회사 도메인 → 회사명 캐시(결정기록 0027).

회사 도메인과 회사명만 둔다(개인정보 아님). 업무 보관기간과 별개로 유지하며,
'로컬 데이터 전체 삭제'(Database.wipe_all)에 포함된다.
"""

from __future__ import annotations

from domain.ports import Clock
from domain.tasks.requester import (
    COMPANY_SOURCES,
    MAX_COMPANY_CHARS,
    CompanyName,
    normalize_domain,
    valid_company_name,
)
from storage.db import Database, to_db_time


class SqliteCompanyDirectory:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def get(self, domain: str) -> CompanyName | None:
        rows = self._db.query(
            "SELECT company, source FROM domain_company WHERE domain = ?", (domain,)
        )
        if not rows:
            return None
        return CompanyName(str(rows[0]["company"]), str(rows[0]["source"]))

    def save(self, domain: str, company: str, source: str) -> None:
        normalized = normalize_domain(domain)
        name = valid_company_name(company)
        if normalized is None or name is None or source not in COMPANY_SOURCES:
            raise ValueError("도메인·회사명·출처 형식이 올바르지 않습니다")
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO domain_company (domain, company, source, resolved_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(domain) DO UPDATE SET"
                " company = excluded.company, source = excluded.source,"
                " resolved_at = excluded.resolved_at",
                (normalized, name[:MAX_COMPANY_CHARS], source, to_db_time(self._clock.now())),
            )

    def delete(self, domain: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM domain_company WHERE domain = ?", (domain,))
        return cursor.rowcount > 0

    def list_all(self) -> list[tuple[str, CompanyName]]:
        rows = self._db.query("SELECT domain, company, source FROM domain_company ORDER BY domain")
        return [
            (str(row["domain"]), CompanyName(str(row["company"]), str(row["source"])))
            for row in rows
        ]
