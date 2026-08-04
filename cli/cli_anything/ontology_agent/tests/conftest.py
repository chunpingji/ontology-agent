"""Shared test fixtures — a stub HttpClient and an isolated config home.

No live backend: the stub records calls and returns programmed responses, and
the config dir is redirected to a tmp path so tests never touch the real one.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from click.testing import CliRunner

from cli_anything.ontology_agent.core.client import FileOrJson

# Matches an async-report *status* path (…/reports/<id>) but not the collection
# (…/reports) nor a download (…/reports/<id>/download).
_STATUS_RE = re.compile(r"/reports/[^/]+$")


class StubClient:
    """Programmable stand-in for :class:`HttpClient`.

    * ``file_or_json`` — what ``request_file_or_json`` returns (the POST result).
    * ``status_responses`` — FIFO queue for report-status polls.
    * ``get_map`` — path → value for ordinary GETs (entities, coverage, …).
    * ``file_download`` — ``(bytes, filename)`` for ``get_file``.
    * ``calls`` — ordered log of every call for assertions.
    """

    def __init__(self) -> None:
        self.file_or_json: FileOrJson | None = None
        self.status_responses: list[dict[str, Any]] = []
        self.get_map: dict[str, Any] = {}
        self.file_download: tuple[bytes, str | None] = (b"", None)
        self.post_map: dict[str, Any] = {}
        self.calls: list[tuple] = []

    # -- verbs used by the domain layer ------------------------------------ #

    def request_file_or_json(self, method: str, path: str, *, json_body: Any = None) -> FileOrJson:
        self.calls.append(("REQ", method, path))
        assert self.file_or_json is not None, "file_or_json not programmed"
        return self.file_or_json

    def get(self, path: str, *, params: dict | None = None) -> Any:
        self.calls.append(("GET", path, params))
        if _STATUS_RE.search(path):
            assert self.status_responses, f"status queue empty for {path}"
            return self.status_responses.pop(0)
        if path in self.get_map:
            return self.get_map[path]
        raise AssertionError(f"unexpected GET {path}")

    def get_file(self, path: str, *, params: dict | None = None) -> tuple[bytes, str | None]:
        self.calls.append(("GETFILE", path))
        return self.file_download

    def post(self, path: str, *, json_body: Any = None, params: dict | None = None) -> Any:
        self.calls.append(("POST", path, json_body))
        return self.post_map.get(path)

    def delete(self, path: str, *, params: dict | None = None) -> Any:
        self.calls.append(("DELETE", path))
        return None


@pytest.fixture(autouse=True)
def cli_env(tmp_path, monkeypatch):
    """Redirect the config home to a tmp dir and clear OA_* env for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("OA_API_URL", "OA_USER", "OA_ROLE", "OA_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def stub() -> StubClient:
    return StubClient()


@pytest.fixture
def install_client(monkeypatch):
    """Return an installer that makes every ``build_client`` yield the given stub."""

    def _install(client: Any) -> Any:
        monkeypatch.setattr(
            "cli_anything.ontology_agent.commands._ctx.HttpClient",
            lambda *args, **kwargs: client,
        )
        return client

    return _install


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()
