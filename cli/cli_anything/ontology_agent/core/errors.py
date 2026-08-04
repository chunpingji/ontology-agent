"""Typed CLI exceptions mapped to stable exit codes.

Exit-code scheme (agent-native — codes are part of the contract):
    0   ok
    1   unexpected/internal error
    2   usage (Click argument errors + :class:`UsageError`)
    3   config
    4   auth / not authenticated (HTTP 401)
    5   forbidden (HTTP 403)
    6   not found (HTTP 404)
    7   precondition / unprocessable (HTTP 422)
    8   conflict (HTTP 409)
    9   network / server / other HTTP (connection, timeout, 5xx, unclassified 4xx)
    10  report generation failed (report_status == "failed")
    11  wait timed out while polling an async report
    130 interrupted (SIGINT)
"""

from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_AUTH = 4
EXIT_FORBIDDEN = 5
EXIT_NOT_FOUND = 6
EXIT_PRECONDITION = 7
EXIT_CONFLICT = 8
EXIT_NETWORK = 9
EXIT_REPORT_FAILED = 10
EXIT_WAIT_TIMEOUT = 11
EXIT_INTERRUPT = 130


class CliError(Exception):
    """Base class for CLI-level errors carrying a machine code + exit code."""

    exit_code: int = EXIT_UNEXPECTED
    code: str = "error"

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class UsageError(CliError):
    """Invalid CLI usage (bad arguments, missing input, refusing to overwrite)."""

    exit_code = EXIT_USAGE
    code = "usage_error"


class ConfigError(CliError):
    """Invalid or missing configuration."""

    exit_code = EXIT_CONFIG
    code = "config_error"


class AuthError(CliError):
    """Not authenticated / token rejected (HTTP 401)."""

    exit_code = EXIT_AUTH
    code = "auth_error"


class ForbiddenError(CliError):
    """Authenticated but not permitted (HTTP 403)."""

    exit_code = EXIT_FORBIDDEN
    code = "forbidden"


class NotFoundError(CliError):
    """Resource not found (HTTP 404)."""

    exit_code = EXIT_NOT_FOUND
    code = "not_found"


class PreconditionError(CliError):
    """Request understood but preconditions unmet (HTTP 422).

    For report generation this is the doc-not-classified / not-a-CMCReport /
    no-relationships gate the backend enforces.
    """

    exit_code = EXIT_PRECONDITION
    code = "precondition_failed"


class ConflictError(CliError):
    """State conflict, e.g. downloading a report that is not yet completed (409)."""

    exit_code = EXIT_CONFLICT
    code = "conflict"


class ApiError(CliError):
    """Unclassified HTTP 4xx response."""

    exit_code = EXIT_NETWORK
    code = "api_error"

    def __init__(self, message: str, *, status_code: int, detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.status_code = status_code


class ServerError(CliError):
    """Backend returned a 5xx response."""

    exit_code = EXIT_NETWORK
    code = "server_error"

    def __init__(self, message: str, *, status_code: int, detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.status_code = status_code


class NetworkError(CliError):
    """Could not reach the backend (connection refused / timeout)."""

    exit_code = EXIT_NETWORK
    code = "network_error"


class ReportFailedError(CliError):
    """Async report generation ended in report_status == "failed"."""

    exit_code = EXIT_REPORT_FAILED
    code = "report_failed"

    def __init__(self, message: str, *, report_id: str | None = None, detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.report_id = report_id


class WaitTimeoutError(CliError):
    """Polling exceeded --wait-timeout before the report reached a terminal state."""

    exit_code = EXIT_WAIT_TIMEOUT
    code = "wait_timeout"

    def __init__(self, message: str, *, report_id: str | None = None, detail: Any = None) -> None:
        super().__init__(message, detail=detail)
        self.report_id = report_id
