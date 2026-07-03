"""Entity alignment: match extracted candidates against existing KG entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.services.extraction.semantic import Embedder, cosine_similarity
from app.services.ontology_engine import IndividualInfo, OntologyEngine

logger = logging.getLogger(__name__)

# Hierarchy-level precedence for cross-level matches (014 US4, FR-017):
# subclass (most specific) → same class → parent (least specific).
_LEVEL_BY_RANK = {0: "subclass", 1: "same", 2: "parent"}
# Bound the ancestor walk so a circular / deeply-nested class hierarchy
# terminates safely (spec Edge Cases: no infinite recursion).
_MAX_HIERARCHY_DEPTH = 32


@dataclass
class AlignmentResult:
    action: str  # "new" | "merge" | "review" | "skip"
    match_iri: str | None = None
    match_score: float = 0.0
    match_label: str | None = None
    method: str = "none"  # "id" | "lexical" | "semantic" | "none"（命中策略，用于审计/日志）
    # 命中所处的层级（相对目标类）："subclass" | "same" | "parent" | "none"（FR-017 审计）。
    matched_level: str = "none"
    # 同一层级存在多个等秩候选时，不自动合并，改由人工复核（FR-017 歧义边界）。
    ambiguous_iris: list[str] = field(default_factory=list)


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

    **Hierarchy-aware search (US4, FR-016/FR-017)**: existing individuals are
    gathered across the *subclass/superclass chain* of ``target_class_iri`` — not
    only the exact class — so a duplicate previously recorded at a different
    hierarchy level is detected as a merge instead of a new duplicate (GAP-6). The
    subclass descendants come from ``engine.get_subclasses`` and the ancestor chain
    from ``get_class_detail().parent_iris`` recursion; each candidate individual is
    tagged with its level (``subclass``/``same``/``parent``). Resolution follows the
    documented precedence **subclass → same → parent**; a genuinely ambiguous
    outcome (multiple equally-ranked individuals at the winning level) is **not**
    auto-merged but surfaced for review (``action="review"`` with ``ambiguous_iris``).
    The matched level and method are recorded for audit.

    Engines that expose only ``get_individuals`` (older duck-typed doubles) degrade
    cleanly to same-class behavior — the chain collapses to ``{target_class_iri}``.
    """
    subclass_iris, ancestor_iris = _build_chain(engine, target_class_iri)
    # (level, rank, individual) for every existing individual within the chain,
    # deduped by IRI. Individuals outside the chain are excluded (class gate —
    # preserves the historical "实体类别相等" precondition for exact class).
    leveled = _gather_leveled(engine, target_class_iri, subclass_iris, ancestor_iris)

    # Step 1: Exact ID match — strongest signal, short-circuits fuzzy matching.
    if id_property:
        candidate_id = _get_candidate_value(candidate, id_property)
        if candidate_id:
            id_hits: list[tuple[int, float, str, IndividualInfo]] = []
            for _level, rank, ind in leveled:
                existing_id = _get_individual_value(ind, id_property)
                if existing_id and str(existing_id) == str(candidate_id):
                    id_hits.append((rank, 1.0, "id", ind))
            resolved = _resolve(id_hits)
            if resolved is not None:
                return resolved

    # Step 2: Fuzzy label match —— 字面 + 语义，跨层级评分后按精度择优。
    candidate_label = _get_candidate_label(candidate, label_property)
    if candidate_label and leveled:
        use_semantic = bool(embedder) and embedder.is_available()
        cand_vec: list[float] | None = None
        if use_semantic:
            ind_labels = [_ind_label(ind) for _l, _r, ind in leveled]
            embedder.embed_many([candidate_label, *[lbl for lbl in ind_labels if lbl]])
            cand_vec = embedder.embed(candidate_label)

        label_hits: list[tuple[int, float, str, IndividualInfo]] = []
        for _level, rank, ind in leveled:
            ind_label = _ind_label(ind)
            if not ind_label:
                continue
            lex = SequenceMatcher(None, candidate_label, ind_label).ratio()
            sem = 0.0
            if cand_vec is not None:
                vec = embedder.embed(ind_label)
                if vec is not None:
                    sem = cosine_similarity(cand_vec, vec)
            lex_hit = lex >= threshold
            sem_hit = sem >= semantic_threshold
            # 语义命中且不弱于字面 → 采信语义（捕捉同义/别名）；否则采信字面。
            if sem_hit and (not lex_hit or sem >= lex):
                label_hits.append((rank, round(float(sem), 4), "semantic", ind))
            elif lex_hit:
                label_hits.append((rank, round(float(lex), 4), "lexical", ind))

        resolved = _resolve(label_hits)
        if resolved is not None:
            return resolved

    return AlignmentResult(action="new", match_score=0.0, method="none", matched_level="none")


def _build_chain(engine: Any, target_class_iri: str) -> tuple[set[str], set[str]]:
    """Return ``(subclass_iris, ancestor_iris)`` for the target across the hierarchy.

    Degrades to empty sets when the engine lacks the traversal methods or a call
    fails — collapsing alignment to same-class behavior (pre-US4 semantics).
    """
    subclass_iris: set[str] = set()
    get_subclasses = getattr(engine, "get_subclasses", None)
    if callable(get_subclasses):
        try:
            subclass_iris = {
                c["iri"]
                for c in (get_subclasses(target_class_iri) or [])
                if c.get("iri")
            }
        except Exception:  # pragma: no cover - defensive: degrade to same-class
            logger.warning("align_entity: get_subclasses 失败，退化为同类对齐", exc_info=True)

    ancestor_iris: set[str] = set()
    get_class_detail = getattr(engine, "get_class_detail", None)
    if callable(get_class_detail):
        visited = {target_class_iri}
        frontier = [target_class_iri]
        for _ in range(_MAX_HIERARCHY_DEPTH):  # depth-bounded: no infinite recursion
            if not frontier:
                break
            nxt: list[str] = []
            for cls in frontier:
                try:
                    detail = get_class_detail(cls)
                except Exception:  # pragma: no cover - defensive
                    logger.warning("align_entity: get_class_detail 失败", exc_info=True)
                    detail = None
                for parent in getattr(detail, "parent_iris", None) or []:
                    if parent and parent not in visited:
                        visited.add(parent)
                        ancestor_iris.add(parent)
                        nxt.append(parent)
            frontier = nxt
    return subclass_iris, ancestor_iris


def _gather_leveled(
    engine: Any,
    target_class_iri: str,
    subclass_iris: set[str],
    ancestor_iris: set[str],
) -> list[tuple[str, int, IndividualInfo]]:
    """Collect existing individuals across the chain, deduped by IRI, each tagged
    with its hierarchy level relative to the target (nearest-first)."""
    by_iri: dict[str, IndividualInfo] = {}
    for cls in {target_class_iri, *subclass_iris, *ancestor_iris}:
        try:
            for ind in engine.get_individuals(cls) or []:
                by_iri.setdefault(ind.iri, ind)
        except Exception:  # pragma: no cover - defensive
            logger.warning("align_entity: get_individuals(%s) 失败", cls, exc_info=True)

    leveled: list[tuple[str, int, IndividualInfo]] = []
    for ind in by_iri.values():
        classes = ind.class_iris or []
        # Classify by nearest level: a subclass typing wins over an explicit
        # same-class typing, which in turn wins over an ancestor typing.
        if any(c in subclass_iris for c in classes):
            leveled.append(("subclass", 0, ind))
        elif target_class_iri in classes:
            leveled.append(("same", 1, ind))
        elif any(c in ancestor_iris for c in classes):
            leveled.append(("parent", 2, ind))
        # else: outside the subclass/superclass chain — excluded (class gate).
    return leveled


def _resolve(
    hits: list[tuple[int, float, str, IndividualInfo]],
) -> AlignmentResult | None:
    """Resolve scored hits by precedence then ambiguity (US4, FR-017).

    ``hits`` is a list of ``(rank, score, method, individual)``. The winning
    level is the minimum rank present (subclass=0 → same=1 → parent=2). Within
    that level the top-scoring individual wins; if two or more distinct
    individuals tie at the top score, the match is genuinely ambiguous → no
    auto-merge, surfaced for review. Returns ``None`` when there are no hits.
    """
    if not hits:
        return None
    best_rank = min(rank for rank, _, _, _ in hits)
    level = _LEVEL_BY_RANK[best_rank]
    # Distinct individuals at the winning rank, keeping each one's best hit.
    top_by_iri: dict[str, tuple[float, str, IndividualInfo]] = {}
    top_score = max(score for rank, score, _, _ in hits if rank == best_rank)
    for rank, score, method, ind in hits:
        if rank == best_rank and score == top_score:
            top_by_iri.setdefault(ind.iri, (score, method, ind))

    if len(top_by_iri) == 1:
        score, method, ind = next(iter(top_by_iri.values()))
        return AlignmentResult(
            action="merge",
            match_iri=ind.iri,
            match_score=score,
            match_label=ind.label_zh or ind.label_en,
            method=method,
            matched_level=level,
        )

    # Equal-rank, equal-strength candidates → not auto-merged (FR-017 ambiguity).
    method = next(iter(top_by_iri.values()))[1]
    return AlignmentResult(
        action="review",
        match_iri=None,
        match_score=top_score,
        method=method,
        matched_level=level,
        ambiguous_iris=sorted(top_by_iri),
    )


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
