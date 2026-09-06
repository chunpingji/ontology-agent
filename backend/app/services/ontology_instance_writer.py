"""Strict isolated ABox writer. Publication is owned by SQL snapshot CAS, not World.save()."""

import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

import owlready2
from rdflib import OWL, RDF, RDFS, BNode, Literal, Namespace, URIRef

from app.schemas.evidence import Candidate
from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id

EV = Namespace("urn:evidence:")


def instance_iri(candidate: Candidate) -> str:
    return candidate.identity.get("instance_iri") or f"urn:evidence:entity:{candidate.candidate_id}"


def assertion_record(candidate: Candidate, entities: dict[str, Candidate]) -> dict:
    identity = stable_id("assertion", [candidate.candidate_id, candidate.revision])
    subject = candidate if candidate.kind == "entity" else entities[candidate.subject.candidate_id]
    target = entities[candidate.object.candidate_id] if candidate.object else None
    return {
        "assertion_id": identity,
        "assertion_iri": f"urn:evidence:assertion:{identity}",
        "subject_iri": instance_iri(subject),
        "object_iri": instance_iri(target) if target else None,
        "candidate": candidate.model_dump(mode="json"),
        "positive_eligible": candidate.positive_eligible,
    }


class EvidenceInstanceWriter:
    def __init__(self, source, root: str | Path):
        self.source = source
        self.root = Path(root)

    def _schema_triples(self):
        source_world = (
            self.source if isinstance(self.source, owlready2.World) else self.source._world
        )
        if not isinstance(source_world, owlready2.World):
            raise TypeError("strict instance writer requires a real Owlready2 World")
        lock = getattr(self.source, "_lock", None)
        with lock if lock is not None else nullcontext():
            return list(source_world.as_rdflib_graph())

    @staticmethod
    def _class_matches(graph, class_iri, expected, seen=frozenset()):
        if expected in seen:
            return False
        if isinstance(expected, BNode):
            for predicate, combine in ((OWL.unionOf, any), (OWL.intersectionOf, all)):
                head = graph.value(expected, predicate)
                if head is not None:
                    members = list(graph.items(head))
                    return bool(members) and combine(
                        EvidenceInstanceWriter._class_matches(graph, class_iri, child, seen | {expected})
                        for child in members
                    )
            return False
        if expected == OWL.Thing:
            return True
        visited, pending = set(), [URIRef(class_iri)]
        while pending:
            current = pending.pop()
            if current == expected:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(
                    parent
                    for parent in graph.objects(current, RDFS.subClassOf)
                    if isinstance(parent, URIRef)
                )
        return False

    def _validate(self, graph, candidate, entities):
        if candidate.validation_status != "passed" or candidate.review_status != "confirmed":
            raise ValueError("unreviewed or unvalidated assertion in commit manifest")
        if candidate.kind == "entity":
            cls = URIRef(candidate.class_iri)
            if (cls, RDF.type, OWL.Class) not in graph and (cls, RDF.type, RDFS.Class) not in graph:
                raise ValueError(f"unknown entity class: {candidate.class_iri}")
            return
        subject = entities.get(candidate.subject.candidate_id)
        if (
            subject is None
            or subject.revision != candidate.subject.revision
            or not subject.positive_eligible
        ):
            raise ValueError("unresolved subject in immutable manifest")
        predicate = URIRef(candidate.predicate_iri)
        expected_kind = OWL.DatatypeProperty if candidate.kind == "property" else OWL.ObjectProperty
        if (predicate, RDF.type, expected_kind) not in graph:
            raise ValueError(f"unknown or wrong-kind predicate: {candidate.predicate_iri}")
        for domain in graph.objects(predicate, RDFS.domain):
            if not self._class_matches(graph, subject.class_iri, domain):
                raise ValueError("predicate domain does not match subject")
        if candidate.kind == "relationship":
            target = entities.get(candidate.object.candidate_id)
            if (
                target is None
                or target.revision != candidate.object.revision
                or not target.positive_eligible
            ):
                raise ValueError("unresolved object in immutable manifest")
            for target_class in graph.objects(predicate, RDFS.range):
                if not self._class_matches(graph, target.class_iri, target_class):
                    raise ValueError("predicate range does not match object")
        else:
            ranges = list(graph.objects(predicate, RDFS.range))
            if ranges and URIRef(candidate.literal.datatype_iri) not in ranges:
                raise ValueError("literal datatype does not match predicate range")

    @staticmethod
    def _projection(record):
        candidate = Candidate.model_validate(record["candidate"])
        if not candidate.positive_eligible:
            return None
        subject = URIRef(record["subject_iri"])
        if candidate.kind == "entity":
            return subject, RDF.type, URIRef(candidate.class_iri)
        if candidate.kind == "relationship":
            return subject, URIRef(candidate.predicate_iri), URIRef(record["object_iri"])
        value = candidate.literal
        if value.kind in {"range", "comparison"}:
            # Such values are expressions, not scalar decimal facts. They remain
            # queryable as typed assertion payloads; do not invent a midpoint.
            return None
        lexical = value.normalized_value
        if lexical is None:
            raise ValueError("literal has no normalized value")
        return (
            subject,
            URIRef(candidate.predicate_iri),
            Literal(
                lexical,
                datatype=URIRef(value.datatype_iri),
                normalize=False,
            ),
        )

    def write(self, commit_id: str, manifest: dict) -> str:
        if len(commit_id) != 64 or any(c not in "0123456789abcdef" for c in commit_id):
            raise ValueError("invalid commit identity")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{commit_id}.sqlite3"
        if destination.exists():
            self.readback(destination, manifest)
            return str(destination)
        candidates = [Candidate.model_validate(c) for c in manifest["candidates"]]
        entities = {c.candidate_id: c for c in candidates if c.kind == "entity"}
        triples = self._schema_triples()
        with tempfile.TemporaryDirectory(prefix=f"{commit_id[:12]}_", dir=self.root) as temp_dir:
            stage_path = Path(temp_dir) / "stage.sqlite3"
            world = owlready2.World(filename=str(stage_path))
            try:
                ontology = world.get_ontology(f"urn:evidence:commit:{commit_id}:")
                graph = world.as_rdflib_graph()
                with ontology:
                    for triple in triples:
                        graph.add(triple)
                    for candidate in candidates:
                        self._validate(graph, candidate, entities)
                    for record in manifest["assertions"]:
                        candidate = Candidate.model_validate(record["candidate"])
                        node = URIRef(record["assertion_iri"])
                        graph.add((node, RDF.type, EV.Assertion))
                        graph.add((node, EV.payload, Literal(canonical_json(record))))
                        graph.add((node, EV.polarity, Literal(candidate.assertion_status)))
                        graph.add((node, EV.subject, URIRef(record["subject_iri"])))
                        graph.add((node, EV.positiveEligible, Literal(candidate.positive_eligible)))
                        if record["object_iri"]:
                            graph.add((node, EV.object, URIRef(record["object_iri"])))
                        if candidate.predicate_iri:
                            graph.add((node, EV.predicate, URIRef(candidate.predicate_iri)))
                        projection = self._projection(record)
                        if projection is not None:
                            graph.add(projection)
                    graph.add(
                        (
                            URIRef(f"urn:evidence:commit:{commit_id}"),
                            EV.manifestHash,
                            Literal(evidence_hash(manifest)),
                        )
                    )
                world.save()
            finally:
                world.close()
            self.readback(stage_path, manifest)
            # The manifest is immutable and the lease gives one SQL publisher.
            # No staged graph is attached to the default public World.
            os.replace(stage_path, destination)
        return str(destination)

    def readback(self, path: str | Path, manifest: dict):
        world = owlready2.World(filename=str(path))
        try:
            graph = world.as_rdflib_graph()
            if Literal(evidence_hash(manifest)) not in set(graph.objects(None, EV.manifestHash)):
                raise ValueError("graph manifest readback mismatch")
            for record in manifest["assertions"]:
                node = URIRef(record["assertion_iri"])
                if (node, RDF.type, EV.Assertion) not in graph:
                    raise ValueError("assertion missing after World.save")
                if Literal(canonical_json(record)) not in set(graph.objects(node, EV.payload)):
                    raise ValueError("assertion provenance readback mismatch")
                projection = self._projection(record)
                if projection is not None and projection not in graph:
                    raise ValueError("positive fact missing after World.save")
            return True
        finally:
            world.close()
