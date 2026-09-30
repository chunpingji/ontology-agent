"""Keep options as a proposition; review participation and timing independently."""

from .ontology import legal_relation, model_menu
from .planning import context_window
from .protocols import stage_schema
from .source import identity, references_cover


def add_groups(engine, window, subject, objects, aliases, rels, proposals, changes):
    by_alias = {aliases[obj["id"]]: obj for obj in objects}
    allowed = {r["iri"]: r for r in rels}
    covered = set()
    for proposed in proposals:
        members = [by_alias.get(key) for key in proposed.object_ids]
        if proposed.predicate_iri not in allowed or any(
            obj is None or not legal_relation(
                engine.catalog, subject["class_iri"], proposed.predicate_iri, obj["class_iri"],
            ) for obj in members
        ):
            evidence = window.quotes(engine.ir, proposed.evidence)
            label = allowed.get(proposed.predicate_iri, {}).get("label", proposed.predicate_iri)
            key, item = engine.observation(
                window, label,
                "关系组包含当前类型卡不允许的谓词或成员类型，整组未采信",
                evidence, kind="relation", subject_id=subject["id"],
                candidate_subject_ids=[obj["id"] for obj in members if obj is not None],
            )
            changes["observations"][key] = item
            continue
        evidence = window.quotes(engine.ir, proposed.evidence)
        ids = [obj["id"] for obj in members]
        key = identity("relation_group", subject["id"], proposed.predicate_iri, sorted(ids),
                       proposed.participation, proposed.selection, proposed.timing,
                       proposed.polarity, proposed.conditions)
        covered.update((proposed.predicate_iri, key) for key in ids)
        changes["relation_groups"][key] = {
            "id": key, "subject_id": subject["id"], "object_ids": ids,
            "alignment_class_iri": subject["class_iri"],
            "predicate_iri": proposed.predicate_iri,
            "label": allowed[proposed.predicate_iri]["label"],
            "participation": proposed.participation, "selection": proposed.selection,
            "timing": proposed.timing, "timing_state": "candidate",
            "polarity": proposed.polarity, "conditions": proposed.conditions,
            "state": "candidate", "reason": proposed.reason,
            "proposal_reason": proposed.reason, "timing_reason": "时间待独立核对",
            "evidence": evidence, "window_id": window.id,
        }
    return covered


def requires_group(obj, objects):
    group = (obj.get("identity_binding") or {}).get("group_id")
    return bool(group and sum(
        (other.get("identity_binding") or {}).get("group_id") == group for other in objects
    ) > 1)


def review_groups(engine, base_window):
    changes = {}
    for candidate in engine.state.get("relation_groups", {}).values():
        if candidate["window_id"] != base_window.id or candidate["state"] != "candidate":
            continue
        entities = [engine.state["entities"][key]
                    for key in [candidate["subject_id"], *candidate["object_ids"]]]
        subject, *objects = entities
        window = context_window(engine.ir, base_window, entities, engine.state.get("fields", {}),
                                extra_refs=candidate["evidence"])
        predicate = next(r for r in model_menu(engine.catalog, [subject["class_iri"]])[
            "classes"][0]["relations"] if r["iri"] == candidate["predicate_iri"])
        payload = {
            "sources": window.payload()["sources"],
            "candidates": [{
                "id": "R", "kind": "relation_groups",
                "subject": engine.entity_input(subject, window, typed=True),
                "objects": [engine.entity_input(obj, window, typed=True) for obj in objects],
                "predicate_definition": predicate,
                **{k: candidate[k] for k in (
                    "participation", "selection", "polarity", "conditions",
                )},
            }],
        }
        if candidate["timing"] != "unspecified":
            payload["candidates"].append({
                **payload["candidates"][0], "id": "T", "kind": "relation_timing",
                "timing": candidate["timing"],
            })
        ids = [item["id"] for item in payload["candidates"]]
        aliases = engine.entity_aliases(window)
        answer = engine.call("evidence_review", payload, stage_schema(
            "evidence_review", source_ids=[s["source_id"] for s in window.sources],
            candidate_ids=ids, entity_ids=[aliases[e["id"]] for e in entities],
        ))
        if set(answer.judgments) != set(ids):
            raise ValueError("group_review_candidate_set_mismatch")
        row = dict(candidate)
        for key, judgment in answer.judgments.items():
            refs = window.quotes(engine.ir, judgment.evidence)
            verdict, reason = judgment.verdict, judgment.reason
            if verdict == "accepted" and (
                judgment.confidence < 0.85 or not refs or any(
                    e["state"] != "accepted" or e.get("referent") and not references_cover(
                        e["referent"], refs,
                    ) for e in entities
                ) or candidate["participation"] == "unknown"
            ):
                verdict, reason = "unresolved", "关系需明确参与方式、端点及原文证据"
            if key == "R":
                row.update(state=verdict, reason=reason, review_evidence=refs)
            else:
                row.update(timing_state=verdict, timing_reason=reason, timing_evidence=refs)
        if candidate["timing"] == "unspecified":
            row.update(timing_state="unresolved", timing_reason="原文未确定时间关系")
        elif row["state"] != "accepted":
            row.update(timing_state="unresolved", timing_reason="参与关系未确认，时间候选暂不采信")
        # Concerns remain separate observations, as for ordinary assertions.
        observations = {}
        by_alias = {aliases[e["id"]]: e["id"] for e in entities}
        for concern in answer.type_concerns:
            if concern.entity_id not in by_alias:
                raise ValueError("type_concern_endpoint_outside_batch")
            refs = window.quotes(engine.ir, concern.evidence)
            oid, item = engine.observation(
                base_window, "主体类型疑点（交由类型处理）", concern.reason, refs,
                kind="entity", candidate_subject_ids=[by_alias[concern.entity_id]],
            )
            observations[oid] = item
        if observations:
            engine.commit({"observations": observations})
        changes[row["id"]] = row
    # Competing participation interpretations cannot both be current accepted facts.
    combined = {**engine.state.get("relation_groups", {}), **changes}
    for key, row in combined.items():
        if row["state"] == "accepted" and any(
            other["state"] == "accepted" and other["subject_id"] == row["subject_id"]
            and other["predicate_iri"] == row["predicate_iri"]
            and set(other["object_ids"]) == set(row["object_ids"])
            and other["polarity"] == row["polarity"]
            and other["conditions"] == row["conditions"]
            and other["participation"] != row["participation"]
            for other in combined.values()
        ):
            changes[key] = {**row, "state": "unresolved", "timing_state": "unresolved",
                            "reason": "竞争关系参与解释相互冲突，保留未决"}
    if changes:
        engine.commit({"relation_groups": changes})
