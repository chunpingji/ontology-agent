"""Entity alignment: match extracted candidates against existing KG entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.services.extraction.semantic import Embedder, cosine_similarity
from app.services.ontology_engine import IndividualInfo, OntologyEngine

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    action: str  # "new", "merge", "skip"
    match_iri: str | None = None
    match_score: float = 0.0
    match_label: str | None = None
    method: str = "none"  # "id" | "lexical" | "semantic" | "none"（命中策略，用于审计/日志）


def align_entity(
    candidate: dict[str, Any],
    target_class_iri: str,
    engine: OntologyEngine,
    id_property: str | None = None,
    label_property: str | None = None,
    threshold: float = 0.85,
    embedder: Embedder | None = None,
    semantic_threshold: float = 0.82,
) -> AlignmentResult:
    """Align a candidate entity against existing individuals in the KG.

    Strategy:
    1. Exact match on ID property (e.g., equipmentID)
    2. Fuzzy match on label/name —— 字面（SequenceMatcher）与语义（嵌入余弦）
       并行，任一过阈即判定 merge，取更高置信度者。语义匹配的前置条件是实体
       类别相等（见下方 class_iris 门控）；``embedder`` 缺省/不可用时退化为纯
       字面匹配，行为与历史一致。
    3. If no match above threshold -> new entity
    """
    existing = engine.get_individuals(target_class_iri) or []
    # 前置条件：实体类别相等。get_individuals 已按类返回，这里显式核验 class_iris，
    # 防御 owlready2 cls.instances() 纳入多类/推断个体（保证语义匹配只在同类内进行）。
    existing = [ind for ind in existing if target_class_iri in (ind.class_iris or [])]

    # Step 1: Exact ID match
    if id_property:
        candidate_id = _get_candidate_value(candidate, id_property)
        if candidate_id:
            for ind in existing:
                existing_id = _get_individual_value(ind, id_property)
                if existing_id and str(existing_id) == str(candidate_id):
                    return AlignmentResult(
                        action="merge", match_iri=ind.iri,
                        match_score=1.0, match_label=ind.label_zh or ind.label_en,
                        method="id",
                    )

    # Step 2: Fuzzy label match —— 字面 + 语义。
    candidate_label = _get_candidate_label(candidate, label_property)
    if candidate_label and existing:
        labels = [(_ind_label(ind), ind) for ind in existing]

        use_semantic = bool(embedder) and embedder.is_available()
        cand_vec: list[float] | None = None
        if use_semantic:
            embedder.embed_many([candidate_label, *[lbl for lbl, _ in labels if lbl]])
            cand_vec = embedder.embed(candidate_label)

        best_lex_score, best_lex = 0.0, None
        best_sem_score, best_sem = 0.0, None
        for ind_label, ind in labels:
            if not ind_label:
                continue
            lex = SequenceMatcher(None, candidate_label, ind_label).ratio()
            if lex > best_lex_score:
                best_lex_score, best_lex = lex, ind
            if cand_vec is not None:
                sem = cosine_similarity(cand_vec, embedder.embed(ind_label))
                if sem > best_sem_score:
                    best_sem_score, best_sem = sem, ind

        lex_hit = best_lex is not None and best_lex_score >= threshold
        sem_hit = best_sem is not None and best_sem_score >= semantic_threshold

        # 语义命中且不弱于字面 → 优先采信语义（捕捉同义/别名）；否则采信字面。
        if sem_hit and (not lex_hit or best_sem_score >= best_lex_score):
            return AlignmentResult(
                action="merge", match_iri=best_sem.iri,
                match_score=round(float(best_sem_score), 4),
                match_label=best_sem.label_zh or best_sem.label_en, method="semantic",
            )
        if lex_hit:
            return AlignmentResult(
                action="merge", match_iri=best_lex.iri,
                match_score=round(float(best_lex_score), 4),
                match_label=best_lex.label_zh or best_lex.label_en, method="lexical",
            )

    return AlignmentResult(action="new", match_score=0.0, method="none")


def _ind_label(ind: IndividualInfo) -> str:
    return ind.label_zh or ind.label_en or ind.name


def _get_candidate_value(candidate: dict, prop_key: str) -> Any:
    for key, val in candidate.items():
        if prop_key in key:
            return val
    return None


def _get_candidate_label(candidate: dict, label_prop: str | None) -> str | None:
    if label_prop:
        val = _get_candidate_value(candidate, label_prop)
        if val:
            return str(val)
    for key, val in candidate.items():
        if "name" in key.lower() or "label" in key.lower():
            return str(val) if val else None
    return None


def _get_individual_value(ind: IndividualInfo, prop_key: str) -> Any:
    for key, val in ind.properties.items():
        if prop_key in key:
            return val
    return None
