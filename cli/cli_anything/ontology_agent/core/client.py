"""HTTP client wrapping ``requests.Session`` for the ontology-agent backend.

Responsibilities:
* attach identity headers on every request (see :meth:`Config.auth_headers`);
* translate responses into parsed JSON or typed :class:`CliError`s;
* distinguish a JSON body from a binary ``.docx`` by ``Content-Type`` — the
  report-generate endpoint returns either, depending on server LLM flags;
* stream file downloads.

Deliberately NOT implemented: automatic retry on POST. Report generation is not
idempotent (each call inserts a new ``GeneratedReport`` row), so a transparent
retry could silently produce duplicates.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests

from cli_anything.ontology_agent.core.config import Config
from cli_anything.ontology_agent.core.errors import (
    ApiError,
    AuthError,
    ConflictError,
    ForbiddenError,
    NetworkError,
    NotFoundError,
    PreconditionError,
    ServerError,
)

# Read timeout for endpoints that may build/stream a document server-side.
_FILE_READ_TIMEOUT = 600.0


@dataclass
class FileOrJson:
    """Result of an endpoint that may return either JSON or a binary file."""

    is_file: bool
    json: Any = None
    content: bytes | None = None
    filename: str | None = None
    content_type: str | None = None


class HttpClient:
    """Thin wrapper over ``requests.Session`` for the ontology-agent API."""

    def __init__(self, config: Config, *, verbose: bool = False) -> None:
        self._config = config
        self._verbose = verbose
        self._http = requests.Session()

    # ------------------------------------------------------------------ #
    # Verbs
    # ------------------------------------------------------------------ #

    def get(self, path: str, *, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, json_body: Any = None, params: dict | None = None) -> Any:
        return self._request("POST", path, json_body=json_body, params=params)

    def delete(self, path: str, *, params: dict | None = None) -> Any:
        return self._request("DELETE", path, params=params)

    def request_file_or_json(
        self, method: str, path: str, *, json_body: Any = None
    ) -> FileOrJson:
        """Issue a request whose response is JSON *or* a binary file.

        The branch is decided by ``Content-Type``: only an explicit
        ``application/json`` is parsed as JSON — anything else (including
        ``octet-stream`` / the docx MIME type) is treated as a file. A body
        that merely fails to JSON-parse is never mistaken for a document.
        """
        resp = self._send(method, path, json_body=json_body, read_timeout=_FILE_READ_TIMEOUT)
        if resp.status_code >= 400:
            self._raise_for_status(resp, method, path)
        ctype = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            return FileOrJson(is_file=False, json=resp.json(), content_type=ctype)
        return FileOrJson(
            is_file=True,
            content=resp.content,
            filename=parse_content_disposition(resp.headers.get("content-disposition")),
            content_type=ctype,
        )

    def get_file(self, path: str, *, params: dict | None = None) -> tuple[bytes, str | None]:
        """Download a binary file; return ``(content, filename)``."""
        resp = self._send("GET", path, params=params, read_timeout=_FILE_READ_TIMEOUT)
        if resp.status_code >= 400:
            self._raise_for_status(resp, "GET", path)
        return resp.content, parse_content_disposition(resp.headers.get("content-disposition"))

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None) -> Any:
        resp = self._send(method, path, json_body=json_body, params=params)
        return self._handle_json(resp, method, path)

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        read_timeout: float | None = None,
    ) -> requests.Response:
        url = f"{self._config.api_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Accept": "application/json", **self._config.auth_headers()}
        timeout = (self._config.timeout_connect, read_timeout or self._config.timeout_read)
        if self._verbose:
            print(f"→ {method} {url}", file=sys.stderr)
        try:
            return self._http.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=timeout,
                verify=self._config.verify_ssl,
            )
        except requests.ConnectionError as e:
            raise NetworkError(
                f"无法连接后端 {self._config.api_url}：{e}。后端是否已启动？可用 --api-url 覆盖。"
            ) from e
        except requests.Timeout as e:
            raise NetworkError(f"请求超时：{method} {path}") from e
        except requests.RequestException as e:
            raise NetworkError(f"HTTP 请求失败：{e}") from e

    @staticmethod
    def _handle_json(resp: requests.Response, method: str, path: str) -> Any:
        status = resp.status_code
        if 200 <= status < 300:
            if status == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text
        HttpClient._raise_for_status(resp, method, path)

    @staticmethod
    def _raise_for_status(resp: requests.Response, method: str, path: str) -> None:
        status = resp.status_code
        body: Any = None
        detail: Any = None
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = body.get("detail") or body.get("message")
            else:
                detail = body
        except ValueError:
            detail = (resp.text or "")[:500] or None
        suffix = f"：{detail}" if detail else ""
        msg = f"{method} {path} → HTTP {status}{suffix}"
        if status == 401:
            raise AuthError(msg, detail=body)
        if status == 403:
            raise ForbiddenError(msg, detail=body)
        if status == 404:
            raise NotFoundError(msg, detail=body)
        if status == 409:
            raise ConflictError(msg, detail=body)
        if status == 422:
            raise PreconditionError(msg, detail=body)
        if 400 <= status < 500:
            raise ApiError(msg, status_code=status, detail=body)
        raise ServerError(msg, status_code=status, detail=body)


def parse_content_disposition(value: str | None) -> str | None:
    """Extract a filename from a ``Content-Disposition`` header.

    Prefers RFC 5987 ``filename*=charset'language'value`` (handles CJK report
    names the backend emits), then falls back to quoted/bare ``filename=``.
    """
    if not value:
        return None
    m = re.search(r"filename\*\s*=\s*([^']+)'([^']*)'([^;]+)", value, re.IGNORECASE)
    if m:
        charset = m.group(1).strip() or "utf-8"
        encoded = m.group(3).strip()
        try:
            return urllib.parse.unquote(encoded, encoding=charset)
        except Exception:
            pass
    m = re.search(r'filename\s*=\s*"([^"]+)"', value, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", value, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return None
