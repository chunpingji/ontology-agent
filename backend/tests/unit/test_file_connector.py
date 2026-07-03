"""Tests for FileConnector + file config validation."""

import pytest

from app.services.integration.file_connector import FileConnector, _guess_format
from app.schemas.integration import ConnectorCreate


# ---------------------------------------------------------------------------
# format guessing
# ---------------------------------------------------------------------------

class TestGuessFormat:
    def test_excel_xlsx(self):
        assert _guess_format("/data/report.xlsx") == "excel"

    def test_excel_xls(self):
        assert _guess_format("legacy.xls") == "excel"

    def test_word_docx(self):
        assert _guess_format("/data/report.docx") == "word"

    def test_pdf(self):
        assert _guess_format("scan.pdf") == "pdf"

    def test_unknown(self):
        assert _guess_format("data.csv") is None

    def test_no_extension(self):
        assert _guess_format("README") is None


# ---------------------------------------------------------------------------
# FileConnector.test_connection
# ---------------------------------------------------------------------------

class TestFileConnectorTestConnection:
    @pytest.mark.asyncio
    async def test_missing_file(self):
        conn = FileConnector(connection_config={"file_path": "/nonexistent/file.xlsx"})
        assert await conn.test_connection() is False
        assert conn.last_status == "error"

    @pytest.mark.asyncio
    async def test_empty_path(self):
        conn = FileConnector(connection_config={"file_path": ""})
        assert await conn.test_connection() is False

    @pytest.mark.asyncio
    async def test_existing_file(self, tmp_path):
        f = tmp_path / "test.xlsx"
        f.write_bytes(b"PK\x03\x04dummy")
        conn = FileConnector(connection_config={"file_path": str(f)})
        assert await conn.test_connection() is True
        assert conn.last_status == "ok"

    @pytest.mark.asyncio
    async def test_empty_file_rejected(self, tmp_path):
        f = tmp_path / "empty.xlsx"
        f.write_bytes(b"")
        conn = FileConnector(connection_config={"file_path": str(f)})
        assert await conn.test_connection() is False
        assert "空" in (conn.last_error or "")

    @pytest.mark.asyncio
    async def test_glob_pattern(self, tmp_path):
        (tmp_path / "a.xlsx").write_bytes(b"PK\x03\x04data")
        (tmp_path / "b.xlsx").write_bytes(b"PK\x03\x04data")
        conn = FileConnector(connection_config={"file_path": str(tmp_path / "*.xlsx")})
        assert await conn.test_connection() is True

    @pytest.mark.asyncio
    async def test_glob_no_match(self, tmp_path):
        conn = FileConnector(connection_config={"file_path": str(tmp_path / "*.xyz")})
        assert await conn.test_connection() is False


# ---------------------------------------------------------------------------
# FileConnector.format property
# ---------------------------------------------------------------------------

class TestFileConnectorFormat:
    def test_explicit_format(self):
        conn = FileConnector(connection_config={"file_path": "data.bin", "format": "excel"})
        assert conn.format == "excel"

    def test_guessed_format(self):
        conn = FileConnector(connection_config={"file_path": "/data/report.docx"})
        assert conn.format == "word"

    def test_unknown_format(self):
        conn = FileConnector(connection_config={"file_path": "data.csv"})
        assert conn.format == ""


# ---------------------------------------------------------------------------
# ConnectorCreate schema validation for file
# ---------------------------------------------------------------------------

class TestFileConfigValidation:
    def test_missing_file_path_rejected(self):
        with pytest.raises(Exception, match="file_path"):
            ConnectorCreate(
                system_type="file", name="test",
                connection_config={},
            )

    def test_empty_file_path_rejected(self):
        with pytest.raises(Exception, match="file_path"):
            ConnectorCreate(
                system_type="file", name="test",
                connection_config={"file_path": "  "},
            )

    def test_invalid_format_rejected(self):
        with pytest.raises(Exception, match="format"):
            ConnectorCreate(
                system_type="file", name="test",
                connection_config={"file_path": "/data/f.csv", "format": "csv"},
            )

    def test_valid_config_accepted(self):
        c = ConnectorCreate(
            system_type="file", name="test",
            connection_config={"file_path": "/data/report.xlsx", "format": "excel"},
        )
        assert c.system_type == "file"

    def test_auto_format_accepted(self):
        c = ConnectorCreate(
            system_type="file", name="test",
            connection_config={"file_path": "/data/report.xlsx"},
        )
        assert c.system_type == "file"
