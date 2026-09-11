"""Paid-stage recovery on an explicitly provisioned disposable PostgreSQL database."""

import pytest
from sqlalchemy.orm import Session

from tests.test_extraction.test_document_run_execution_postgresql import pg_engine as pg_engine
from tests.test_extraction.test_evidence_repair import (
    test_production_stage_receipt_survives_interruption_without_rediscovery as check_stage_recovery,
)


@pytest.mark.parametrize("incremental", [False, True])
def test_postgresql_committed_discovery_survives_worker_loss(
    pg_engine, tmp_path, monkeypatch, incremental,
):
    with Session(pg_engine) as db:
        check_stage_recovery(db, tmp_path, monkeypatch, incremental)
