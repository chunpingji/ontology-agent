"""Isolated hierarchy/summary experiment around the production semantic runner.

``build_variant(base, ir, structure, mode, metadata=...)`` returns a fresh runner.
It does not mutate the baseline, IR, ontology, or production extraction code.
Metadata maps real node IDs to ``content_summary`` and provenance fields. A
DocStructure (or its serialized section_tree) can supply the same metadata.

The planner is deliberately deterministic lexical ontology matching, not an
LLM planner. It ranks existing class/predicate menus and source windows, keeps
all remaining work, and adds non-factual hints only to recall requests. Every
type/binding verification still receives the original production envelope.
GLiNER is optional span recall, never a source of accepted graph facts.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from time import perf_counter

from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id
from app.services.extraction.extraction_tasks import SYSTEM, GenericExtractionRunner
from app.services.extraction.hierarchical_context import model_request
from app.services.extraction.model_protocol import ModelProtocol

POLICY_VERSION = "evaluation-hierarchy-hints-v1"
MODES = {"structure", "structure_summary", "structure_summary_gliner"}
HINT_INSTRUCTION = (
    "以下是派生的抽取规划元数据，不是原文证据，也不是已确认实体或关系。"
    "优先检查提示类型、属性和关系，但仍独立检查全部给定类型及原文。"
    "仅在 target 原文中逐字引用；不得引用摘要作为事实，不得按摘要补造事实。"
    "父章节仅提供主题线索，不能自动决定局部主体、属性归属或关系成立。"
)


def _mapping(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    return {}


def collect_metadata(structure=None) -> dict[str, dict]:
    """Read summaries without mutating parser-owned tree objects."""
    if structure is None:
        return {}
    root = getattr(structure, "section_tree", None)
    if root is None and isinstance(structure, dict):
        root = structure.get("section_tree", structure)
    result = {}

    def visit(value):
        node = _mapping(value)
        node_id = node.get("node_id")
        if node_id:
            result[node_id] = deepcopy(node.get("layer_metadata", {}))
        for child in node.get("children", []):
            visit(child)

    visit(root)
    return result


def _terms(text: str) -> set[str]:
    """Language-agnostic tokens plus Han bigrams; no business-name dispatch."""
    value = text.casefold()
    terms, position = set(), 0
    while position < len(value):
        start = position
        char = value[position]
        position += 1
        if char.isascii() and char.isalnum():
            while position < len(value) and value[position].isascii() and value[position].isalnum():
                position += 1
            terms.add(value[start:position])
        elif "\u3400" <= char <= "\u9fff":
            while position < len(value) and "\u3400" <= value[position] <= "\u9fff":
                position += 1
            segment = value[start:position]
            if len(segment) == 1:
                terms.add(segment)
            terms.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return terms


def _score(text, definition):
    label = str(definition.get("label", ""))
    description = str(definition.get("description", ""))
    terms = _terms(label + " " + description)
    if not text or not terms:
        return 0.0
    overlap = len(_terms(text) & terms) / math.sqrt(max(1, len(terms)))
    exact_label = 2.0 if len(label.strip()) >= 2 and label.casefold() in text.casefold() else 0.0
    return overlap + exact_label


def build_plan(schema, ir, structure=None, mode="structure_summary", *, metadata=None):
    """Return auditable node rankings over legal ontology definitions only."""
    if mode not in MODES:
        raise ValueError(f"unknown hierarchy variant: {mode}")
    started = perf_counter()
    supplied = collect_metadata(structure) if metadata is None else deepcopy(metadata)
    nodes = {node["node_id"]: node for node in ir.nodes}
    diagnostics = []
    unknown = sorted(set(supplied) - set(nodes))
    if unknown:
        diagnostics.append({"code": "unknown_metadata_nodes_ignored", "node_ids": unknown})
    usable = {}
    for node_id, value in supplied.items():
        if node_id not in nodes:
            continue
        item = _mapping(value)
        if (
            item.get("analysis_id", ir.analysis_id) != ir.analysis_id
            or item.get("structure_hash", ir.structure_hash) != ir.structure_hash
            or item.get("summary_status") in {"stale", "unavailable", "disabled", "pending"}
        ):
            diagnostics.append(
                {"code": "stale_or_unavailable_metadata_ignored", "node_id": node_id}
            )
            continue
        usable[node_id] = item

    plan_nodes = {}
    for node_id, node in nodes.items():
        chain, seen, current = [], set(), node_id
        while current in nodes and current not in seen:
            seen.add(current)
            chain.append(current)
            current = nodes[current].get("parent_id")
        cues = []
        for distance, ancestor_id in enumerate(chain):
            ancestor = nodes[ancestor_id]
            item = usable.get(ancestor_id, {})
            summary = str(item.get("content_summary") or "") if mode != "structure" else ""
            cues.append(
                {
                    "node_id": ancestor_id,
                    "distance": distance,
                    "heading": ancestor.get("heading", ""),
                    "summary": summary,
                    "summary_source": item.get("summary_source", "none") if summary else "none",
                    "summary_status": item.get("summary_status", "unspecified")
                    if summary
                    else "absent",
                }
            )

        def relevance(definition):
            return round(
                sum(
                    (0.5 ** cue["distance"])
                    * (_score(cue["heading"], definition) + _score(cue["summary"], definition))
                    for cue in cues
                ),
                6,
            )

        classes, properties, relationships = [], [], []
        for iri, definition in schema.items():
            score = relevance(definition)
            if score > 0:
                classes.append(
                    {"class_iri": iri, "label": definition.get("label", iri), "score": score}
                )
            for field, target in (("properties", properties), ("relationships", relationships)):
                for predicate in definition.get(field, []):
                    value = relevance(predicate)
                    if value <= 0:
                        continue
                    target.append(
                        {
                            "subject_class_iri": iri,
                            "predicate_iri": predicate["iri"],
                            "label": predicate.get("label", predicate["iri"]),
                            "score": value,
                            **(
                                {
                                    "object_class_iris": [
                                        c for c in predicate.get("range", []) if c in schema
                                    ]
                                }
                                if field == "relationships"
                                else {}
                            ),
                        }
                    )
        for values in (classes, properties, relationships):
            values.sort(key=lambda item: -item["score"])
        plan_nodes[node_id] = {
            "node_id": node_id,
            "cues": cues,
            "entity_type_hints": classes,
            "property_hints": properties,
            "relationship_hints": relationships,
            "score": max(
                [item["score"] for group in (classes, properties, relationships) for item in group],
                default=0,
            ),
        }
    identity = {
        "policy_version": POLICY_VERSION,
        "mode": mode,
        "analysis_id": ir.analysis_id,
        "ontology_release": evidence_hash(schema),
        "nodes": plan_nodes,
    }
    return {
        **identity,
        "dependency_hash": evidence_hash(identity),
        "diagnostics": diagnostics,
        "planner": "deterministic_ontology_lexical_matching_not_llm",
        "all_source_regions_and_classes_preserved": True,
        "summary_node_count": sum(bool(usable.get(n, {}).get("content_summary")) for n in nodes)
        if mode != "structure"
        else 0,
        "planning_wall_seconds": perf_counter() - started,
    }


class HierarchyHintRunner(GenericExtractionRunner):
    """Production verification with source/menu ordering and recall-only hints."""

    def __init__(self, base_runner, ir, plan, *, gliner=None):
        # Reordering legal predicate definitions is a scheduling intervention,
        # never a schema addition. Copy first to preserve the baseline runner.
        schema = deepcopy(base_runner.schema)
        predicate_scores = {}
        for node in plan["nodes"].values():
            for item in [*node["property_hints"], *node["relationship_hints"]]:
                key = (item["subject_class_iri"], item["predicate_iri"])
                predicate_scores[key] = max(predicate_scores.get(key, 0), item["score"])
        for iri, definition in schema.items():
            for field in ("properties", "relationships"):
                if field in definition:
                    definition[field].sort(key=lambda p: -predicate_scores.get((iri, p["iri"]), 0))
        super().__init__(
            schema,
            base_runner.tokenizer,
            base_runner.model_call,
            model_identity=base_runner.model_identity,
            budget=base_runner.budget.model_copy(deep=True),
            compact_identifiers=base_runner.compact_identifiers,
            priority_paths=deepcopy(base_runner.priority_paths),
        )
        # The ontology's semantic release is unchanged by ordering a copied menu.
        self.ontology_release = base_runner.ontology_release
        self.trace_fn = base_runner.trace_fn
        self.assert_owner_fn = base_runner.assert_owner_fn
        self.plan = plan
        self.gliner = gliner
        self._gliner_cache = {}
        self._hint_ir = ir
        self.variant_statistics = Counter(
            {
                "planning_wall_seconds": plan["planning_wall_seconds"],
                "gliner_wall_seconds": 0.0,
            }
        )
        if plan["mode"] == "structure_summary_gliner":
            if gliner is None:
                raise ValueError("GLiNER mode requires an explicit span extractor")
            started = perf_counter()
            available = gliner.is_available()
            self.variant_statistics["gliner_load_wall_seconds"] = perf_counter() - started
            if not available:
                raise RuntimeError(
                    "GLiNER unavailable: do not label this run as a GLiNER experiment"
                )

    def input_id(self, ir, effective_class="", *, scheduler_version=None):
        base = super().input_id(ir, effective_class, scheduler_version=scheduler_version)
        return stable_id("hierarchy-experiment", [base, self.plan["dependency_hash"]])

    def pack_regions(self, ir, regions):
        # Stable sort preserves same-score order and visits the entire input.
        ordered = sorted(
            regions,
            key=lambda region: (
                -self.plan["nodes"]
                .get(ir.unit(region.evidence_id).section_node_id, {})
                .get("score", 0)
            ),
        )
        yield from super().pack_regions(ir, ordered)

    def _local_hints(self, payload):
        targets = [f for f in payload["fragments"] if f.get("purpose") == "target"]
        node_ids = list(dict.fromkeys(f["anchor"]["section_node_id"] for f in targets))
        selected = [
            self.plan["nodes"][node_id] for node_id in node_ids if node_id in self.plan["nodes"]
        ]
        selected.sort(key=lambda node: -node["score"])
        allowed = payload["task"].get("predicate_definition", {}).get("classes", {})
        class_scores = {}
        for node in selected:
            for item in node["entity_type_hints"]:
                if item["class_iri"] in allowed:
                    class_scores[item["class_iri"]] = max(
                        class_scores.get(item["class_iri"], 0), item["score"]
                    )
        priority = sorted(class_scores, key=lambda iri: -class_scores[iri])
        if allowed and not priority:
            self.variant_statistics["entity_requests_without_matching_type_hint"] += 1
        hints = {
            "source_kind": "derived_planning_metadata",
            "fact_eligible": False,
            "instruction": HINT_INSTRUCTION,
            "priority_class_iris": priority[:12],
            "remaining_classes_still_required": True,
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "cues": [{**cue, "summary": cue["summary"][:240]} for cue in node["cues"][:3]],
                    "property_hints": node["property_hints"][:6],
                    "relationship_hints": node["relationship_hints"][:6],
                }
                for node in selected[:3]
            ],
        }
        if self.gliner is not None:
            hints["gliner_span_suggestions"] = self._span_hints(payload, targets, priority)
        return hints, class_scores

    def _span_hints(self, payload, targets, priority):
        definition = payload["task"].get("predicate_definition", {})
        classes = definition.get("classes", {})
        if classes:
            iris = [*priority, *(iri for iri in classes if iri not in priority)]
            label_mapping = {}
            for iri in iris[:12]:
                label = str(classes[iri].get("label", iri))
                label_mapping.setdefault(label, []).append(iri)
        elif payload["task"].get("task_kind") == "property":
            iri = payload["task"]["predicate_iri"]
            label_mapping = {str(definition.get("label", iri)): [iri]}
        else:
            return []
        labels = list(label_mapping)
        pending, keys, results = [], [], []
        for fragment in targets:
            anchor = fragment["anchor"]
            key = evidence_hash([anchor, fragment["text"], labels])
            keys.append((key, fragment))
            if key not in self._gliner_cache:
                pending.append((key, fragment))
        if pending:
            started = perf_counter()
            batches = self.gliner.extract_batch_with_spans(
                [fragment["text"] for _, fragment in pending], labels
            )
            self.variant_statistics["gliner_wall_seconds"] += perf_counter() - started
            self.variant_statistics["gliner_batch_calls"] += 1
            if len(batches) != len(pending):
                raise ValueError("GLiNER returned an incomplete batch")
            for (key, fragment), spans in zip(pending, batches):
                accepted = []
                for span in spans:
                    start, end = span.get("start"), span.get("end")
                    text = span.get("text")
                    if (
                        not isinstance(start, int)
                        or not isinstance(end, int)
                        or not 0 <= start < end <= len(fragment["text"])
                        or fragment["text"][start:end] != text
                        or span.get("label") not in label_mapping
                    ):
                        self.variant_statistics["gliner_invalid_spans"] += 1
                        continue
                    offset = fragment["anchor"].get("span_start") or 0
                    accepted.append(
                        {
                            "evidence_id": fragment["anchor"]["evidence_id"],
                            "start": offset + start,
                            "end": offset + end,
                            "text": text,
                            "label": span["label"],
                            "ontology_iris": label_mapping[span["label"]],
                            "score": span.get("score", 0),
                            "validated_fact": False,
                        }
                    )
                self._gliner_cache[key] = accepted
                self.variant_statistics["gliner_valid_span_suggestions"] += len(accepted)
        for key, _ in keys:
            results.extend(self._gliner_cache[key])
        self.variant_statistics["gliner_suggestions_omitted_from_prompt"] += max(
            0, len(results) - 24
        )
        return results[:24]

    def _invoke(self, task, envelope, response_type, stage, candidate=None):
        if stage != "recall" or envelope.serialized_input is None:
            return super()._invoke(task, envelope, response_type, stage, candidate)
        payload = json.loads(envelope.serialized_input)
        hints, scores = self._local_hints(payload)
        definition = payload["task"]["predicate_definition"]
        if "classes" in definition:
            definition["classes"] = dict(
                sorted(definition["classes"].items(), key=lambda pair: -scores.get(pair[0], 0))
            )
        definition["extraction_hints"] = hints
        # Count the exact wire request after augmentation. Hints may be omitted
        # for budget; source regions, competitors, and ontology menus never are.
        schema = response_type.model_json_schema()
        user = model_request(payload, stage, candidate)
        wire = ModelProtocol(SYSTEM, user, schema) if self.compact_identifiers else None
        tokens = (
            self.tokenizer.count(
                (wire.user + wire.system) if wire else (user + SYSTEM + canonical_json(schema))
            )
            + 128
        )
        if tokens > task.budget.max_input_tokens:
            self.variant_statistics["hints_omitted_for_budget"] += 1
            return super()._invoke(task, envelope, response_type, stage, candidate)
        self.variant_statistics["hinted_recall_calls"] += 1
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        augmented = envelope.model_copy(
            update={
                "serialized_input": serialized,
                "context_hash": evidence_hash(
                    [envelope.context_hash, self.plan["dependency_hash"], serialized]
                ),
            }
        )
        return super()._invoke(task, augmented, response_type, stage, candidate)


def build_variant(
    base_runner, ir, structure=None, mode="structure_summary", *, metadata=None, gliner=None
):
    """Build a fresh experimental runner; callers time metadata preparation separately."""
    plan = build_plan(base_runner.schema, ir, structure, mode, metadata=metadata)
    return HierarchyHintRunner(base_runner, ir, plan, gliner=gliner)
