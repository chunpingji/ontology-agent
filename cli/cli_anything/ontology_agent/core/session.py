"""Login-token lifecycle over :class:`Config`.

The backend issues a stateless 12h bearer token (``POST /api/auth/login``) with
no refresh endpoint — logout is purely local (clear the token). These helpers
keep that lifecycle in one place, separate from generic config editing.
"""

from __future__ import annotations

from cli_anything.ontology_agent.core.config import Config


def apply_login(cfg: Config, *, token: str, username: str | None, role: str | None) -> Config:
    """Persist a fresh login: store the token and adopt its username/role so
    subsequent dev-header calls agree with the token's identity."""
    return cfg.update(token=token, user=username, role=role)


def clear_token(cfg: Config) -> Config:
    """Local logout — drop the stored token (dev X-User/X-Role identity kept)."""
    current = cfg.as_dict()
    current["token"] = None
    fresh = Config(**current)
    fresh.save()
    return fresh


def is_authenticated(cfg: Config) -> bool:
    """True when a bearer token is stored. Note: with a dev backend
    (``auth_required=false``) the CLI still works without one via X-User/X-Role."""
    return bool(cfg.token)
