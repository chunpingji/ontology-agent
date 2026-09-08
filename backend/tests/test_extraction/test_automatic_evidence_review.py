import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select

from app.models.evidence import EvidenceCandidateRecord, EvidenceJobState, EvidenceReview
from app.models.extraction import ExtractionJob
from app.schemas.evidence import DocumentProvenance, EvidenceAnchor
from app.services.extraction.candidate_store import CandidateConflict, CandidateStore
from app.services.fact_commit import FactCommitService
from tests.test_extraction.test_fact_commit import assertions, entity


@pytest.fixture
def job(db):
    row = ExtractionJob(id=uuid.uuid4(), source_type="word", status="completed")
    db.add(row)
    db.commit()
    return row


def document_values():
    anchor = EvidenceAnchor(
        document_hash="a" * 64,
        parser_version="4",
        structure_hash="b" * 64,
        evidence_id="paragraph-1",
        section_node_id="section:1",
        block_id="paragraph:1:0",
        physical_page_number=1,
    )
    values = assertions()
    for value in values:
        value.provenance = [
            DocumentProvenance(anchors=[anchor], excerpts=[value.text or "原文依据"])
        ]
        for binding in value.bindings:
            binding.method = "explicit_assertion"
            binding.anchors = [anchor]
    return values


class Writer:
    def write(self, identity, manifest):
        return "isolated-test-world"


def test_document_extraction_automatically_reviews_each_kind_without_publishing(db, job):
    store = CandidateStore(db)
    saved = store.persist_validated(job.id, document_values(), actor="extractor")
    assert {c.kind for c in saved} == {"entity", "property", "relationship"}
    assert all(c.review_status == "confirmed" and c.review_source == "automatic" for c in saved)
    assert all(c.commit_status == "not_requested" for c in saved)
    assert db.get(EvidenceJobState, job.id).snapshot_id is None
    assert not saved[
        -1
    ].positive_eligible  # Default review never changes negation into affirmation.
    reviews = db.scalars(select(EvidenceReview)).all()
    assert len(reviews) == 4 and all(r.actor == "system:automatic-review" for r in reviews)
    store.persist_validated(job.id, document_values(), actor="extractor")
    assert len(db.scalars(select(EvidenceReview)).all()) == 4


def test_failed_validation_and_manual_entries_do_not_receive_automatic_approval(db, job):
    values = document_values()[:2]
    values[0].validation_status = "rejected"
    values[1].validation_status = "conflict"
    values.append(entity("manual"))
    saved = CandidateStore(db).persist_validated(job.id, values, actor="extractor")
    assert all(c.review_status == "pending" and c.review_source is None for c in saved)


def test_publishing_again_reuses_legacy_assertions_without_review_metadata(db, job):
    store = CandidateStore(db)
    saved = store.persist_validated(job.id, document_values(), actor="extractor")
    service = FactCommitService(db, Writer())
    items = [{"candidate_id": c.candidate_id, "revision": c.revision} for c in saved]
    first = service.request(job.id, "first", items, "analyst")
    assert service.apply(first.id).status == "succeeded"
    previous = service.published_snapshot(job.id)
    for candidate in saved:
        row = db.get(EvidenceCandidateRecord, candidate.candidate_id)
        row.payload = {
            key: value
            for key, value in row.payload.items()
            if key not in {"review_source", "review_reason"}
        }
    db.commit()
    second = service.request(job.id, "again", items, "analyst")
    assert service.apply(second.id).status == "succeeded"
    assert service.published_snapshot(job.id)["assertions"] == previous["assertions"]


def test_explicit_rejection_retracts_dependents_and_preserves_historical_snapshot(db, job):
    store = CandidateStore(db)
    saved = store.persist_validated(job.id, document_values(), actor="extractor")
    service = FactCommitService(db, Writer())
    commit = service.request(
        job.id,
        "first",
        [{"candidate_id": c.candidate_id, "revision": c.revision} for c in saved],
        "analyst",
    )
    assert service.apply(commit.id).status == "succeeded"
    previous = service.published_snapshot(job.id)
    root = saved[0]
    with pytest.raises(CandidateConflict):
        store.review(
            root.candidate_id,
            1,
            "rejected",
            "过期页面",
            "analyst",
            expected_review_status="pending",
        )
    with pytest.raises(ValueError, match="reason"):
        store.review(
            root.candidate_id, 1, "rejected", "  ", "analyst", expected_review_status="confirmed"
        )
    rejected = store.review(
        root.candidate_id,
        1,
        "rejected",
        "此处并非药物名称",
        "analyst",
        expected_review_status="confirmed",
    )
    assert rejected.review_status == "rejected" and rejected.review_source == "manual"
    assert rejected.review_reason == "此处并非药物名称"
    latest = service.published_snapshot(job.id)
    assert latest["snapshot_id"] != previous["snapshot_id"]
    assert [r["candidate"]["candidate_id"] for r in latest["assertions"]] == [saved[1].candidate_id]
    assert len(service.published_snapshot(job.id, previous["snapshot_id"])["assertions"]) == 4
    for dependent in saved[2:]:
        current = store.get(dependent.candidate_id)
        assert current.revision == 2 and current.validation_status == "pending"
        assert current.review_source is None and current.commit_status == "not_requested"
    repeated = store.persist_validated(job.id, document_values(), actor="extractor")
    assert repeated[0].review_status == "rejected"  # Reruns cannot overwrite the user's decision.
    assert repeated[0].review_reason == rejected.review_reason
    with pytest.raises(ValueError, match="confirmed"):
        service.request(
            job.id, "rejected", [{"candidate_id": root.candidate_id, "revision": 1}], "analyst"
        )


def test_rejection_rolls_back_with_snapshot_if_audit_write_fails(db, job, monkeypatch):
    store = CandidateStore(db)
    saved = store.persist_validated(job.id, document_values(), actor="extractor")
    service = FactCommitService(db, Writer())
    commit = service.request(
        job.id, "first", [{"candidate_id": c.candidate_id, "revision": 1} for c in saved], "analyst"
    )
    assert service.apply(commit.id).status == "succeeded"
    previous = service.published_snapshot(job.id)

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.services.extraction.candidate_store.audit.append", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        store.review(
            saved[0].candidate_id,
            1,
            "rejected",
            "有异议",
            "analyst",
            expected_review_status="confirmed",
        )
    assert store.get(saved[0].candidate_id).review_status == "confirmed"
    assert store.get(saved[2].candidate_id).revision == 1
    assert service.published_snapshot(job.id) == previous


@pytest.mark.parametrize("include_expected_status", [True, False])
def test_api_rejection_requires_reason_role_and_current_status(
    client, analyst_headers, operator_headers, db, job, include_expected_status
):
    saved = CandidateStore(db).persist_validated(job.id, document_values(), actor="extractor")
    path = f"/api/extraction/evidence/candidates/{saved[0].candidate_id}/review"
    body = {
        "expected_revision": 1,
        "expected_review_status": "confirmed",
        "decision": "rejected",
        "reason": "识别类型有误",
    }
    if not include_expected_status:
        body.pop("expected_review_status")
    assert client.put(path, json=body, headers=operator_headers).status_code == 403
    assert (
        client.put(path, json={**body, "reason": " "}, headers=analyst_headers).status_code == 422
    )
    response = client.put(path, json=body, headers=analyst_headers)
    assert response.status_code == 200, response.text
    assert response.json()["review_reason"] == body["reason"]
    assert response.json()["review_source"] == "manual"
    assert client.put(path, json=body, headers=analyst_headers).status_code == 409
    response = client.get(f"/api/extraction/jobs/{job.id}/evidence", headers=analyst_headers)
    assert response.status_code == 200
    assert response.json()["candidates"][0]["review_status"] == "rejected"


def test_existing_results_migration_is_idempotent_and_preserves_manual_decisions(db, job):
    store = CandidateStore(db)
    saved = store.persist_validated(job.id, document_values(), actor="extractor")
    # Simulate pre-policy rows and their original audit history.
    db.execute(delete(EvidenceReview))
    for candidate in saved:
        row = db.get(EvidenceCandidateRecord, candidate.candidate_id)
        row.review_status = "pending"
        row.payload = {
            **row.payload,
            "review_status": "pending",
            "review_source": None,
            "review_reason": "",
        }
    db.commit()
    store.review(saved[1].candidate_id, 1, "rejected", "保留已有异议", "analyst")
    path = Path(__file__).parents[2] / "alembic/versions/0028_automatic_evidence_review.py"
    spec = importlib.util.spec_from_file_location("automatic_review_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(db.connection()))
    module.upgrade()
    module.upgrade()
    db.expire_all()
    assert store.get(saved[0].candidate_id).review_status == "confirmed"
    assert store.get(saved[2].candidate_id).review_source == "automatic"
    assert store.get(saved[1].candidate_id).review_reason == "保留已有异议"
    assert store.get(saved[3].candidate_id).review_status == "pending"
    assert len(db.scalars(select(EvidenceReview)).all()) == 3
    assert db.get(EvidenceJobState, job.id).snapshot_id is None
