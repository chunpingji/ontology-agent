"""Bridge layer: extraction edges → Facts for rule evaluation (010, FR-001).

Converts relationship extraction edges (from ``relation_extractor.extract_relationships``)
into the ``Facts`` dataclass consumed by the interpreter's ``evaluate()``, without DB
persistence.  Also provides postcondition injection for post-control re-evaluation (FR-003).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from app.services.extraction.transforms import apply_transform
from app.services.reasoning.interpreter import Facts

logger = logging.getLogger(__name__)

# Hierarchy root for drug-class membership (FR-012). Mirrors
# ``seed_declarative.DRUG_PRODUCT_IRI``; kept as a literal so importing the fact
# bridge (used widely, incl. reporting) does not pull the heavy ontology engine.
DRUG_PRODUCT_IRI = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"


def _short_name(iri: str | None) -> str:
    """Strip namespace prefix, keeping the local name (after last ``/`` or ``#``)."""
    if not iri:
        return ""
    for sep in ("#", "/"):
        idx = iri.rfind(sep)
        if idx >= 0:
            return iri[idx + 1 :]
    return iri


def _normalize_scalar(value: Any) -> Any:
    """Normalize a scalar against the shared controlled vocabulary (R10/FR-015).

    Reuses the R6 ``controlled_vocab`` transform seam (no new normalization
    engine, Principle V). Non-string / no-match values pass through unchanged —
    the transform's advisory note is discarded here.
    """
    if not isinstance(value, str):
        return value
    return apply_transform("controlled_vocab", None, value).value


def edges_to_facts(edges: list[dict], engine: Any = None) -> Facts:
    """Convert relationship extraction edges to a ``Facts`` instance.

    Each edge dict has the structure produced by ``_make_edge`` in
    ``relation_extractor.py``::

        subject_class_iri, predicate_iri, object_class_iri,
        object_data_properties: [{iri, label, value}, ...],
        source_ref, ...

    Mapping rules (research.md R4/R10):
    - ``predicate_iri``  → ``relations[short_name]`` (append ``object_class_iri``)
    - ``object_data_properties[].iri`` → ``data_values[short_name]`` (if iri present)
    - ``object_data_properties[].label`` → ``scalars[label]``
    - drug-class markers on a DrugProduct (**or subclass**) object → ``drug_classes``
    - class-level external alignments on the object → ``alignments[predicate]``

    **Ontology-aware mode (US3)** — when ``engine`` (an ``OntologyEngine``) is
    supplied, membership, domain gating, and alignment population consult the
    published ontology instead of hardcoded string matches, so new subclasses /
    renamed properties / external alignments are picked up with no code changes:

    - **Class membership (FR-012)**: an object is a drug-class member iff its
      IRI is ``DrugProduct`` or a subclass (``engine.get_subclasses``), replacing
      the brittle ``"DrugProduct" in obj_class`` substring test.
    - **Domain gating (FR-013)**: an ``iri``-keyed data value is asserted onto
      ``data_values`` only when the property's declared domain includes the
      object class (``engine.get_data_properties_by_domain``) — no cross-domain
      leakage. When the ontology declares no domain properties for the class,
      gating is skipped (assert-all) to avoid over-suppression.
    - **External alignments (FR-014)**: ``engine.get_class_alignments`` populates
      the previously-empty ``Facts.alignments`` (consumed by the interpreter's
      ``external_alignment`` op).

    Controlled-vocab scalar normalization (FR-015) is applied unconditionally
    (it reuses the static shared vocabulary; no engine needed). When ``engine``
    is ``None`` the legacy behavior is preserved verbatim (backward compatible).
    All engine calls are defensively wrapped — a flaky engine degrades to the
    legacy path rather than breaking fact building.
    """
    relations: dict[str, list[str]] = {}
    data_values: dict[str, Any] = {}
    scalars: dict[str, Any] = {}
    drug_classes: list[str] = []
    alignments: dict[str, list[str]] = {}

    # Hierarchy-aware drug-class membership set (FR-012): DrugProduct + subclasses.
    drug_member_iris = {DRUG_PRODUCT_IRI}
    if engine is not None:
        try:
            drug_member_iris |= {c["iri"] for c in engine.get_subclasses(DRUG_PRODUCT_IRI)}
        except Exception:  # pragma: no cover - defensive: degrade to legacy membership
            logger.warning("edges_to_facts: get_subclasses 失败，回退子串判定", exc_info=True)

    domain_cache: dict[str, set[str] | None] = {}
    align_cache: dict[str, list[str]] = {}

    def _allowed_props(obj_class: str) -> set[str] | None:
        """Declared data-prop IRI set for domain gating; ``None`` == do not gate."""
        if engine is None or not obj_class:
            return None
        if obj_class not in domain_cache:
            allowed: set[str] | None = None
            try:
                props = engine.get_data_properties_by_domain(obj_class)
                if props:
                    allowed = {p["iri"] for p in props}
            except Exception:  # pragma: no cover - defensive: skip gating on failure
                logger.warning("edges_to_facts: 域属性查询失败，跳过域门控", exc_info=True)
                allowed = None
            domain_cache[obj_class] = allowed
        return domain_cache[obj_class]

    def _class_alignments(obj_class: str) -> list[str]:
        if engine is None or not obj_class:
            return []
        if obj_class not in align_cache:
            try:
                align_cache[obj_class] = list(engine.get_class_alignments(obj_class))
            except Exception:  # pragma: no cover - defensive: no alignments on failure
                logger.warning("edges_to_facts: 类对齐查询失败", exc_info=True)
                align_cache[obj_class] = []
        return align_cache[obj_class]

    for edge in edges:
        pred_short = _short_name(edge.get("predicate_iri"))
        obj_class = edge.get("object_class_iri") or ""

        if pred_short:
            relations.setdefault(pred_short, [])
            if obj_class and obj_class not in relations[pred_short]:
                relations[pred_short].append(obj_class)

        # External-standard alignments (FR-014) → Facts.alignments[predicate].
        for align_iri in _class_alignments(obj_class) if pred_short else []:
            bucket = alignments.setdefault(pred_short, [])
            if align_iri not in bucket:
                bucket.append(align_iri)

        allowed = _allowed_props(obj_class)
        for dp in edge.get("object_data_properties") or []:
            label = dp.get("label", "")
            value = _normalize_scalar(dp.get("value", ""))
            iri = dp.get("iri")
            # Domain gating (FR-013): assert iri-keyed value only if in-domain.
            if iri and (allowed is None or iri in allowed):
                data_values[_short_name(iri)] = value
            if label:
                scalars[label] = value

        # Drug-class membership: hierarchy-aware with an engine, else legacy substring.
        is_drug = (
            obj_class in drug_member_iris if engine is not None else "DrugProduct" in obj_class
        )
        if is_drug:
            for dp in edge.get("object_data_properties") or []:
                lbl = dp.get("label", "")
                val = _normalize_scalar(dp.get("value", ""))
                if lbl and ("分类" in lbl or "类别" in lbl) and val:
                    if val not in drug_classes:
                        drug_classes.append(val)

    return Facts(
        drug_classes=drug_classes,
        relations=relations,
        data_values=data_values,
        scalars=scalars,
        alignments=alignments,
    )


def apply_postconditions(facts: Facts, postconditions: dict[str, Any]) -> Facts:
    """Shallow-copy ``facts`` and inject postcondition keys (research.md R5).

    Boolean/literal postconditions go into ``scalars``; class-typed postconditions
    (values that look like IRIs) go into ``relations`` as a sentinel entry.
    """
    new_facts = copy.copy(facts)
    new_facts.scalars = {**facts.scalars, **postconditions}
    new_facts.relations = dict(facts.relations)
    for key, val in postconditions.items():
        if isinstance(val, str) and ("/" in val or "#" in val):
            new_facts.relations.setdefault(key, [])
            if val not in new_facts.relations[key]:
                new_facts.relations[key].append(val)
    return new_facts


def snapshot_to_facts(selector, subject_iri, assertion_ids):
    """Read exact selected instances, retaining UNKNOWN for ambiguous scalar owners.

    This adapter does not run vocabulary transforms, infer classes from labels,
    call external sources, or inject planned controls as observed facts.
    """
    from collections import defaultdict

    allowed = set(assertion_ids)
    selected = [r for r in selector.records if r["assertion_id"] in allowed
                and selector._positive(r) and r["assertion_id"] not in selector.conflicts()]
    reachable = {subject_iri}
    for _ in range(16):
        added = {r["object_iri"] for r in selected
                 if r["candidate"]["kind"] == "relationship" and r["subject_iri"] in reachable}
        if added <= reachable:
            break
        reachable |= added
    facts = Facts(strict_evidence=True)
    aliases = defaultdict(set)
    for iri in selector.schema:
        aliases[_short_name(iri)].add(iri)
    facts.class_aliases = {key: next(iter(iris)) for key, iris in aliases.items() if len(iris) == 1}
    scalar_values = defaultdict(set)
    predicates = defaultdict(set)
    for record in selected:
        if record["subject_iri"] not in reachable:
            continue
        candidate = selector.candidates[record["assertion_id"]]
        predicate = candidate.predicate_iri
        if candidate.kind == "entity":
            facts.drug_classes.extend([candidate.class_iri, *selector.schema.get(candidate.class_iri, {}).get("parents", [])])
        elif candidate.kind == "relationship":
            target = selector.entities.get(record["object_iri"])
            if target:
                cls = target["candidate"]["class_iri"]
                facts.relations.setdefault(predicate, []).extend([cls, *selector.schema.get(cls, {}).get("parents", [])])
                predicates[_short_name(predicate)].add(predicate)
        elif candidate.kind == "property" and candidate.literal.kind not in {"range", "comparison"}:
            # Include owner in uniqueness: an A/B value is not a document-wide scalar.
            scalar_values[predicate].add((record["subject_iri"], candidate.literal.normalized_value))
            predicates[_short_name(predicate)].add(predicate)
    for predicate, values in scalar_values.items():
        if len(values) == 1:
            value = next(iter(values))[1]
            facts.data_values[predicate] = value
            facts.scalars[predicate] = value
    for short, values in predicates.items():
        if len(values) != 1:
            continue
        predicate = next(iter(values))
        if predicate in facts.data_values:
            facts.data_values[short] = facts.data_values[predicate]
            facts.scalars[short] = facts.data_values[predicate]
        if predicate in facts.relations:
            facts.relations[short] = facts.relations[predicate]
    return facts
