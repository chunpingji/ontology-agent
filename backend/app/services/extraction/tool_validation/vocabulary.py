"""Local SKOS lexical overlay for the bounded CMC extraction experiment.

The overlay contains recall vocabulary, not ontology changes, observed values,
or validated facts. No report text or scoring reference enters this compiler.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rdflib import RDFS, SKOS, Graph, Literal, Namespace, URIRef

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


def build_extraction_vocabulary(
    catalog: dict,
    class_iris: list[str],
    *,
    ontology_dir: str | Path,
    overlay_path: str | Path = DEFAULT_OVERLAY,
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
