"""Frozen ontology vocabulary, with an explicit legacy experimental compiler.

The overlay contains recall vocabulary, not ontology changes, observed values,
or validated facts. No report text or scoring reference enters this compiler.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal as RoleLiteral

from pydantic import Field, model_validator
from rdflib import RDFS, SKOS, Graph, Literal, Namespace, URIRef

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
from app.services.extraction.ontology_guided.ontology_lexical import SKOS_ALT_LABEL_IRI
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu

PROFILE = "cmc-extraction-vocabulary-v1"
VOCAB = Namespace("urn:ontology-agent:cmc-extraction-vocabulary:")
DEFAULT_OVERLAY = Path(__file__).resolve().parents[3] / "resources/cmc_extraction_vocabulary.ttl"
_ROLE_GROUP = {
    "entity_name": "entities", "record_anchor": "entities",
    "attribute_value": "values", "unit": "units",
}
_ROLE_INSTRUCTIONS = {
    "entity_name": "抽取有原文明示角色的实体名称或称谓；不抽类别表头，名称不证明唯一身份。",
    "record_anchor": "抽取单条信息记录或工艺步骤的名称、标题或明确标识；不以跨记录共享名称替代。",
    "attribute_value": "抽取对应字段的原文值，不抽字段标题；保留否定、范围、比较和完整限定。",
    "unit": "只抽原文实际出现的完整单位，包括分母和幂；不抽数值，不由目标单位补造源单位。",
}


def _literal_rows(graph: Graph, iri: str, predicate, *, kind: str, path: Path) -> list[dict]:
    literals = [value for value in graph.objects(URIRef(iri), predicate)
                if isinstance(value, Literal)]
    literals.sort(key=lambda value: (0 if value.language == "zh" else 1,
                                    value.language or "", str(value)))
    return [{"kind": kind, "predicate": str(predicate), "value": str(value),
             "language": value.language, "path": str(path)} for value in literals]


def _one_literal(graph: Graph, iri: str, predicate) -> str | None:
    values = list(graph.objects(URIRef(iri), predicate))
    if len(values) != 1 or not isinstance(values[0], Literal) or not str(values[0]).strip():
        return None
    return str(values[0])


def _load_local(path: Path) -> Graph:
    # Explicit Turtle data avoids URL resolution; owl:imports triples stay data.
    return Graph().parse(data=path.read_text(encoding="utf-8"), format="turtle")


def build_experimental_extraction_vocabulary(
    catalog: dict,
    class_iris: list[str],
    *,
    ontology_dir: str | Path,
    overlay_path: str | Path,
) -> dict:
    """Compile allowed class/property labels and documented unit vocabulary.

    Only direct ``skos:altLabel`` values of the same IRI are considered. If they
    are absent, explicitly curated overlay aliases supply the lexical hints.
    A missing manual extraction role/definition stays missing; no label splitting,
    ancestor/subclass alias inheritance, report ranking or implicit truncation.
    """
    ontology_dir, overlay_path = Path(ontology_dir), Path(overlay_path)
    paths = sorted(ontology_dir.glob("*.ttl"))
    if not ontology_dir.is_dir() or not paths:
        raise ValueError("ontology_turtle_files_missing")
    overlay = _load_local(overlay_path)
    ontology = [(path, _load_local(path)) for path in paths]
    requested: dict[str, str] = {}
    missing = []
    canonical_units = set()
    for iri in dict.fromkeys(class_iris):
        card = catalog.get(iri)
        if card is None:
            missing.append({"iri": iri, "kind": "class", "reason": "class_outside_catalog"})
            continue
        requested[iri] = "class"
        for prop in card.get("properties", []):
            requested[prop["iri"]] = "property"
            if prop.get("canonical_unit"):
                canonical_units.add(prop["canonical_unit"])
    # Unit inclusion is declared by the overlay's target-unit/property links,
    # never inferred from report values or from semantic/lexical similarity.
    for node in set(overlay.subjects(VOCAB.role, Literal("unit"))):
        for_units = {str(value) for value in overlay.objects(node, VOCAB.forCanonicalUnit)}
        for_props = {str(value) for value in overlay.objects(node, VOCAB.forProperty)}
        if for_units & canonical_units or for_props & set(requested):
            requested[str(node)] = "unit"
    result = {
        "profile": PROFILE,
        "groups": {"entities": [], "values": [], "units": []},
        "entries": {}, "missing": missing,
        "metadata": {
            "ontology_files": [{"path": str(path),
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                               for path in paths],
            "overlay": {"path": str(overlay_path),
                        "sha256": hashlib.sha256(overlay_path.read_bytes()).hexdigest(),
                        "source": "manual_general_knowledge_experimental_not_expert_gold"},
            "source_text_used": False, "silver_reference_used": False,
            "direct_aliases_only": True, "truncated": False,
        },
    }
    for iri, kind in sorted(requested.items()):
        role = _one_literal(overlay, iri, VOCAB.role)
        label = _one_literal(overlay, iri, VOCAB.extractionLabel)
        definition = _one_literal(overlay, iri, SKOS.definition)
        manual_source = list(overlay.objects(URIRef(iri), VOCAB.source))
        if not role or not label or not definition or not manual_source:
            missing.append({"iri": iri, "kind": kind, "reason": "manual_entry_missing"})
            continue
        if (role not in _ROLE_GROUP or (kind == "property" and role != "attribute_value")
                or (kind == "unit" and role != "unit")
                or (kind == "class" and role not in {"entity_name", "record_anchor"})):
            missing.append({"iri": iri, "kind": kind, "reason": "extraction_role_mismatch"})
            continue
        if label in result["entries"]:
            # Never merge different IRIs just because the readable labels collide.
            raise ValueError("duplicate_extraction_label:" + label)
        direct_pref, direct_labels, direct_alt = [], [], []
        for path, graph in ontology:
            direct_pref.extend(_literal_rows(
                graph, iri, SKOS.prefLabel, kind="ontology", path=path))
            direct_labels.extend(_literal_rows(graph, iri, RDFS.label, kind="ontology", path=path))
            direct_alt.extend(_literal_rows(graph, iri, SKOS.altLabel, kind="ontology", path=path))
        manual_pref = _literal_rows(overlay, iri, SKOS.prefLabel,
                                    kind="manual_overlay", path=overlay_path)
        manual_alt = _literal_rows(overlay, iri, SKOS.altLabel,
                                   kind="manual_overlay", path=overlay_path)
        pref_rows = direct_pref or direct_labels or manual_pref
        if not pref_rows:
            missing.append({"iri": iri, "kind": kind, "reason": "preferred_label_missing"})
            continue
        # Prefer Chinese across files as well as within each source graph.
        pref_rows.sort(key=lambda row: (0 if row["language"] == "zh" else 1, row["value"]))
        pref_label = pref_rows[0]["value"]
        alt_rows = direct_alt or manual_alt
        aliases = list(dict.fromkeys(row["value"] for row in alt_rows
                                     if row["value"] != pref_label))
        hints = "、".join(dict.fromkeys([pref_label, *aliases]))
        description = f"{_ROLE_INSTRUCTIONS[role]}{definition} 上下文术语提示：{hints}。"
        if role == "attribute_value":
            description += "术语是字段语义线索，不是待返回的字段标题。"
        sources = [pref_rows[0], *alt_rows]
        for predicate in (SKOS.definition, VOCAB.role, VOCAB.extractionLabel):
            sources.extend(_literal_rows(overlay, iri, predicate,
                                         kind="manual_overlay", path=overlay_path))
        entry = {
            "iri": iri, "role": role, "pref_label": pref_label, "alt_labels": aliases,
            "description": description, "sources": sources,
        }
        canonical = _one_literal(overlay, iri, VOCAB.canonicalUnit)
        if canonical:
            entry["canonical_unit"] = canonical
        result["entries"][label] = entry
        result["groups"][_ROLE_GROUP[role]].append(label)
    result["metadata"].update(
        selected_class_count=len(set(class_iris)), requested_entry_count=len(requested),
        entry_count=len(result["entries"]), missing_count=len(missing),
        entries_using_authority_alt_labels=sum(
            any(source["kind"] == "ontology" and source["predicate"] == str(SKOS.altLabel)
                for source in entry["sources"])
            for entry in result["entries"].values()
        ),
    )
    return result


class LexicalTerm(EvidenceModel):
    text: str = Field(min_length=1)
    language: str | None = None


class VocabularyEntry(EvidenceModel):
    iri: str = Field(min_length=1)
    role: RoleLiteral["entity", "record_anchor", "field_label", "field_value", "unit"]
    extraction_label: str | None = None
    definition: str | None = None
    labels: list[LexicalTerm] = Field(default_factory=list)
    aliases: list[LexicalTerm] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    canonical_unit: str | None = None
    for_predicates: list[str] = Field(default_factory=list)
    for_canonical_units: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_explicit_source(self):
        if any(not source.strip() for source in self.source_refs):
            raise ValueError("vocabulary source references must not be blank")
        if self.extraction_label is not None and not self.extraction_label.strip():
            raise ValueError("extraction label must not be blank")
        if any(not term.text.strip() for term in [*self.labels, *self.aliases]):
            raise ValueError("vocabulary terms must not be blank")
        return self


class VocabularyOverlay(EvidenceModel):
    version: str = Field(min_length=1)
    entries: list[VocabularyEntry]

    @model_validator(mode="after")
    def unique_entries(self):
        keys = [(entry.iri, entry.role) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_vocabulary_role")
        labels = [entry.extraction_label for entry in self.entries if entry.extraction_label]
        if len(labels) != len(set(labels)):
            raise ValueError("duplicate_extraction_label")
        return self


_GENERIC_INSTRUCTIONS = {
    "entity": _ROLE_INSTRUCTIONS["entity_name"],
    "record_anchor": _ROLE_INSTRUCTIONS["record_anchor"],
    "field_label": "抽取原文明示的字段标题或字段名称，不把标题当作字段值。",
    "field_value": _ROLE_INSTRUCTIONS["attribute_value"],
    "unit": _ROLE_INSTRUCTIONS["unit"],
}


def build_extraction_vocabulary(
    snapshot: OntologySnapshot,
    class_iris: list[str],
    *,
    overlay: VocabularyOverlay | None = None,
) -> dict:
    """Compile recall hints from frozen input; never open a source or infer facts.

    Roles are keyed by IRI plus role. Direct authoritative aliases win over an
    explicit overlay; field labels and values remain separate model queries.
    Missing definitions remain visible instead of acquiring invented semantics.
    """
    manual = {(entry.iri, entry.role): entry for entry in overlay.entries} if overlay else {}
    requested = {}
    missing = []
    canonical_units = set()
    for iri in sorted(set(class_iris)):
        definition = snapshot.classes.get(iri)
        if definition is None:
            missing.append({"iri": iri, "role": "entity", "reason": "class_outside_snapshot"})
            continue
        role = "record_anchor" if (iri, "record_anchor") in manual else "entity"
        requested[iri, role] = definition
        menu = compile_local_menu(
            snapshot, SubjectRef(entity_id="vocabulary", revision=1, class_iri=iri)
        )
        for prop in menu.properties:
            requested[prop.iri, "field_label"] = prop
            requested[prop.iri, "field_value"] = prop
            if prop.canonical_unit:
                canonical_units.add(prop.canonical_unit)
        for edge in menu.relationships:
            requested[edge.iri, "field_label"] = edge
    predicates = {iri for iri, role in requested if role.startswith("field_")}
    for key, entry in manual.items():
        if entry.role == "unit" and (
            set(entry.for_predicates) & predicates
            or set(entry.for_canonical_units) & canonical_units
        ):
            requested[key] = None
    result = {
        "profile": "ontology-extraction-vocabulary-v1",
        "entries": {},
        "groups": {role: [] for role in ("entity", "field_label", "field_value", "unit")},
        "missing": missing,
        "metadata": {
            "ontology_snapshot_id": snapshot.snapshot_id,
            "ontology_hash": snapshot.ontology_hash,
            "overlay_hash": evidence_hash(overlay) if overlay else None,
            "source_text_used": False,
            "silver_reference_used": False,
            "direct_aliases_only": True,
            "truncated": False,
        },
    }
    lexical = snapshot.lexical_context.annotations if snapshot.lexical_context else {}
    for (iri, role), definition in sorted(requested.items()):
        entry = manual.get((iri, role))
        authoritative_definition = definition.description.strip() if definition else ""
        description = authoritative_definition or (entry.definition or "" if entry else "")
        if not description.strip():
            missing.append({"iri": iri, "role": role, "reason": "definition_missing"})
            continue
        terms = lexical.get(iri, [])
        labels = [term for term in terms if term.predicate_iri != SKOS_ALT_LABEL_IRI]
        if not labels and definition and definition.label.strip():
            labels = [LexicalTerm(text=definition.label)]
        labels = labels or (entry.labels if entry else [])
        if not labels:
            missing.append({"iri": iri, "role": role, "reason": "preferred_label_missing"})
            continue
        labels = sorted(labels, key=lambda term: (term.language != "zh", term.text))
        aliases = [term for term in terms if term.predicate_iri == SKOS_ALT_LABEL_IRI]
        aliases = aliases or (entry.aliases if entry else [])
        pref = labels[0].text
        alt = list(dict.fromkeys(term.text for term in aliases if term.text != pref))
        label = (
            entry.extraction_label if entry and entry.extraction_label
            else "vocab_" + evidence_hash([iri, role])[:24]
        )
        if label in result["entries"]:
            raise ValueError("duplicate_extraction_label:" + label)
        sources = [{
            "kind": "ontology", "snapshot_id": snapshot.snapshot_id,
            "iri": iri, "field": "definition" if authoritative_definition else "labels",
        }] if definition else []
        sources.extend({
            "kind": "ontology", "snapshot_id": snapshot.snapshot_id, "iri": iri,
            "predicate": term.predicate_iri, "value": term.text, "language": term.language,
        } for term in terms)
        if entry:
            sources.extend({"kind": "manual_overlay", "source_ref": ref}
                           for ref in entry.source_refs)
        compiled = {
            "iri": iri, "role": role, "pref_label": pref, "alt_labels": alt,
            "description": _GENERIC_INSTRUCTIONS[role] + description
            + " 上下文术语提示：" + "、".join([pref, *alt]) + "。",
            "sources": sources,
        }
        if entry and entry.canonical_unit:
            compiled["canonical_unit"] = entry.canonical_unit
        result["entries"][label] = compiled
        result["groups"]["entity" if role == "record_anchor" else role].append(label)
    result["metadata"].update(
        requested_entry_count=len(requested), entry_count=len(result["entries"]),
        missing_count=len(missing),
    )
    return result
