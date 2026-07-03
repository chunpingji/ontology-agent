"""Tests for DatabaseConnector + database config validation."""

import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.integration.db_connector import DatabaseConnector
from app.schemas.integration import ConnectorCreate


# ---------------------------------------------------------------------------
# DatabaseConnector
# ---------------------------------------------------------------------------

class TestDatabaseConnectorTestConnection:
    """test_connection: dsn_env resolution + connectivity probe."""

    @pytest.mark.asyncio
    async def test_missing_dsn_env_key(self):
        conn = DatabaseConnector(connection_config={})
        assert await conn.test_connection() is False
        assert conn.last_status == "error"

    @pytest.mark.asyncio
    async def test_missing_env_var(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_DB_DSN", raising=False)
        conn = DatabaseConnector(connection_config={"dsn_env": "NONEXISTENT_DB_DSN"})
        assert await conn.test_connection() is False
        assert conn.last_status == "error"

    @pytest.mark.asyncio
    async def test_success_with_sqlite(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        dsn = f"sqlite:///{db_path}"
        monkeypatch.setenv("TEST_DB_DSN", dsn)
        conn = DatabaseConnector(connection_config={"dsn_env": "TEST_DB_DSN"})
        assert await conn.test_connection() is True
        assert conn.last_status == "ok"
        assert conn.last_error is None


class TestDatabaseConnectorReflect:
    """reflect_tables: structure reflection via SQLAlchemy inspect."""

    def test_reflect_empty_db(self, tmp_path, monkeypatch):
        dsn = f"sqlite:///{tmp_path / 'empty.db'}"
        monkeypatch.setenv("TEST_DB_DSN", dsn)
        conn = DatabaseConnector(connection_config={"dsn_env": "TEST_DB_DSN"})
        tables = conn.reflect_tables()
        assert tables == []

    def test_reflect_with_tables(self, tmp_path, monkeypatch):
        from sqlalchemy import create_engine, text
        db_path = tmp_path / "with_tables.db"
        dsn = f"sqlite:///{db_path}"
        engine = create_engine(dsn)
        with engine.connect() as c:
            c.execute(text("CREATE TABLE drug_product (id INTEGER PRIMARY KEY, name TEXT)"))
            c.execute(text("CREATE TABLE equipment (id INTEGER PRIMARY KEY, code TEXT)"))
            c.commit()
        engine.dispose()
        monkeypatch.setenv("TEST_DB_DSN", dsn)
        conn = DatabaseConnector(connection_config={"dsn_env": "TEST_DB_DSN"})
        tables = conn.reflect_tables()
        names = [t["table"] for t in tables]
        assert "drug_product" in names
        assert "equipment" in names

    def test_reflect_include_tables_filter(self, tmp_path, monkeypatch):
        from sqlalchemy import create_engine, text
        db_path = tmp_path / "filter.db"
        dsn = f"sqlite:///{db_path}"
        engine = create_engine(dsn)
        with engine.connect() as c:
            c.execute(text("CREATE TABLE keep_me (id INTEGER PRIMARY KEY)"))
            c.execute(text("CREATE TABLE skip_me (id INTEGER PRIMARY KEY)"))
            c.commit()
        engine.dispose()
        monkeypatch.setenv("TEST_DB_DSN", dsn)
        conn = DatabaseConnector(
            connection_config={"dsn_env": "TEST_DB_DSN", "include_tables": ["keep_me"]}
        )
        tables = conn.reflect_tables()
        assert len(tables) == 1
        assert tables[0]["table"] == "keep_me"


# ---------------------------------------------------------------------------
# ConnectorCreate schema validation for database
# ---------------------------------------------------------------------------

class TestDatabaseConfigValidation:
    """_validate_database_config via ConnectorCreate model_validator."""

    def test_missing_dsn_env_rejected(self):
        with pytest.raises(Exception, match="dsn_env"):
            ConnectorCreate(
                system_type="database", name="test",
                connection_config={},
            )

    def test_raw_dsn_string_rejected(self):
        with pytest.raises(Exception, match="环境变量名"):
            ConnectorCreate(
                system_type="database", name="test",
                connection_config={"dsn_env": "postgresql://user:pass@host/db"},
            )

    def test_valid_dsn_env_accepted(self):
        c = ConnectorCreate(
            system_type="database", name="test",
            connection_config={"dsn_env": "SOURCE_DB_DSN"},
        )
        assert c.system_type == "database"

    def test_non_database_type_not_affected(self):
        c = ConnectorCreate(
            system_type="APS", name="test",
            connection_config={"source_mode": "inline", "inline_changes": []},
        )
        assert c.system_type == "APS"
