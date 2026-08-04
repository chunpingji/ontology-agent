"""Auth domain calls — login / whoami.

Mirrors ``frontend/src/lib/api.ts``: ``POST /api/auth/login`` issues a 12h bearer
token; ``GET /api/auth/me`` echoes the resolved identity. Logout is local-only
(the token is stateless) and lives in :mod:`core.session`.
"""

from __future__ import annotations

from typing import Any

from cli_anything.ontology_agent.core.client import HttpClient


def login(client: HttpClient, username: str, password: str) -> dict[str, Any]:
    """Return ``{token, username, role, display_name}`` on success (401 → AuthError)."""
    return client.post("/api/auth/login", json_body={"username": username, "password": password})


def whoami(client: HttpClient) -> dict[str, Any]:
    """Return ``{username, role}`` for the resolved caller."""
    return client.get("/api/auth/me")
