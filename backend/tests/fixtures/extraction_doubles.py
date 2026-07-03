"""T002 — reusable extraction test doubles for feature 014.

Two source stand-ins that let US1 (DB) and US2 (API) exercise the declarative
pipeline without touching a real intranet source:

- ``make_source_table`` — a **file-based** SQLite database (in-memory SQLite is
  connection-private, so the reader's own ``create_engine`` would not see the
  rows) exposing a ``drug_product`` table, with the DSN published under an
  environment variable name (credentials-via-env, FR-006). Returns the env-var
  *name* the class binding's ``source_system`` points at.
- ``PaginatedRestStub`` — a deterministic paginated JSON endpoint injected via
  the connector's ``http_fetcher`` seam (same DI pattern as
  ``DocumentRepositoryConnector``), so pagination / bearer-auth / degradation
  are testable offline.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text

# Default rows mirror quickstart Scenario A (raw source values pre-transform:
# risk is the Chinese term, normalized to the ontology enum by controlled_vocab).
DEFAULT_ROWS = [
    {"id": 1, "approval_no": "国药准字H20003007", "risk": "高", "mfr_code": "M001", "drug_name": "阿司匹林"},
    {"id": 2, "approval_no": "国药准字H20050001", "risk": "中", "mfr_code": "M002", "drug_name": "布洛芬"},
    {"id": 3, "approval_no": "国药准字H20090001", "risk": "低", "mfr_code": "M001", "drug_name": "对乙酰氨基酚"},
]

_COLUMNS = ("id", "approval_no", "risk", "mfr_code", "drug_name")


def make_source_table(
    tmp_path: Path,
    *,
    env_name: str = "DRUG_DB_DSN",
    table: str = "drug_product",
    rows: list[dict] | None = None,
    columns: tuple[str, ...] = _COLUMNS,
) -> str:
    """Create a temp SQLite ``table`` seeded with ``rows`` and publish its DSN
    under ``env_name``. Returns ``env_name`` (what a class binding's
    ``source_system`` references). Caller owns cleanup of the env var.
    """
    rows = DEFAULT_ROWS if rows is None else rows
    db_path = tmp_path / "source.db"
    dsn = f"sqlite:///{db_path}"
    os.environ[env_name] = dsn

    col_defs = ", ".join(
        "id INTEGER PRIMARY KEY" if c == "id" else f"{c} TEXT" for c in columns
    )
    engine = create_engine(dsn)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {table} ({col_defs})"))
        if rows:
            placeholders = ", ".join(f":{c}" for c in columns)
            stmt = text(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            )
            for r in rows:
                conn.execute(stmt, {c: r.get(c) for c in columns})
    engine.dispose()
    return env_name


def make_pages(items: list[dict], page_size: int = 2, *, data_key: str = "data",
               cursor_key: str = "next") -> list[dict]:
    """Chunk ``items`` into cursor-paginated response pages.

    Page ``i`` carries ``{data_key: [...], cursor_key: "<i+1>"}``; the last page
    carries ``cursor_key: None``. Matches a ``{"style":"cursor",
    "cursor_path":"$.next"}`` connector config.
    """
    pages: list[dict] = []
    for start in range(0, max(len(items), 1), page_size):
        chunk = items[start : start + page_size]
        idx = start // page_size
        has_more = start + page_size < len(items)
        pages.append({data_key: chunk, cursor_key: str(idx + 1) if has_more else None})
    return pages


class PaginatedRestStub:
    """Deterministic paginated JSON endpoint (injected as ``http_fetcher``).

    ``fetch(url, headers, params)`` returns the page addressed by the request's
    ``cursor`` param (absent → first page), records every call (for auth-header
    and pagination assertions), and can simulate an unreachable/timed-out
    intranet host via ``simulate`` (drives graceful-degradation tests).
    """

    def __init__(self, pages: list[dict], *, simulate: str | None = None,
                 cursor_key: str = "next") -> None:
        self.pages = pages
        self.simulate = simulate
        self.cursor_key = cursor_key
        self.calls: list[dict] = []

    async def fetch(self, url: str, headers: dict, params: dict) -> dict:
        self.calls.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        if self.simulate in ("unreachable", "timeout"):
            # httpx errors are what the real transport raises; the reader treats
            # them as intranet-down → degraded (never a crash).
            import httpx

            if self.simulate == "timeout":
                raise httpx.ReadTimeout("stub: timeout", request=None)
            raise httpx.ConnectError("stub: connection refused", request=None)

        cursor = (params or {}).get("cursor")
        idx = 0 if cursor in (None, "") else int(cursor)
        if idx >= len(self.pages):
            return {"data": [], self.cursor_key: None}
        return self.pages[idx]

    @property
    def last_headers(self) -> dict:
        return self.calls[-1]["headers"] if self.calls else {}
