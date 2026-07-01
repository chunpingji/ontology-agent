"""Bridge layer: extraction edges → Facts for rule evaluation (010, FR-001).

Converts relationship extraction edges (from ``relation_extractor.extract_relationships``)
into the ``Facts`` dataclass consumed by the interpreter's ``evaluate()``, without DB
persistence.  Also provides postcondition injection for post-control re-evaluation (FR-003).
"""

from __future__ import annotations

import copy
from typing import Any

from app.services.reasoning.interpreter import Facts


def _short_name(iri: str) -> str:
    """Strip namespace prefix, keeping the local name (after last ``/`` or ``#``)."""
    for sep in ("#", "/"):
        idx = iri.rfind(sep)
        if idx >= 0:
            return iri[idx + 1 :]
    return iri


def edges_to_facts(edges: list[dict]) -> Facts:
    """Convert relationship extraction edges to a ``Facts`` instance.

    Each edge dict has the structure produced by ``_make_edge`` in
    ``relation_extractor.py``::

        subject_class_iri, predicate_iri, object_class_iri,
        object_data_properties: [{iri, label, value}, ...],
        source_ref, ...

    Mapping rules (research.md R4):
    - ``predicate_iri``  → ``relations[short_name]`` (append ``object_class_iri``)
    - ``object_data_properties[].iri`` → ``data_values[short_name]`` (if iri present)
    - ``object_data_properties[].label`` → ``scalars[label]``
    - DrugProduct class markers → ``drug_classes``
    """
    relations: dict[str, list[str]] = {}
    data_values: dict[str, Any] = {}
    scalars: dict[str, Any] = {}
    drug_classes: list[str] = []

    for edge in edges:
        pred_short = _short_name(edge["predicate_iri"])
        obj_class = edge.get("object_class_iri", "")

        relations.setdefault(pred_short, [])
        if obj_class and obj_class not in relations[pred_short]:
            relations[pred_short].append(obj_class)

        for dp in edge.get("object_data_properties") or []:
            label = dp.get("label", "")
            value = dp.get("value", "")
            iri = dp.get("iri")
            if iri:
                data_values[_short_name(iri)] = value
            if label:
                scalars[label] = value

        if "DrugProduct" in obj_class:
            for dp in edge.get("object_data_properties") or []:
                lbl = dp.get("label", "")
                val = dp.get("value", "")
                if lbl and ("分类" in lbl or "类别" in lbl) and val:
                    if val not in drug_classes:
                        drug_classes.append(val)

    return Facts(
        drug_classes=drug_classes,
        relations=relations,
        data_values=data_values,
        scalars=scalars,
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
