import uuid

import owlready2
import pytest
from rdflib import OWL, RDF, RDFS, XSD, URIRef

from app.models.extraction import ExtractionJob
from app.schemas.evidence import (
    BindingEvidence,
    Candidate,
    CandidateRef,
    LiteralValue,
    ManualProvenance,
)
from app.services.extraction.candidate_store import CandidateConflict, CandidateStore
from app.services.fact_commit import FactCommitService
from app.services.ontology_instance_writer import EvidenceInstanceWriter


@pytest.fixture
def evidence_job(db):
    job = ExtractionJob(id=uuid.uuid4(), source_type="word", status="completed")
    db.add(job)
    db.commit()
    return job


def manual(value):
    return ManualProvenance(actor="analyst", review_id="entry", value=value, reason="人工录入")


def entity(identity, iri="urn:test:Drug"):
    return Candidate(
        candidate_id=identity,
        kind="entity",
        class_iri=iri,
        text=identity,
        provenance=[manual(identity)],
        validation_status="passed",
    )


def assertions():
    a, b = entity("A"), entity("E", "urn:test:Equipment")
    prop = Candidate(
        candidate_id="dose",
        kind="property",
        subject=CandidateRef(candidate_id="A", revision=1),
        predicate_iri="urn:test:strength",
        literal=LiteralValue(
            kind="number",
            raw_value="0.1234567890123456789",
            normalized_value="0.1234567890123456789",
            datatype_iri="http://www.w3.org/2001/XMLSchema#decimal",
        ),
        provenance=[manual("0.1234567890123456789")],
        bindings=[
            BindingEvidence(
                method="manual_decision",
                subject_candidate_id="A",
                predicate_iri="urn:test:strength",
                provenance_indexes=[0],
            )
        ],
        validation_status="passed",
    )
    negative = Candidate(
        candidate_id="not-uses",
        kind="relationship",
        subject=CandidateRef(candidate_id="A", revision=1),
        object=CandidateRef(candidate_id="E", revision=1),
        predicate_iri="urn:test:uses",
        assertion_status="negated",
        provenance=[manual("A 不使用 E")],
        bindings=[
            BindingEvidence(
                method="manual_decision",
                subject_candidate_id="A",
                object_candidate_id="E",
                predicate_iri="urn:test:uses",
                provenance_indexes=[0],
            )
        ],
        validation_status="passed",
    )
    return [a, b, prop, negative]


@pytest.fixture
def real_world():
    world = owlready2.World()
    onto = world.get_ontology("urn:test:")
    with onto:
        # Exact RDF IRIs avoid Owlready2's Python-class namespace conventions.
        graph = world.as_rdflib_graph()
        drug, equipment, strength, uses = [
            URIRef(f"urn:test:{name}") for name in ("Drug", "Equipment", "strength", "uses")
        ]
        for cls in (drug, equipment):
            graph.add((cls, RDF.type, OWL.Class))
        graph.add((strength, RDF.type, OWL.DatatypeProperty))
        graph.add((strength, RDFS.domain, drug))
        graph.add((strength, RDFS.range, XSD.decimal))
        graph.add((uses, RDF.type, OWL.ObjectProperty))
        graph.add((uses, RDFS.domain, drug))
        graph.add((uses, RDFS.range, equipment))
    yield world
    world.close()


def prepare(db, job):
    store = CandidateStore(db)
    candidates = store.persist_validated(job.id, assertions(), actor="extractor")
    return [
        store.review(c.candidate_id, c.revision, "confirmed", "已核对", "analyst")
        for c in candidates
    ]


def test_review_is_cas_and_never_implicitly_commits(db, evidence_job):
    store = CandidateStore(db)
    candidate = store.persist_validated(evidence_job.id, [entity("A")], actor="extractor")[0]
    reviewed = store.review(candidate.candidate_id, 1, "confirmed", "核对", "analyst")
    assert reviewed.review_status == "confirmed"
    assert reviewed.commit_status == "not_requested"
    with pytest.raises(CandidateConflict):
        store.review(
            candidate.candidate_id,
            1,
            "rejected",
            "过期并发请求",
            "another",
            expected_review_status="pending",
        )


def test_api_commit_requires_real_writer_and_replays_retained_provenance(
    client,
    db,
    evidence_job,
    real_world,
    tmp_path,
    monkeypatch,
    analyst_headers,
):
    from app.config import settings
    from app.dependencies import get_ontology_engine
    from app.main import app

    candidates = prepare(db, evidence_job)
    monkeypatch.setattr(settings, "evidence_world_dir", tmp_path / "api-worlds")
    path = f"/api/extraction/jobs/{evidence_job.id}/evidence"
    body = {
        "idempotency_key": "api",
        "items": [{"candidate_id": c.candidate_id, "revision": 1} for c in candidates],
    }
    failed = client.post(path + "/commits", json=body, headers=analyst_headers)
    assert failed.status_code == 200 and failed.json()["status"] == "failed"
    assert not client.get(path + "/snapshot", headers=analyst_headers).json()["published"]
    app.dependency_overrides[get_ontology_engine] = lambda: real_world
    commit = client.post(
        f"/api/extraction/evidence/commits/{failed.json()['commit_id']}/retry",
        headers=analyst_headers,
    )
    assert commit.status_code == 200 and commit.json()["status"] == "succeeded", commit.text
    snapshot = client.get(path + "/snapshot", headers=analyst_headers).json()
    assert snapshot["published"]
    for assertion in snapshot["assertions"]:
        result = client.get(
            f"/api/extraction/evidence/assertions/{assertion['assertion_id']}/provenance",
            headers=analyst_headers,
        )
        assert result.status_code == 200
        assert result.json()["provenance"] == assertion["candidate"]["provenance"]


def test_real_world_commit_is_idempotent_and_negative_is_persisted_not_projected(
    db,
    evidence_job,
    real_world,
    tmp_path,
):
    candidates = prepare(db, evidence_job)
    writer = EvidenceInstanceWriter(real_world, tmp_path)
    service = FactCommitService(db, writer)
    items = [{"candidate_id": c.candidate_id, "revision": c.revision} for c in candidates]
    first = service.request(evidence_job.id, "batch-1", items, "analyst")
    assert first.status == "queued"
    assert service.published_snapshot(evidence_job.id) is None
    commit = service.apply(first.id)
    assert commit.status == "succeeded", commit.error
    assert service.request(evidence_job.id, "batch-1", items, "analyst").id == commit.id
    snapshot = service.published_snapshot(evidence_job.id)
    assert len(snapshot["assertions"]) == 4
    negative = next(
        a for a in snapshot["assertions"] if a["candidate"]["assertion_status"] == "negated"
    )
    reopened = owlready2.World(filename=commit.world_path)
    graph = reopened.as_rdflib_graph()
    assert (
        URIRef(negative["subject_iri"]),
        URIRef("urn:test:uses"),
        URIRef(negative["object_iri"]),
    ) not in graph
    assert (URIRef(negative["assertion_iri"]), RDF.type, URIRef("urn:evidence:Assertion")) in graph
    strength = next(a for a in snapshot["assertions"] if a["candidate"]["kind"] == "property")
    values = list(graph.objects(URIRef(strength["subject_iri"]), URIRef("urn:test:strength")))
    assert str(values[0]) == "0.1234567890123456789"
    reopened.close()
    assert not list(real_world.as_rdflib_graph().subjects(RDF.type, URIRef("urn:test:Drug")))


def test_failed_graph_write_does_not_publish_and_retry_uses_same_manifest(
    db,
    evidence_job,
    real_world,
    tmp_path,
):
    candidates = prepare(db, evidence_job)

    class FailingWriter:
        def write(self, *args):
            raise RuntimeError("disk unavailable")

    service = FactCommitService(db, FailingWriter())
    requested = service.request(
        evidence_job.id,
        "retry",
        [{"candidate_id": c.candidate_id, "revision": c.revision} for c in candidates],
        "analyst",
    )
    failed = service.apply(requested.id)
    assert failed.status == "failed"
    assert service.published_snapshot(evidence_job.id) is None
    restarted = FactCommitService(db, EvidenceInstanceWriter(real_world, tmp_path))
    assert restarted.apply(requested.id).status == "succeeded"
    assert restarted.published_snapshot(evidence_job.id)["snapshot_id"]


def test_different_batches_inherit_previously_published_facts(
    db, evidence_job, real_world, tmp_path
):
    store = CandidateStore(db)
    service = FactCommitService(db, EvidenceInstanceWriter(real_world, tmp_path))
    for name in ("A", "B"):
        candidate = store.persist_validated(evidence_job.id, [entity(name)], actor="extractor")[0]
        candidate = store.review(candidate.candidate_id, 1, "confirmed", "核对", "analyst")
        commit = service.request(
            evidence_job.id,
            name,
            [{"candidate_id": candidate.candidate_id, "revision": 1}],
            "analyst",
        )
        assert service.apply(commit.id).status == "succeeded"
    snapshot = service.published_snapshot(evidence_job.id)
    assert {a["candidate"]["text"] for a in snapshot["assertions"]} == {"A", "B"}


def test_conditional_assertion_commits_without_positive_projection(
    db, evidence_job, real_world, tmp_path
):
    values = assertions()
    payload = values[-1].model_dump(mode="json")
    payload.update(assertion_status="conditional", condition_provenance_indexes=[0])
    values[-1] = Candidate.model_validate(payload)
    store = CandidateStore(db)
    candidates = store.persist_validated(evidence_job.id, values, actor="extractor")
    for candidate in candidates:
        store.review(candidate.candidate_id, 1, "confirmed", "已核对条件", "analyst")
    service = FactCommitService(db, EvidenceInstanceWriter(real_world, tmp_path))
    commit = service.request(
        evidence_job.id,
        "condition",
        [
            {"candidate_id": candidates[-1].candidate_id, "revision": 1},
        ],
        "analyst",
    )
    assert service.apply(commit.id).status == "succeeded"
    records = service.published_snapshot(evidence_job.id)["assertions"]
    condition = next(a for a in records if a["candidate"]["assertion_status"] == "conditional")
    assert not condition["positive_eligible"]
    assert condition["candidate"]["condition_provenance_indexes"] == [0]
    reopened = owlready2.World(filename=service.apply(commit.id).world_path)
    assert (
        URIRef(condition["subject_iri"]),
        URIRef("urn:test:uses"),
        URIRef(condition["object_iri"]),
    ) not in reopened.as_rdflib_graph()
    reopened.close()


def test_idempotency_content_conflict_and_stale_edit(db, evidence_job, real_world, tmp_path):
    candidates = prepare(db, evidence_job)
    service = FactCommitService(db, EvidenceInstanceWriter(real_world, tmp_path))
    items = [{"candidate_id": candidates[0].candidate_id, "revision": 1}]
    commit = service.request(evidence_job.id, "key", items, "analyst")
    with pytest.raises(CandidateConflict, match="different content"):
        service.request(
            evidence_job.id,
            "key",
            [{"candidate_id": candidates[1].candidate_id, "revision": 1}],
            "analyst",
        )
    store = CandidateStore(db)
    edited = store.review(
        candidates[0].candidate_id,
        1,
        "confirmed",
        "更正",
        "analyst",
        edited_payload={"text": "new"},
        validator=lambda c: c,
    )
    assert edited.revision == 2 and edited.review_status == "pending"
    assert store.get(candidates[2].candidate_id).validation_status == "pending"
    assert service.apply(commit.id).status == "failed"
    assert service.published_snapshot(evidence_job.id) is None


def test_entity_resolve_rewrites_and_invalidates_dependents(db, evidence_job):
    candidates = prepare(db, evidence_job)
    store = CandidateStore(db)
    canonical = store.persist_validated(evidence_job.id, [entity("canonical")], actor="extractor")[
        0
    ]
    store.review(canonical.candidate_id, 1, "confirmed", "主体核对", "analyst")
    resolved = store.resolve(
        candidates[0].candidate_id,
        1,
        canonical.candidate_id,
        1,
        "同一实体",
        "analyst",
        validator=lambda c: c,
    )
    assert resolved.identity["canonical_candidate_id"] == canonical.candidate_id
    dependent = store.get(candidates[2].candidate_id)
    assert dependent.subject.candidate_id == canonical.candidate_id
    assert dependent.bindings[0].subject_candidate_id == canonical.candidate_id
    assert dependent.revision == 2 and dependent.review_status == "pending"
    with pytest.raises((CandidateConflict, ValueError)):
        store.resolve(
            canonical.candidate_id,
            1,
            resolved.candidate_id,
            2,
            "循环归并",
            "analyst",
            validator=lambda c: c,
        )


def test_different_keys_concurrently_publish_shared_dependencies(real_world, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, RLock, local
    from types import SimpleNamespace

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.services import audit

    sql = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"timeout": 20})
    Base.metadata.create_all(sql)
    sessions = sessionmaker(sql, expire_on_commit=False)
    with sessions() as db:
        job = ExtractionJob(id=uuid.uuid4(), source_type="word", status="completed")
        db.add(job)
        db.commit()
        candidates = prepare(db, job)
        service = FactCommitService(db, None)
        ids = [
            service.request(
                job.id, f"key-{index}", [{"candidate_id": c.candidate_id, "revision": 1}], "analyst"
            ).id
            for index, c in enumerate(candidates[2:])
        ]
        job_id = job.id
    barrier = Barrier(2)
    head_barrier, local_state = Barrier(2), local()

    @event.listens_for(sql, "after_cursor_execute")
    def race_head(connection, cursor, statement, parameters, context, many):
        if "FROM evidence_job_states" in statement and not getattr(local_state, "read_head", False):
            local_state.read_head = True
            head_barrier.wait(timeout=10)

    source = SimpleNamespace(_world=real_world, _lock=RLock())

    class ConcurrentWriter(EvidenceInstanceWriter):
        def write(self, *args):
            barrier.wait(timeout=10)
            return super().write(*args)

    def apply(identity):
        with sessions() as db:
            result = FactCommitService(db, ConcurrentWriter(source, tmp_path / "worlds")).apply(
                identity
            )
            return result.status, result.error

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(apply, ids))
    assert all(status == "succeeded" for status, _ in results), results
    event.remove(sql, "after_cursor_execute", race_head)
    with sessions() as db:
        snapshot = FactCommitService(db, None).published_snapshot(job_id)
        assert len(snapshot["assertions"]) == 4
        assert audit.verify(db)["ok"]
    sql.dispose()
