"""Config: defaults, precedence, persistence perms, redaction, auth headers."""

from __future__ import annotations

import os
import stat

from cli_anything.ontology_agent.core.config import (
    DEFAULT_API_URL,
    DEFAULT_ROLE,
    DEFAULT_USER,
    Config,
    config_dir,
    config_path,
)


def test_load_defaults():
    cfg = Config.load()
    assert cfg.api_url == DEFAULT_API_URL
    assert cfg.user == DEFAULT_USER
    assert cfg.role == DEFAULT_ROLE
    assert cfg.token is None


def test_env_overrides_take_effect(monkeypatch):
    monkeypatch.setenv("OA_API_URL", "http://example:9000")
    monkeypatch.setenv("OA_USER", "alice")
    monkeypatch.setenv("OA_TOKEN", "tok-123")
    cfg = Config.load()
    assert cfg.api_url == "http://example:9000"
    assert cfg.user == "alice"
    assert cfg.token == "tok-123"


def test_save_reload_and_file_perms():
    cfg = Config.load().update(api_url="http://persisted:8000")
    assert config_path().is_file()
    mode = stat.S_IMODE(os.stat(config_path()).st_mode)
    assert mode == 0o600
    assert Config.load().api_url == "http://persisted:8000"


def test_config_dir_perms():
    d = config_dir()
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_redacted_hides_token():
    cfg = Config.load().update(token="supersecrettoken")
    red = cfg.redacted()
    assert red["token"] != "supersecrettoken"
    assert red["token"].startswith("supersec")


def test_auth_headers_include_all_when_token_present():
    cfg = Config(user="analyst", role="senior_analyst", token="tok")
    h = cfg.auth_headers()
    assert h["X-User"] == "analyst"
    assert h["X-Role"] == "senior_analyst"
    assert h["Authorization"] == "Bearer tok"


def test_auth_headers_omit_authorization_without_token():
    cfg = Config(user="analyst", role="senior_analyst", token=None)
    assert "Authorization" not in cfg.auth_headers()


def test_timeout_tuple():
    cfg = Config(timeout_connect=3.0, timeout_read=7.0)
    assert cfg.timeout == (3.0, 7.0)
