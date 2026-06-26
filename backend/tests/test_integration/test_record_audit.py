"""记录层物化留痕（007 US1，T013；宪章 III ALCOA+）。

doc_repo 同步成功 → FactMaterializationRun 完整（success/计数/水位/changes/event_ids/finished_at）；
审计哈希链落 action="integration.materialize"、actor="system"、details.run_id/change_count。
"""

from __future__ import annotations

import asyncio

from app.models.integration import IntegrationConnector
from app.models.reasoning import AuditLog
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import DOC_REPO_CHANGE_TTR_V2, inline_config


def test_doc_sync_records_run_and_audit(db, fake_engine):
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS",
        connection_config=inline_config(changes=[DOC_REPO_CHANGE_TTR_V2]),
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    run = asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    assert run.status == "success"
    assert run.change_count == 1
    assert run.cursor_to["version"] == 2
    assert run.changes and run.changes[0]["entity_id"] == "doc-TTR-001"
    assert run.event_ids and len(run.event_ids) == 1
    assert run.finished_at is not None
    assert run.connector_id == c.id

    log = (
        db.query(AuditLog)
        .filter(AuditLog.action == "integration.materialize")
        .order_by(AuditLog.seq.desc())
        .first()
    )
    assert log is not None
    assert log.actor == "system"
    assert log.details["run_id"] == str(run.id)
    assert log.details["change_count"] == 1
