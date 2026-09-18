"""Run document-analysis PostgreSQL tests with an explicit disposable database."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_FILE = BACKEND / ".env.postgresql-test"
ENV_KEY = "DOCUMENT_ANALYSIS_TEST_DATABASE_URL"
DEFAULT_TESTS = [
    "tests/test_extraction/test_document_run_execution_postgresql.py",
    "tests/test_extraction/test_performance_postgresql.py",
    "tests/test_extraction/test_ranking_budget_control_postgresql.py",
    "tests/test_extraction/test_evidence_repair_postgresql.py",
    "tests/test_extraction/test_current_state_transactions.py"
    "::test_postgresql_competing_current_work_commits_have_one_winner",
]


def test_database_url():
    value = os.environ.get(ENV_KEY)
    if not value and CONFIG_FILE.is_file():
        for line in CONFIG_FILE.read_text().splitlines():
            if line.startswith(ENV_KEY + "="):
                value = line.split("=", 1)[1].strip()
                break
    if not value:
        raise SystemExit(f"Configure {ENV_KEY} or {CONFIG_FILE}; no application DB fallback.")
    try:
        url = make_url(value)
    except Exception:
        raise SystemExit("Invalid test database URL; connection details omitted.") from None
    if (
        url.get_backend_name() != "postgresql"
        or "document_analysis" not in (url.database or "")
        or not url.database.endswith("_test")
    ):
        raise SystemExit("A disposable PostgreSQL document_analysis*_test database is required.")
    return value, url


def initialize_empty_database(value):
    # These historical migrations import current metadata. Bootstrap only a new
    # empty test schema; stamp is not evidence of historical migration success.
    os.environ["DATABASE_URL"] = value
    sys.path.insert(0, str(BACKEND))
    from alembic.config import Config

    import app.models  # noqa: F401
    from alembic import command
    from app.db import Base

    engine = create_engine(value)
    try:
        with engine.begin() as connection:
            if inspect(connection).get_table_names():
                raise SystemExit(
                    "Test database is not empty; initialization will not overwrite it."
                )
            Base.metadata.create_all(connection)
        command.stamp(Config(str(BACKEND / "alembic.ini")), "head")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true", help="Initialize an empty test schema only.")
    args, pytest_args = parser.parse_known_args()
    os.chdir(BACKEND)
    value, url = test_database_url()
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            identity = connection.execute(
                text(
                    "SELECT current_database(), current_user, rolsuper, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            if identity[0] != url.database or any(identity[2:]):
                raise SystemExit("Use the dedicated test database and a non-administrator role.")
            print(f"PostgreSQL test database: {identity[0]} ({url.host}:{url.port})", flush=True)
    finally:
        engine.dispose()
    if args.init:
        initialize_empty_database(value)
        print("Empty test schema initialized and stamped at code head; migrations not replayed.")
        return 0
    env = {**os.environ, ENV_KEY: value}
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--tb=short",
            *(pytest_args or DEFAULT_TESTS),
        ],
        cwd=BACKEND,
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
