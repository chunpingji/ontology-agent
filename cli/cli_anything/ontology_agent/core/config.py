"""Persistent CLI configuration — ``$XDG_CONFIG_HOME/ontology-agent-cli/config.json``.

Holds the backend URL and the caller identity used to reach it. The identity
mirrors the frontend Report Center exactly (``frontend/src/lib/api.ts``):

* ``user`` / ``role`` → sent as ``X-User`` / ``X-Role`` on every request (a dev
  gateway with ``auth_required=false`` trusts these). Default ``analyst`` /
  ``senior_analyst`` — same as the workbench's ``DEFAULT_IDENTITY``.
* ``token`` → when set, also attached as ``Authorization: Bearer`` (the frontend
  sends both; under enforcement the backend resolves identity from the token and
  ignores the dev headers).

The file is written 0600 and its directory 0700 because it may hold a bearer
token. ``config show`` redacts the token.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_USER = "analyst"
DEFAULT_ROLE = "senior_analyst"

# Env overrides (highest precedence below explicit CLI flags).
ENV_API_URL = "OA_API_URL"
ENV_USER = "OA_USER"
ENV_ROLE = "OA_ROLE"
ENV_TOKEN = "OA_TOKEN"


def config_dir() -> Path:
    """Return (creating, 0700) the config directory.

    Honors ``$XDG_CONFIG_HOME`` (Linux/XDG convention), else ``~/.config``.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    d = root / "ontology-agent-cli"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)  # 0700
    except OSError:
        pass
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    """CLI configuration. Persisted fields only — transient flag/env overrides
    are layered on the in-memory instance by the root command, never saved."""

    api_url: str = DEFAULT_API_URL
    user: str | None = DEFAULT_USER
    role: str | None = DEFAULT_ROLE
    token: str | None = None
    verify_ssl: bool = True
    # (connect, read) seconds for ordinary JSON calls.
    timeout_connect: float = 10.0
    timeout_read: float = 120.0

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls) -> "Config":
        """Load the *persisted* config from disk, then layer env overrides in-memory.

        CLI flag overrides are applied by the root command on top of this result
        and must never be saved back — they are transient for the current run only.

        Raises :class:`~cli_anything.ontology_agent.core.errors.ConfigError` (exit 3)
        when the config file exists but is unreadable or not a JSON object.
        """
        # Import here to avoid a circular import (errors → config → errors).
        from cli_anything.ontology_agent.core.errors import ConfigError

        path = config_path()
        if not path.is_file():
            persisted = cls()
        else:
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
            except OSError as e:
                raise ConfigError(f"无法读取配置文件 {path}：{e}") from e
            except json.JSONDecodeError as e:
                raise ConfigError(f"配置文件格式错误（非合法 JSON）：{path}：{e}") from e
            if not isinstance(data, dict):
                raise ConfigError(f"配置文件必须是 JSON 对象，实际类型：{type(data).__name__}")
            allowed = {f.name for f in fields(cls)}
            persisted = cls(**{k: v for k, v in data.items() if k in allowed})

        # Env overrides — applied to an in-memory copy; never written back to disk.
        cfg = persisted
        cfg.api_url = os.environ.get(ENV_API_URL, cfg.api_url)
        if ENV_USER in os.environ:
            cfg.user = os.environ[ENV_USER] or None
        if ENV_ROLE in os.environ:
            cfg.role = os.environ[ENV_ROLE] or None
        if ENV_TOKEN in os.environ:
            cfg.token = os.environ[ENV_TOKEN] or None
        return cfg

    def save(self) -> None:
        """Atomically persist to disk with 0600 permissions.

        Only call this from commands that explicitly mutate persisted config
        (``config set``, ``config reset``, ``auth login``, ``auth logout``).
        Never call from the root command's transient-flag overlay.
        """
        _atomic_write_json(config_path(), asdict(self))

    def update(self, **updates: Any) -> "Config":
        """Return a new Config with non-None updates applied and persisted.

        Only persisted fields are written; callers must not pass transient
        flag values (api_url from --api-url, token from --token, etc.) here
        unless the intent is to persist them (e.g. ``auth login`` persisting
        the token, ``config set`` persisting the URL).
        """
        current = asdict(self)
        current.update({k: v for k, v in updates.items() if v is not None})
        new = Config(**current)
        new.save()
        return new

    def reset(self) -> "Config":
        fresh = Config()
        fresh.save()
        return fresh

    # ------------------------------------------------------------------ #
    # Views
    # ------------------------------------------------------------------ #

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def redacted(self) -> dict[str, Any]:
        """`config show` view — never print the full bearer token."""
        d = asdict(self)
        if self.token:
            d["token"] = self.token[:8] + "…" if len(self.token) > 8 else "…"
        return d

    # ------------------------------------------------------------------ #
    # Request wiring
    # ------------------------------------------------------------------ #

    def auth_headers(self) -> dict[str, str]:
        """Identity headers — mirrors the frontend ``identityHeaders()``:
        X-User/X-Role whenever set, plus Bearer when a token is present."""
        headers: dict[str, str] = {}
        if self.user:
            headers["X-User"] = self.user
        if self.role:
            headers["X-Role"] = self.role
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.timeout_connect, self.timeout_read)


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically (temp + os.replace) with 0600 perms on the result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
