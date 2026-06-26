"""US4 凭据注入契约测试（T038；FR-010 / 宪章安全）。

契约 [doc-repo-connector C3](../../../specs/007-rnd-document-fact-source/contracts/doc-repo-connector.md)：
`http` 模式凭据经 **env 变量名引用**（`token_ref="EDMS_TOKEN"`）运行时 `os.environ` 解析为
请求头（C3.1）；明文凭据 MUST NOT 入 `connection_config`（C3.2），也 MUST NOT 出现于
`FactMaterializationRun.changes` / 审计 / 日志（C3.3）。
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.models.integration import IntegrationConnector
from app.models.reasoning import AuditLog
from app.services.integration import doc_repo_connector as drc_mod
from app.services.integration.doc_repo_connector import DocumentRepositoryConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    http_config,
    http_endpoint_envelopes,
)

SECRET = "super-secret-edms-token"
FACTS = "http://slpra.org/facts#"


@pytest.fixture(autouse=True)
def _edms_token(monkeypatch):
    monkeypatch.setenv("EDMS_TOKEN", SECRET)


def _run(coro):
    return asyncio.run(coro)


# --- C3.1 凭据运行时解析 ----------------------------------------------------


def test_token_ref_resolved_from_environ_into_headers():
    """C3.1：token_ref 经 os.environ 解析为 Authorization 头（凭据不入库，运行时注入）。"""
    captured: dict = {}

    async def fetch(base_url, headers, cursor):
        captured["headers"] = dict(headers)
        return http_endpoint_envelopes()

    conn = DocumentRepositoryConnector(
        http_config(token_ref="EDMS_TOKEN"), {}, timeout=2.0, http_fetcher=fetch
    )
    _run(conn.fetch_incremental({}))
    assert captured["headers"].get("Authorization") == f"Bearer {SECRET}"


def test_missing_env_credential_raises_not_silent():
    """token_ref 指向未注入的 env → 明确报错（绝不静默用空凭据触达端点）。"""

    async def fetch(base_url, headers, cursor):  # pragma: no cover - 不应被调用
        raise AssertionError("凭据缺失时不应触达端点")

    conn = DocumentRepositoryConnector(
        http_config(token_ref="ABSENT_VAR"), {}, timeout=2.0, http_fetcher=fetch
    )
    with pytest.raises((ValueError, KeyError)):
        _run(conn.fetch_incremental({}))


# --- C3.2 持久化配置无明文凭据 ----------------------------------------------


def test_persisted_connection_config_has_no_plaintext_credential(db):
    """C3.2：持久化的 connection_config 仅含变量名引用，无明文 token/password/密钥。"""
    cfg = http_config(token_ref="EDMS_TOKEN")
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS-http",
        connection_config=cfg,
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    stored = c.connection_config
    assert stored["token_ref"] == "EDMS_TOKEN"  # 仅变量名引用
    blob = json.dumps(stored, ensure_ascii=False)
    assert SECRET not in blob
    for forbidden_key in ("token", "password", "secret", "api_key", "apikey"):
        assert forbidden_key not in stored, f"connection_config 不得含明文凭据键 {forbidden_key!r}"


# --- C3.3 凭据不入 changes / 审计 / 日志 ------------------------------------


def test_credentials_absent_from_run_changes_audit_and_logs(db, fake_engine, monkeypatch, caplog):
    """C3.3：经 run_sync（工厂路径）物化后，凭据不出现于 changes/审计/日志（最小暴露）。"""

    async def fetch(base_url, headers, cursor):
        return http_endpoint_envelopes()

    # 工厂路径不传 http_fetcher → 打桩模块级默认传输（避免真实网络）。
    def _stub_transport(base_url, headers, cursor, timeout):
        return fetch(base_url, headers, cursor)

    monkeypatch.setattr(drc_mod, "_httpx_get_changes", _stub_transport)

    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS-http",
        connection_config=http_config(token_ref="EDMS_TOKEN"),
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    with caplog.at_level(logging.DEBUG):
        run = _run(FactMaterializer(db, fake_engine).run_sync(c))

    assert run.status == "success"
    assert run.change_count == 1

    # changes：归一化文档变更，绝无 Authorization/token。
    changes_blob = json.dumps(run.changes, ensure_ascii=False)
    assert SECRET not in changes_blob
    assert "Authorization" not in changes_blob

    # 审计明细：仅 run_id/change_count，无凭据。
    audit_rows = db.query(AuditLog).all()
    audit_blob = json.dumps([r.details for r in audit_rows], ensure_ascii=False)
    assert SECRET not in audit_blob

    # 日志：不回显凭据。
    assert SECRET not in caplog.text
