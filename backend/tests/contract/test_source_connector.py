"""T022 — contract: ``rest_api`` Source Connection config + ``/test`` + generic pull (US2).

Written *before* the connector/reader exist (T024–T031) — MUST FAIL until:

- ``POST /connectors`` validates **intranet-only** ``base_url`` (FR-022) and rejects
  literal credentials, accepting only ``*_env`` references (FR-006);
- ``connector_for`` dispatches ``system_type="rest_api"`` to a ``RestConnector`` whose
  ``/test`` probe **degrades** (``ok:false``) on an unreachable intranet host *without
  raising* (R12/FR-019);
- ``RestConnector`` paginates a cursor endpoint and injects the bearer token from the
  environment at call time (never persisted, never logged).

Offline-deterministic: the connector's HTTP transport is the injectable
``http_fetcher`` seam (same DI pattern as ``DocumentRepositoryConnector``), driven by
``PaginatedRestStub``; no real network. See contracts/source-connector.md.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from tests.fixtures.feature014 import ANALYST, PaginatedRestStub, make_pages

INTEGRATION = "/api/integration"
CONNECTORS = f"{INTEGRATION}/connectors"

INTRANET_BASE = "http://drug-registry.intranet.local"
PUBLIC_BASE = "https://api.public-cloud.com"
TOKEN_ENV = "DRUG_API_TOKEN"

_ITEMS = [
    {"drugName": "阿司匹林", "approvalNo": "国药准字H20003007"},
    {"drugName": "布洛芬", "approvalNo": "国药准字H20050001"},
    {"drugName": "对乙酰氨基酚", "approvalNo": "国药准字H20090001"},
]


def _config(**over) -> dict:
    cfg = {
        "base_url": INTRANET_BASE,
        "endpoint": "/api/drugs",
        "auth": {"scheme": "bearer", "token_env": TOKEN_ENV},
        "pagination": {"style": "cursor", "cursor_path": "$.next", "page_size": 2},
    }
    cfg.update(over)
    return cfg


def _create(client, connection_config, *, name="internal-drug-registry"):
    return client.post(
        CONNECTORS,
        json={"name": name, "system_type": "rest_api",
              "connection_config": connection_config},
        headers=ANALYST,
    )


# --- config validation (FR-006 / FR-022) -----------------------------------
def test_create_rest_connector_no_secret_echoed(drug_client):
    r = _create(drug_client, _config())
    assert r.status_code == 201, r.text
    auth = r.json()["connection_config"]["auth"]
    # only the *_env reference is stored/echoed — never a literal secret value.
    assert auth.get("token_env") == TOKEN_ENV
    assert "token" not in auth


def test_public_base_url_rejected(drug_client):
    """A public-internet/cloud base_url is refused (air-gap posture, FR-022)."""
    r = _create(drug_client, _config(base_url=PUBLIC_BASE), name="public-x")
    assert r.status_code == 422, r.text


def test_literal_credential_rejected(drug_client):
    """auth carrying a literal token (not a ``*_env`` ref) is refused (FR-006)."""
    bad = _config(auth={"scheme": "bearer", "token": "super-secret-abc123"})
    r = _create(drug_client, bad, name="literal-cred")
    assert r.status_code == 422, r.text


# --- /test probe: reachable → ok; unreachable → graceful degrade (R12) ------
def test_test_endpoint_reachable(drug_client):
    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        cid = _create(drug_client, _config(inline_pages=make_pages(_ITEMS, 2)),
                      name="reachable-x").json()["id"]
        r = drug_client.post(f"{CONNECTORS}/{cid}/test", headers=ANALYST)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
    finally:
        os.environ.pop(TOKEN_ENV, None)


def test_test_endpoint_unreachable_degrades(drug_client):
    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        cid = _create(
            drug_client,
            _config(inline_pages=make_pages(_ITEMS, 2), simulate="unreachable"),
            name="down-x",
        ).json()["id"]
        r = drug_client.post(f"{CONNECTORS}/{cid}/test", headers=ANALYST)
        assert r.status_code == 200, r.text          # graceful, not a 5xx
        assert r.json()["ok"] is False               # degraded signal, no exception
    finally:
        os.environ.pop(TOKEN_ENV, None)


# --- generic REST/JSON pull (direct RestConnector) -------------------------
def test_rest_connector_paginates_all_items():
    from app.services.integration.rest_connector import RestConnector

    os.environ[TOKEN_ENV] = "tkn-abc"
    try:
        stub = PaginatedRestStub(make_pages(_ITEMS, 2))
        conn = RestConnector(_config(), http_fetcher=stub.fetch)
        items = asyncio.run(conn.fetch_items())
    finally:
        os.environ.pop(TOKEN_ENV, None)

    assert {i["approvalNo"] for i in items} == {
        "国药准字H20003007", "国药准字H20050001", "国药准字H20090001"
    }
    assert len(stub.calls) >= 2                       # cursor followed across pages


def test_bearer_auth_header_from_env():
    from app.services.integration.rest_connector import RestConnector

    os.environ[TOKEN_ENV] = "tkn-xyz-42"
    try:
        stub = PaginatedRestStub(make_pages(_ITEMS, 2))
        conn = RestConnector(_config(), http_fetcher=stub.fetch)
        asyncio.run(conn.fetch_items())
    finally:
        os.environ.pop(TOKEN_ENV, None)

    assert stub.last_headers.get("Authorization") == "Bearer tkn-xyz-42"


def test_missing_token_env_raises_never_silent():
    """Missing credential env → explicit error, never a silent empty-cred fetch (FR-006)."""
    from app.services.integration.rest_connector import RestConnector

    os.environ.pop(TOKEN_ENV, None)
    stub = PaginatedRestStub(make_pages(_ITEMS, 2))
    conn = RestConnector(_config(), http_fetcher=stub.fetch)
    with pytest.raises(ValueError):
        asyncio.run(conn.fetch_items())
