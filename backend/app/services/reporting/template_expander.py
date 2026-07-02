"""012 T030: Ontology-driven dynamic slot expansion.

Given a static :class:`ReportTemplate` and a document class IRI, discovers
additional data properties from the ontology that are NOT already present in
the static template.  New properties are added as slots grouped under
"扩展属性: {class_label}" groups in a dedicated "本体扩展" section.

The input template is never mutated — a **new** ``ReportTemplate`` is
returned with the expanded section appended.  If the ontology engine is
unavailable or the document class is not found, the original template is
returned unchanged.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ontology_engine import OntologyEngine
    from app.services.reporting.ast_template import ReportTemplate

logger = logging.getLogger(__name__)


def expand_template_with_ontology(
    template: ReportTemplate,
    doc_class_iri: str,
    engine: OntologyEngine,
) -> ReportTemplate:
    """Return *template* enriched with ontology-derived slots.

    Algorithm:
    1. From *doc_class_iri*, traverse object properties to find range classes.
    2. For each range class, call ``engine.get_data_properties_by_domain()``
       to discover data properties.
    3. Filter out properties already present in the static template (by IRI).
    4. Group new properties under "扩展属性: {class_label}" groups.
    5. Append groups to a new "本体扩展" section.

    Returns the original template unchanged when expansion yields no new
    slots, the engine is not loaded, or any lookup fails.
    """
    if not engine.is_loaded or not doc_class_iri:
        return template

    from app.services.reporting.ast_template import (
        Group,
        LLMExtractionSource,
        Section,
        Slot,
    )

    existing_iris = _collect_existing_iris(template)

    try:
        obj_props = engine.get_object_properties_by_domain(doc_class_iri)
    except Exception:
        logger.warning("本体扩展：获取对象属性失败", exc_info=True)
        return template

    new_groups: list[Group] = []
    for op in obj_props:
        for range_iri in op.get("range", []):
            try:
                data_props = engine.get_data_properties_by_domain(range_iri)
            except Exception:
                continue

            fresh = [dp for dp in data_props if dp["iri"] not in existing_iris]
            if not fresh:
                continue

            range_label = op.get("label", "") or range_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            class_name = range_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]

            slots: list[Slot] = []
            for dp in fresh:
                existing_iris.add(dp["iri"])
                slot_id = f"ontology.{class_name}.{dp['name']}"
                slots.append(Slot(
                    slot_id=slot_id,
                    label=dp.get("label") or dp["name"],
                    source=LLMExtractionSource(
                        kind="llm_extraction",
                        object_class_iri=range_iri,
                        data_property_iri=dp["iri"],
                        label=dp.get("label") or dp["name"],
                    ),
                    required=False,
                    on_missing="leave_blank",
                ))

            new_groups.append(Group(
                group_id=f"ontology_{class_name}",
                title=f"扩展属性: {range_label}",
                kind="fields",
                slots=slots,
            ))

    if not new_groups:
        return template

    expansion_section = Section(
        section_id="ontology_expansion",
        title="本体扩展",
        groups=new_groups,
    )

    expanded = copy.deepcopy(template)
    expanded.sections.append(expansion_section)
    logger.info(
        "本体扩展：添加 %d 个动态组、%d 个新槽位",
        len(new_groups),
        sum(len(g.slots) for g in new_groups),
    )
    return expanded


def _collect_existing_iris(template: ReportTemplate) -> set[str]:
    """Collect all property IRIs already referenced in the static template."""
    iris: set[str] = set()
    for _, _, slot in template.iter_slots():
        src = slot.source
        if hasattr(src, "data_property_iri"):
            iris.add(src.data_property_iri)
        if hasattr(src, "object_class_iri_contains"):
            iris.add(src.object_class_iri_contains)
    return iris
