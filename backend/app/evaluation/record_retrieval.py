"""Source-record retrieval with separate ranking, authorization, and coverage.

``RecordIndex(ir, schema).plan(subject, predicate, kind="property")`` ranks all
records; no lexical match is required to retain a record. A planned record exposes
``retrieved_target_ranges`` for retrieval and ``target_ranges`` for extraction.
Only the latter have an accepted subject scope. Names and summaries cannot grant
that acceptance. ``record_context`` exports atomic original-source fragments for
the citation layer, including explicit table and parent-container roles.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field

from app.evaluation.hierarchy_variant import _score, build_plan
from app.schemas.evidence import (
    CandidateRef,
    EvidenceModel,
    EvidenceRange,
    EvidenceScope,
    ScopeExpansion,
)
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.evidence_scope import (
    build_scope,
    candidate_ref,
    document_anchors,
    scope_intervals,
)
from app.services.extraction.table_records import table_records

POLICY_VERSION = "evaluation-record-retrieval-v1"


class SourceRecord(EvidenceModel):
    record_id: str
    kind: Literal["table_row", "paragraph", "heading"]
    section_node_id: str
    source_ranges: list[EvidenceRange]
    binding_ranges: list[EvidenceRange] = Field(default_factory=list)
    heading_ranges: list[EvidenceRange] = Field(default_factory=list)
    note_ranges: list[EvidenceRange] = Field(default_factory=list)
    parent_table_ranges: list[EvidenceRange] = Field(default_factory=list)
    table_path: list[str] | None = None
    row_index: int | None = None
    cell_memberships: list[dict[str, Any]] = Field(default_factory=list)
    parent_links: list[dict[str, Any]] = Field(default_factory=list)


class PlannedRecord(SourceRecord):
    retrieved_target_ranges: list[EvidenceRange] = Field(default_factory=list)
    target_ranges: list[EvidenceRange] = Field(default_factory=list)
    score: float = 0
    score_components: dict[str, float] = Field(default_factory=dict)
    matched: bool = False
    selected: bool = True
    authorization: Literal[
        "document_root",
        "accepted_scope",
        "verified_identity",
        "needs_identity_verification",
        "needs_subject_binding",
    ]
    partially_authorized: bool = False
    identity_dependency_refs: list[CandidateRef] = Field(default_factory=list)


class RecordRetrievalPlan(EvidenceModel):
    plan_id: str
    policy_version: str = POLICY_VERSION
    analysis_id: str
    ontology_release: str
    subject: CandidateRef
    predicate_iri: str
    task_kind: Literal["property", "relationship"]
    direct_range_class_iris: list[str] = Field(default_factory=list)
    authorized_scope: EvidenceScope
    records: list[PlannedRecord]
    ledger: dict[str, dict[str, Any]]
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)

    def mark(self, record_id, status, *, task_ids=(), candidate_ids=(), reason=None):
        """Record an actual outcome; a rank or empty response is not coverage."""
        if record_id not in self.ledger:
            raise ValueError("unknown retrieval record")
        if status not in {"searched", "no_evidence", "incomplete", "failed", "unsearched"}:
            raise ValueError("unknown retrieval outcome")
        entry = self.ledger[record_id]
        if not entry["selected"] and status in {"searched", "no_evidence"}:
            raise ValueError("unselected record cannot be marked searched")
        entry.update(status=status, task_ids=list(task_ids), candidate_ids=list(candidate_ids))
        if reason is not None:
            entry["reason"] = reason

    @property
    def coverage(self):
        values = list(self.ledger.values())
        return {
            "total_records": len(values),
            "selected_records": sum(item["selected"] for item in values),
            "unmatched_records": sum(not item["matched"] for item in values),
            "unsearched_records": sum(item["status"] == "unsearched" for item in values),
            "unresolved_subject_records": sum(
                item["authorization"].startswith("needs_") for item in values
            ),
            "complete": all(item["status"] in {"searched", "no_evidence"} for item in values),
        }


class RecordIndex:
    def __init__(self, ir: DocumentIR, schema: dict, metadata=None):
        # Detach mutable inputs so a plan cannot survive source or ontology edits.
        self.ir = DocumentIR.model_validate(ir.model_dump(mode="json"))
        self.schema = deepcopy(schema)
        self.ontology_release = evidence_hash(self.schema)
        self.tables = table_records(self.ir)
        self.nodes = {node["node_id"]: node for node in self.ir.nodes}
        self.positions = {unit.evidence_id: i for i, unit in enumerate(self.ir.evidence_units)}
        self.cues = build_plan({}, self.ir, metadata=metadata or {}, mode="structure_summary")
        self.records = self._build_records()
        self.by_id = {record.record_id: record for record in self.records}

    def _ranges(self, units):
        return [
            EvidenceRange(evidence_id=unit.evidence_id, start=0, end=len(unit.text))
            for unit in units
            if unit.text
        ]

    def _unique_ranges(self, ranges):
        unique = {(r.evidence_id, r.start, r.end): r for r in ranges}
        return sorted(unique.values(), key=lambda r: (self.positions[r.evidence_id], r.start or 0))

    def _heading_ranges(self, node_id, *, exclude_ids=()):
        chain, seen = set(), set()
        while node_id in self.nodes and node_id not in seen:
            seen.add(node_id)
            chain.add(node_id)
            node_id = self.nodes[node_id].get("parent_id")
        return self._ranges(
            unit
            for unit in self.ir.evidence_units
            if unit.kind == "heading"
            and unit.section_node_id in chain
            and unit.evidence_id not in exclude_ids
        )

    def _build_records(self):
        records = []
        row_records = {}
        for path, table in self.tables.tables.items():
            for row in range(table["header_row_count"], len(table["grid"])):
                row_units = self.tables.row_units(path, row)
                units = [
                    unit
                    for unit in row_units
                    if unit.text and not self.tables.is_header(unit)
                ]
                if not units:
                    continue
                headers = [
                    unit for unit in self.tables.metadata(row_units) if self.tables.is_header(unit)
                ]
                identity = {
                    "analysis": self.ir.analysis_id,
                    "kind": "table_row",
                    "table_path": path,
                    "row_index": row,
                }
                record = SourceRecord(
                    record_id=stable_id("record", identity),
                    kind="table_row",
                    section_node_id=units[0].section_node_id,
                    source_ranges=self._ranges(units),
                    binding_ranges=self._ranges(headers),
                    heading_ranges=self._heading_ranges(units[0].section_node_id),
                    note_ranges=self._ranges(self.tables.notes(units)),
                    table_path=list(path),
                    row_index=row,
                    cell_memberships=[
                        {
                            "evidence_id": unit.evidence_id,
                            "source_cell_id": unit.source_cell_id,
                            "data_row_indices": sorted(self.tables.rows(unit)),
                            "column_indices": sorted(self.tables.columns(unit)),
                            "shared_across_rows": len(self.tables.rows(unit)) > 1,
                        }
                        for unit in units
                    ],
                )
                records.append(record)
                row_records[(path, row)] = record
        # Actual parser parent-cell links distinguish containment from a column
        # header. Parent data never become the nested row's extraction targets.
        containers = {}
        for path, table in self.tables.tables.items():
            for cell in table["source_cells"]:
                for block in cell["blocks"]:
                    if block["kind"] == "table":
                        containers[tuple(block["table"]["table_path"])] = (path, cell)
        for record in records:
            path, seen = tuple(record.table_path), set()
            while path in containers and path not in seen:
                seen.add(path)
                parent_path, cell = containers[path]
                parent_rows = self.tables._rows.get(cell["cell_id"], set()) - set(
                    range(self.tables.tables[parent_path]["header_row_count"])
                )
                for row in sorted(parent_rows):
                    parent = row_records.get((parent_path, row))
                    if parent is None:
                        continue
                    record.parent_links.append(
                        {
                            "parent_record_id": parent.record_id,
                            "table_path": list(parent_path),
                            "row_index": row,
                            "container_cell_id": cell["cell_id"],
                            "relationship": "physical_containment_only",
                        }
                    )
                    record.parent_table_ranges.extend(
                        [*parent.source_ranges, *parent.binding_ranges]
                    )
                path = parent_path
            record.parent_table_ranges = self._unique_ranges(record.parent_table_ranges)
        paragraphs = defaultdict(list)
        for unit in self.ir.evidence_units:
            if not unit.table_path and unit.text:
                paragraphs[(unit.section_node_id, unit.paragraph_index, unit.kind)].append(unit)
        for (node_id, paragraph_index, kind), units in paragraphs.items():
            records.append(
                SourceRecord(
                    record_id=stable_id(
                        "record", [self.ir.analysis_id, kind, node_id, paragraph_index]
                    ),
                    kind=kind,
                    section_node_id=node_id,
                    source_ranges=self._ranges(units),
                    heading_ranges=self._heading_ranges(
                        node_id, exclude_ids={unit.evidence_id for unit in units}
                    ),
                )
            )
        records.sort(
            key=lambda record: min(self.positions[r.evidence_id] for r in record.source_ranges)
        )
        return records

    def _verified_identity(self, candidate):
        verdict = candidate.type_verification
        identity = candidate.identity
        if (
            not candidate.positive_eligible
            or candidate.review_status == "rejected"
            or verdict is None
            or not verdict.supported
            or not verdict.identity_supported
            or not identity.get("instance_iri")
            or not identity.get("key_value")
        ):
            return None
        keys = {
            p["iri"]
            for p in self.schema.get(candidate.class_iri, {}).get("properties", [])
            if p.get("identity_key") is True
        }
        if identity.get("key_predicate") not in keys:
            return None
        return (
            candidate.class_iri,
            identity["instance_iri"],
            identity["key_predicate"],
            evidence_hash(identity["key_value"]),
        )

    def _scope(self, subject, candidates, bound_relationships):
        if not subject.positive_eligible or subject.review_status == "rejected":
            raise ValueError("record retrieval requires a validated affirmative subject")
        for candidate in [subject, *candidates]:
            for anchor in document_anchors(candidate):
                self.ir.resolve(anchor)
        identity = self._verified_identity(subject)
        aliases = [
            candidate
            for candidate in candidates
            if candidate.candidate_id != subject.candidate_id
            and identity is not None
            and self._verified_identity(candidate) == identity
        ]
        related = [
            candidate
            for candidate in candidates
            if candidate.kind == "entity"
            and candidate.positive_eligible
            and candidate.candidate_id != subject.candidate_id
            and (
                candidate.class_iri == subject.class_iri
                or candidate.class_iri in self.schema.get(subject.class_iri, {}).get("parents", [])
                or subject.class_iri in self.schema.get(candidate.class_iri, {}).get("parents", [])
            )
        ]

        def competitors(owner):
            # Disable build_scope's permissive same-IRI alias shortcut. Only the
            # strictly verified aliases below may contribute new source ranges.
            return [
                candidate.model_copy(
                    update={
                        "identity": {
                            key: value
                            for key, value in candidate.identity.items()
                            if key != "instance_iri"
                        }
                    }
                )
                for candidate in [subject, *related]
                if candidate.candidate_id != owner.candidate_id
                and candidate.candidate_id not in {alias.candidate_id for alias in aliases}
            ]

        base = build_scope(
            self.ir, subject, competitors(subject), bound_relationships=bound_relationships
        )
        payload = base.model_dump(mode="json", exclude={"scope_id"})
        for alias in aliases:
            alias_scope = build_scope(
                self.ir, alias, competitors(alias), bound_relationships=bound_relationships
            )
            payload["ranges"].extend(r.model_dump(mode="json") for r in alias_scope.ranges)
            payload["construction_evidence"].extend(
                a.model_dump(mode="json") for a in alias_scope.construction_evidence
            )
            payload["reference_ranges"].extend(
                r.model_dump(mode="json") for r in alias_scope.reference_ranges
            )
            payload["record_ids"].append(f"verified-identity:{alias.candidate_id}:{alias.revision}")
        payload["policy_version"] = POLICY_VERSION
        authorized = EvidenceScope(scope_id=stable_id("record-scope", payload), **payload)
        return authorized, base, aliases

    def _intersect(self, ranges, intervals):
        result = []
        for region in ranges:
            for left, right in intervals.get(region.evidence_id, []):
                start, end = max(left, region.start), min(right, region.end)
                if start < end:
                    result.append(
                        EvidenceRange(evidence_id=region.evidence_id, start=start, end=end)
                    )
        return self._unique_ranges(result)

    def plan(
        self, subject, predicate, *, kind, candidates=(), bound_relationships=(), max_records=None
    ):
        if kind not in {"property", "relationship"}:
            raise ValueError("record retrieval requires property or relationship")
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be positive or None")
        field = "properties" if kind == "property" else "relationships"
        canonical = next(
            (
                p
                for p in self.schema.get(subject.class_iri, {}).get(field, [])
                if p["iri"] == predicate.get("iri")
            ),
            None,
        )
        if canonical is None:
            raise ValueError("predicate is not declared for this subject class")
        predicate = deepcopy(canonical)
        candidates = list(candidates)
        scope, original_scope, aliases = self._scope(subject, candidates, list(bound_relationships))
        intervals = scope_intervals(scope, self.ir)
        original_intervals = scope_intervals(original_scope, self.ir)
        declared_ranges = set(predicate.get("range", [])) if kind == "relationship" else set()
        range_classes = sorted(
            iri
            for iri, definition in self.schema.items()
            if iri in declared_ranges or declared_ranges.intersection(definition.get("parents", []))
        )
        definitions = [predicate, *(self.schema[iri] for iri in range_classes)]
        planned = []
        subject_names = {c.text.strip() for c in [subject, *aliases] if c.text.strip()}
        for record in self.records:
            source_text = "\n".join(self.ir.unit(r.evidence_id).text for r in record.source_ranges)
            binding_text = "\n".join(
                self.ir.unit(r.evidence_id).text for r in record.binding_ranges
            )
            source_score = max(
                (_score(source_text + "\n" + binding_text, d) for d in definitions), default=0
            )
            cues = self.cues["nodes"].get(record.section_node_id, {}).get("cues", [])
            heading_score = sum(
                (0.5 ** cue["distance"])
                * max((_score(cue["heading"], d) for d in definitions), default=0)
                for cue in cues
            )
            summary_score = sum(
                (0.5 ** cue["distance"])
                * max((_score(cue["summary"], d) for d in definitions), default=0)
                for cue in cues
            )
            names_match = any(name in source_text for name in subject_names)
            targets = self._intersect(record.source_ranges, intervals)
            original_targets = self._intersect(record.source_ranges, original_intervals)
            if targets:
                authorization = (
                    "document_root"
                    if subject.identity.get("document_root") == self.ir.document_hash
                    else ("verified_identity" if targets != original_targets else "accepted_scope")
                )
            else:
                authorization = (
                    "needs_identity_verification" if names_match else "needs_subject_binding"
                )
            components = {
                "source_and_column_headers": source_score,
                "headings": heading_score,
                "summaries": summary_score,
                "subject_name": float(names_match),
                "authorized_source": float(bool(targets)),
            }
            planned.append(
                PlannedRecord(
                    **record.model_dump(),
                    retrieved_target_ranges=record.source_ranges,
                    target_ranges=targets,
                    authorization=authorization,
                    partially_authorized=bool(targets) and targets != record.source_ranges,
                    identity_dependency_refs=[candidate_ref(alias) for alias in aliases]
                    if authorization == "verified_identity"
                    else [],
                    score=round(
                        4 * source_score
                        + 2 * heading_score
                        + summary_score
                        + 2 * names_match
                        + 8 * bool(targets),
                        6,
                    ),
                    score_components=components,
                    matched=bool(source_score + heading_score + summary_score),
                )
            )
        planned.sort(key=lambda record: -record.score)
        for index, record in enumerate(planned):
            record.selected = max_records is None or index < max_records
        ledger = {
            record.record_id: {
                "status": "unsearched",
                "selected": record.selected,
                "matched": record.matched,
                "authorization": record.authorization,
                "partially_authorized": record.partially_authorized,
                "task_ids": [],
                "candidate_ids": [],
                "reason": "rank_limit" if not record.selected else None,
            }
            for record in planned
        }
        identity = {
            "policy_version": POLICY_VERSION,
            "analysis": self.ir.analysis_id,
            "ontology": self.ontology_release,
            "subject": candidate_ref(subject),
            "predicate": predicate,
            "kind": kind,
            "records": planned,
            "metadata_dependency": self.cues["dependency_hash"],
            "scope": scope,
        }
        return RecordRetrievalPlan(
            plan_id=stable_id("record-retrieval-plan", identity),
            analysis_id=self.ir.analysis_id,
            ontology_release=self.ontology_release,
            subject=candidate_ref(subject),
            predicate_iri=predicate["iri"],
            task_kind=kind,
            direct_range_class_iris=range_classes,
            authorized_scope=scope,
            records=planned,
            ledger=ledger,
            diagnostics=deepcopy(self.cues["diagnostics"]),
        )

    def reference_scope(self, plan: RecordRetrievalPlan, record_id: str):
        """Propose retrieval whose every fact requires production verify_reference.

        This does not authorize ownership or alter the plan's accepted scope.
        Tasks must target only this record's ``retrieved_target_ranges``. All of
        those ranges are deliberately reference ranges, including overlap with an
        old scope, so no proposed assertion can bypass independent coreference.
        """
        if (
            plan.analysis_id != self.ir.analysis_id
            or plan.ontology_release != self.ontology_release
        ):
            raise ValueError("retrieval plan dependencies do not match index")
        if record_id not in plan.ledger or record_id not in self.by_id:
            raise ValueError("record does not belong to retrieval plan")
        record = self.by_id[record_id]
        scope = plan.authorized_scope
        if scope.document_hash != self.ir.document_hash or scope.subject != plan.subject:
            raise ValueError("retrieval scope belongs to a different subject or document")
        if not scope.construction_evidence:
            raise ValueError("reference retrieval requires existing subject identity evidence")
        for region in record.source_ranges:
            for excluded in scope.excluded_ranges:
                if region.evidence_id == excluded.evidence_id and max(
                    region.start, excluded.start or 0
                ) < min(region.end, excluded.end if excluded.end is not None else region.end):
                    raise ValueError("record intersects explicitly excluded source")
        expansion = ScopeExpansion(
            reason=(
                "Unverified record retrieval; every assertion requires independent verify_reference"
            ),
            added_ranges=record.source_ranges,
            evidence=scope.construction_evidence,
            policy_version=POLICY_VERSION + ":reference-required",
        )
        payload = scope.model_dump(mode="json", exclude={"scope_id"})
        payload.update(
            revision=scope.revision + 1,
            parent_scope_id=scope.scope_id,
            ranges=[*payload["ranges"], *(r.model_dump(mode="json") for r in record.source_ranges)],
            reference_ranges=[
                *payload["reference_ranges"],
                *(r.model_dump(mode="json") for r in record.source_ranges),
            ],
            record_ids=[*scope.record_ids, f"reference-required:{record_id}"],
            expansion_history=[*payload["expansion_history"], expansion.model_dump(mode="json")],
            policy_version=POLICY_VERSION + ":reference-required",
        )
        proposed = EvidenceScope(scope_id=stable_id("record-reference-scope", payload), **payload)
        plan.ledger[record_id].update(
            reference_verification_required=True,
            provisional_scope_id=proposed.scope_id,
        )
        return proposed

    def reference_context(self, plan: RecordRetrievalPlan, record_id: str):
        """Export record proposals plus subject identity, preserving atomic quotes."""
        scope = self.reference_scope(plan, record_id)
        planned = next(record for record in plan.records if record.record_id == record_id)
        context = self.record_context(
            planned.model_copy(
                update={
                    "target_ranges": self.by_id[record_id].source_ranges,
                }
            )
        )
        registered = {evidence_hash(anchor) for anchor in context["allowed_binding_regions"]}
        for anchor in scope.construction_evidence:
            self.ir.resolve(anchor)
            if evidence_hash(anchor) not in registered:
                context["allowed_binding_regions"].append(anchor)
                context["fragments"].append(
                    {
                        "anchor": anchor.model_dump(mode="json"),
                        "text": self.ir.resolve(anchor),
                        "purpose": "scope_construction_evidence",
                        "fact_eligible": False,
                        "binding_permitted": True,
                        "record_role": "verified_subject_identity",
                    }
                )
                registered.add(evidence_hash(anchor))
        context.update(
            scope=scope,
            reference_verification_required=True,
            mandatory_verification_stages=["verify_binding", "verify_reference"],
        )
        return context

    def record_context(self, planned: PlannedRecord):
        """Original atomic quotes plus scope/record roles for a ContextEnvelope.

        Headers, table notes, and physical parent rows are usable binding context,
        never newly authorized values. Their source table coordinates stay intact;
        an outer header cannot serve as an inner table's unit/column declaration.
        """
        if planned.record_id not in self.by_id:
            raise ValueError("record belongs to a different source index")
        record = self.by_id[planned.record_id]
        targets = self._unique_ranges(planned.target_ranges)
        if any(
            not any(
                r.evidence_id == source.evidence_id
                and source.start <= r.start < r.end <= source.end
                for source in record.source_ranges
            )
            for r in targets
        ):
            raise ValueError("record target extends beyond its original source record")
        allowed_facts = [self.ir.anchor(r.evidence_id, r.start, r.end) for r in targets]
        fragments = [
            {
                "anchor": anchor.model_dump(mode="json"),
                "text": self.ir.resolve(anchor),
                "purpose": "target",
                "fact_eligible": True,
                "binding_permitted": True,
                "record_id": record.record_id,
            }
            for anchor in allowed_facts
        ]
        binding = {evidence_hash(anchor): anchor for anchor in allowed_facts}
        target_keys = {(r.evidence_id, r.start, r.end) for r in targets}
        groups = [
            (record.source_ranges, "retrieval_candidate", "unverified_record_data", False),
            (record.binding_ranges, "table_record_metadata", "column_header", True),
            (record.heading_ranges, "ancestor_heading", "section_heading", True),
            (record.note_ranges, "table_note", "table_note", True),
            (record.parent_table_ranges, "parent_table_context", "physical_container", True),
        ]
        for ranges, purpose, role, permitted in groups:
            for region in ranges:
                if (region.evidence_id, region.start, region.end) in target_keys:
                    continue
                anchor = self.ir.anchor(region.evidence_id, region.start, region.end)
                fragments.append(
                    {
                        "anchor": anchor.model_dump(mode="json"),
                        "text": self.ir.resolve(anchor),
                        "purpose": purpose,
                        "fact_eligible": False,
                        "binding_permitted": permitted,
                        "record_role": role,
                        "record_id": record.record_id,
                    }
                )
                if permitted:
                    binding[evidence_hash(anchor)] = anchor
        return {
            "record_id": record.record_id,
            "fragments": fragments,
            "allowed_fact_regions": allowed_facts,
            "allowed_binding_regions": list(binding.values()),
            "table_record": {
                "table_path": record.table_path,
                "row_index": record.row_index,
                "cell_memberships": record.cell_memberships,
            },
            "parent_links": record.parent_links,
            "authorization": planned.authorization,
        }
