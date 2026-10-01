"""Document-local co-reference with exact source proof; mentions stay authoritative."""

from itertools import combinations, islice

from .model import request_size
from .ontology import identity_guidance
from .planning import context_window
from .protocols import stage_schema
from .source import Window, identity, quote_for_reference, reference, references_cover

STRONG_BASES = {"explicit_alias", "scoped_identifier", "explicit_reference"}


def ancestors(classes, iri):
    seen, todo = set(), [iri]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        card = classes.get(current)
        if card is not None:
            todo.extend(card.get("parent_iris", ()) if isinstance(card, dict) else card.parent_iris)
    return seen


def compatible(classes, left, right):
    a, b = left.get("class_iri"), right.get("class_iri")
    return bool(a in classes and b in classes
                and (a in ancestors(classes, b) or b in ancestors(classes, a)))


def pair_id(left, right):
    return identity("coreference", sorted((left, right)))


def candidate_pairs(state, classes, work_rows=None):
    """Only source-clued work is eligible; type compatibility never creates a pair."""
    rows = state.get("work", {}).values() if work_rows is None else work_rows
    seen = set()
    for work in sorted(rows, key=lambda row: row["id"]):
        if work["kind"] != "coreference_review" or work["status"] != "ready":
            continue
        data = work["input"]
        keys = sorted([data["left_mention_id"], data["right_mention_id"]])
        left, right = [state.get("entities", {}).get(key) for key in keys]
        if (keys[0] == keys[1] or not data.get("clue_refs")
                or any(row is None or row.get("state") != "accepted"
                       or row.get("role") == "document_root" or not row.get("referent")
                       or row.get("referent_unresolved") for row in (left, right))
                or not compatible(classes, left, right)):
            continue
        key = pair_id(*keys)
        if key not in seen:
            seen.add(key)
            yield key, left, right


def review_coreferences(engine, *, work_rows=None):
    """One bounded call at a time, saving each result through the existing current state."""
    rows = [row for row in (engine.state.get("work", {}).values()
                           if work_rows is None else work_rows)
            if row["kind"] == "coreference_review" and row["status"] == "ready"]
    by_pair = {pair_id(row["input"]["left_mention_id"], row["input"]["right_mention_id"]): row
               for row in rows}
    pending = list(candidate_pairs(engine.state, engine.catalog.classes, rows))
    eligible = {key for key, _, _ in pending}
    waiting = {row["id"]: {**row, "status": "waiting",
                           "reason_code": "coreference_source_or_endpoint_unresolved"}
               for key, row in by_pair.items() if key not in eligible}
    if waiting:
        engine.commit({"work": waiting})

    def review(pairs):
        batch_work = [engine.state["work"][by_pair[key]["id"]] for key, _, _ in pairs]
        entities = {row["id"]: row for _, left, right in pairs for row in (left, right)}
        sections = {row["referent"]["section_id"] for row in entities.values()}
        headings = [reference(engine.ir, unit.evidence_id, 0, len(unit.text))
                    for unit in engine.ir.evidence_units
                    if unit.kind == "heading" and unit.section_node_id in sections
                    and unit.text.strip()]
        clues = [reference(engine.ir, *ref) if isinstance(ref, (list, tuple)) else ref
                 for work in batch_work for ref in work["input"]["clue_refs"]]
        window = context_window(
            engine.ir, Window("coreference", [], [], []), entities.values(),
            engine.state.get("fields", {}), [*headings, *clues],
        )
        aliases = engine.entity_aliases(window)
        ids = {f"P{i + 1}": pair for i, pair in enumerate(pairs)}
        payload = window.payload()
        payload["entities"] = [engine.entity_input(row, window, typed=True)
                               for row in sorted(entities.values(), key=lambda row: row["id"])]
        payload["pairs"] = [{"pair_id": key, "left_id": aliases[left["id"]],
                             "right_id": aliases[right["id"]],
                             "required_evidence": list(dict.fromkeys(
                                 quote_for_reference(window, row["referent"])["source_id"]
                                 for row in (left, right)
                             ))}
                            for key, (_, left, right) in ids.items()]
        payload["types"] = [
            {"iri": card.iri, "label": card.label, "description": card.description,
             **identity_guidance(card, annotation_contracts=engine.catalog.annotation_contracts)}
            for iri in sorted({row["class_iri"] for row in entities.values()})
            if (card := engine.catalog.classes[iri])
        ]
        schema = stage_schema("coreference_review",
                              source_ids=[s["source_id"] for s in window.sources],
                              candidate_ids=ids, pair_sources={
                                  key: [quote_for_reference(window, row["referent"])["source_id"]
                                        for row in (left, right)]
                                  for key, (_, left, right) in ids.items()
                              })
        if (engine.max_request_bytes is not None
                and request_size("coreference_review", payload, schema)
                > engine.max_request_bytes):
            if len(pairs) == 1:
                raise ValueError("HARNESS_EVIDENCE_CONTEXT_TOO_LARGE")
            middle = len(pairs) // 2
            review(pairs[:middle])
            review(pairs[middle:])
            return
        cursor = engine.state.get("cursor", {}).get("main")
        if cursor is not None and cursor.get("stage") != "coreference_review":
            engine.commit({"cursor": {"main": {**cursor, "stage": "coreference_review"}}})
        engine._call_window = window
        engine._call_targets = [{"domain": "work", "id": row["id"],
                                 "dependency_hash": row["dependency_hash"]} for row in batch_work]
        answer = engine.call("coreference_review", payload, schema)
        if set(answer.judgments) != set(ids):
            raise ValueError("coreference_pair_set_mismatch")
        changes, completed = {}, {}
        for alias, judgment in answer.judgments.items():
            key, left, right = ids[alias]
            verdict, reason = judgment.verdict, judgment.reason
            evidence, proof = [], []
            try:
                evidence = window.quotes(engine.ir, judgment.evidence)
                proof = [window.resolve(engine.ir, quote) for quote in judgment.proof]
                if verdict != "unresolved":
                    if not proof or not all(references_cover(ref, evidence) for ref in
                                            [left["referent"], right["referent"], *proof]):
                        raise ValueError("coreference_source_proof_incomplete")
                    if judgment.confidence < 0.9:
                        raise ValueError("coreference_confidence_insufficient")
                    if (verdict == "same" and judgment.basis not in STRONG_BASES
                            or verdict == "different" and judgment.basis != "distinct"):
                        raise ValueError("coreference_basis_insufficient")
                    if verdict == "same" and judgment.basis == "explicit_alias":
                        binding = judgment.alias_binding
                        if binding is None:
                            raise ValueError("coreference_alias_binding_missing")
                        a, b, declaration = (window.resolve(engine.ir, quote) for quote in
                                             (binding.left, binding.right, binding.declaration))
                        if (not references_cover(a, [left["referent"]])
                                or not references_cover(b, [right["referent"]])
                                or a["text"].strip().casefold() == b["text"].strip().casefold()
                                or not all(ref["text"] in declaration["text"] for ref in (a, b))
                                or not references_cover(declaration, evidence)):
                            raise ValueError("coreference_alias_binding_invalid")
                        for ref in (a, b, declaration):
                            if ref not in proof:
                                proof.append(ref)
            except ValueError as exc:
                verdict, reason = "unresolved", f"{exc}；模型理由：{reason}"
            changes[key] = {
                "id": key, "left_mention_id": left["id"], "right_mention_id": right["id"],
                "verdict": verdict, "basis": judgment.basis, "reason": reason,
                "evidence": evidence, "proof": proof,
            }
            work = engine.state["work"][by_pair[key]["id"]]
            changes[key]["dependency_hash"] = work["dependency_hash"]
            completed[work["id"]] = {
                **work, "status": "done", "reason_code": None,
                "output_ids": [key], "applied_dependency_hash": work["dependency_hash"],
            }
        engine.commit({"coreferences": changes, "work": completed})

    pending = iter(pending)
    while batch := list(islice(pending, 6)):
        review(batch)


def canonical_mentions(entities, decisions, classes):
    """Only complete, consistent same-cliques merge; contradictory chains never do."""
    aliases = {key: key for key in entities}
    adjacent = {key: set() for key in entities}
    for row in decisions:
        left, right = row["left_mention_id"], row["right_mention_id"]
        if (row["verdict"] == "same" and left in entities and right in entities
                and all(entities[key]["state"] == "accepted"
                        and entities[key]["role"] != "document_root" for key in (left, right))
                and compatible(classes, entities[left], entities[right])):
            adjacent[left].add(right)
            adjacent[right].add(left)
    visited = set()
    for start in sorted(entities):
        if start in visited:
            continue
        component, todo = set(), [start]
        while todo:
            key = todo.pop()
            if key in component:
                continue
            component.add(key)
            todo.extend(adjacent[key] - component)
        visited.update(component)
        if not all(right in adjacent[left] for left, right in combinations(component, 2)):
            continue
        canonical = min(component, key=lambda key: (
            -len(ancestors(classes, entities[key].get("class_iri"))), key,
        ))
        aliases.update(dict.fromkeys(component, canonical))
    return aliases


def project_coreferences(result, decisions, classes):
    """Project unified nodes without modifying mention facts or their original endpoints."""
    mentions = {row["id"]: row for row in result["entities"]}
    aliases = canonical_mentions(mentions, decisions, classes)
    grouped = {}
    for key, mention in mentions.items():
        canonical = aliases[key]
        group = grouped.setdefault(canonical, {**mentions[canonical], "mentions": [],
                                               "evidence": []})
        group["mentions"].append(mention)
        for ref in mention["evidence"]:
            if ref not in group["evidence"]:
                group["evidence"].append(ref)
    result["entities"] = list(grouped.values())
    for row in result.get("relation_groups", []):
        row["subject_mention_id"] = row["subject_id"]
        row["object_mention_ids"] = list(row["object_ids"])
        row["subject_id"] = aliases.get(row["subject_id"], row["subject_id"])
        row["object_ids"] = list(dict.fromkeys(aliases.get(k, k) for k in row["object_ids"]))
        if len(row["object_ids"]) != len(row["object_mention_ids"]):
            row.update(state="unresolved", timing_state="unresolved",
                       reason="共指结果改变关系成员数量，关系组保留未决")
    result["coreferences"] = [
        {**row, "applied": row["verdict"] == "same"
         and aliases.get(row["left_mention_id"]) == aliases.get(row["right_mention_id"])
         and row["left_mention_id"] in aliases and row["right_mention_id"] in aliases}
        for row in decisions
    ]
    for kind in ("properties", "relations"):
        for row in result[kind]:
            for endpoint in ("subject", "object") if kind == "relations" else ("subject",):
                key = endpoint + "_id"
                row[endpoint + "_mention_id"] = row[key]
                row[key] = aliases.get(row[key], row[key])
    for row in result["observations"]:
        row["candidate_subject_ids"] = list(dict.fromkeys(
            aliases.get(key, key) for key in row["candidate_subject_ids"]
        ))
        row["object_id"] = aliases.get(row["object_id"], row["object_id"])
        for alignment in row["alignments"]:
            alignment["subject_id"] = aliases.get(alignment["subject_id"], alignment["subject_id"])
