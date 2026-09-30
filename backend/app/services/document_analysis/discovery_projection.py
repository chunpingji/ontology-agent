"""Read saved proposals separately from accepted graph facts; never initiate model work."""

from __future__ import annotations

import json

from app.schemas.document_target_graph import (
    DiscoverySource,
    SavedDiscoveryItem,
    SavedDiscoverySummary,
)
from app.services.document_analysis.public_projection import build_selection_registry
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.claim_protocol import (
    FrozenClaimSet,
    iter_proposals,
    iter_quotes,
)


def project_saved_discovery(*, run_id, results, requests, index, graph, accepted_assertions=()):
    """Only exact saved claims and verified graph revisions can be labelled accepted."""
    saved, verifications, outcomes, turns = {}, {}, {}, []
    for row in results.values():
        identity = (row["lineage_id"], row.get("member_task_id"))
        value, field = row["value"], row["field"]
        if field == "discovery":
            previous = saved.get(identity)
            if previous is None or value["assertion_generation"] > previous["assertion_generation"]:
                saved[identity] = value
        elif field == "verification":
            verifications.setdefault(identity, []).extend(value.get("targets", []))
        elif field == "outcome":
            outcomes.setdefault(identity, []).append(value)
        elif field == "model_turn":
            turns.append((identity, value))
    invalid = {(ref.id, ref.revision) for ref in getattr(graph, "invalidated_refs", [])}
    accepted = set(accepted_assertions)
    for entity in graph.entities:
        refs = [entity.type_decision_ref, entity.referent_decision_ref]
        if entity.grounding_kind == "record":
            refs.append(entity.composition_decision_ref)
        if (entity.seed_origin != "user_selected" and entity.independent_review != "rejected"
                and entity.identity_state != "undetermined" and entity.source_selection_refs
                and all(refs) and (entity.entity_id, entity.revision) not in invalid
                and all((ref.id, ref.revision) not in invalid for ref in refs)):
            accepted.add((entity.entity_id, entity.revision))
    # Mention claims acquire a source-derived document entity ID on registration.
    # Use the saved exact claim-to-entity resolution, never labels or same-name matches.
    registered = set(accepted)
    for values in outcomes.values():
        for outcome in values:
            for resolution in outcome.get("reference_resolutions", []):
                entity_ref, claim_ref = resolution["entity_ref"], resolution["claim_ref"]
                claim_key = (claim_ref["id"], claim_ref["revision"])
                if ((entity_ref["id"], entity_ref["revision"]) in registered
                        and claim_key not in invalid):
                    accepted.add(claim_key)
    items, registry = [], {}

    def sources(quotes, issues=()):
        values = []
        seen = set()
        for quote in quotes:
            key = (quote.evidence_id, quote.text, quote.context_text)
            if key in seen:
                continue
            seen.add(key)
            ref = None
            try:
                if index is None or any("source" in code or "quote" in code for code in issues):
                    raise ValueError("source_not_authorized")
                unit = index.ir.unit(quote.evidence_id)
                haystack, offset = unit.text, 0
                if quote.context_text:
                    if haystack.count(quote.context_text) != 1:
                        raise ValueError("ambiguous_source")
                    offset = haystack.index(quote.context_text)
                    haystack = quote.context_text
                if not quote.text.strip() or haystack.count(quote.text) != 1:
                    raise ValueError("ambiguous_source")
                start = offset + haystack.index(quote.text)
                anchor = index.ir.anchor(quote.evidence_id, start, start + len(quote.text))
                selected = build_selection_registry(
                    recognition_run_id=run_id, analysis_id=index.ir.analysis_id,
                    graph=None, index=index, extra_anchors=[anchor],
                )
                registry.update(selected)
                ref = next(iter(selected))
            except (ValueError, KeyError):
                pass
            values.append(DiscoverySource(text=quote.text, selection_ref=ref))
        return values

    for identity, raw in saved.items():
        frozen = FrozenClaimSet.model_validate(raw, strict=True)
        for kind, proposal in iter_proposals(frozen):
            if kind not in {"entity", "property", "relation"}:
                continue
            ref = frozen.local_ref_map[proposal.local_id]
            issues = frozen.claim_issues.get(proposal.local_id, [])
            checks = [target for target in verifications.get(identity, [])
                      if evidence_hash({"claim_ref": ref, "content_hash": target["content_hash"]})
                      == target["target_id"]]
            decisions = {d["decision_id"]: d for target in checks for d in target["decisions"]}
            reasons = [*issues, *[
                f"{d['check_kind']}: {d['verdict']} · {d['reason']}"
                for d in decisions.values() if d["verdict"] != "supported"
            ], *[issue for target in checks for issue in target["validation_issues"]]]
            state = "accepted" if (ref.id, ref.revision) in accepted else (
                "rejected" if issues or any(d["verdict"] == "unsupported"
                                            for d in decisions.values()) else "pending"
            )
            if state == "pending" and not reasons:
                reasons = ["候选已保存，等待本体对齐与原文采信。"]
            quotes = list(iter_quotes(proposal))
            label = (proposal.value_quote.text if kind == "property" else
                     " → ".join([proposal.subject_id, proposal.predicate_iri,
                                  ", ".join(proposal.object_ids)]) if kind == "relation" else
                     " / ".join(q.text for q in quotes[:3]))
            items.append(SavedDiscoveryItem(
                id=ref.id, kind=kind, label=label, class_iri=getattr(proposal, "class_iri", None),
                predicate_iri=getattr(proposal, "predicate_iri", None),
                subject_id=getattr(proposal, "subject_id", None),
                object_ids=getattr(proposal, "object_ids", []), state=state,
                reasons=list(dict.fromkeys(reasons)), sources=sources(quotes, issues),
            ))
        for observation in frozen.observations:
            items.append(SavedDiscoveryItem(
                id=stable_id("saved-observation", [identity, observation]), kind="observation",
                label=observation.quote.text, predicate_iri=observation.predicate_iri,
                subject_id=observation.subject_id, state="observation",
                reasons=[f"{observation.kind}: {observation.reason}"],
                sources=sources([observation.quote]),
            ))
    for identity, turn in turns:
        invalid = False
        for output in turn.get("output_items", []):
            for part in output.get("content", []):
                if part.get("type") == "output_text":
                    try:
                        json.loads(part["text"])
                    except (ValueError, TypeError):
                        invalid = True
        if invalid or turn.get("error") or turn.get("response_status") != "completed":
            items.append(SavedDiscoveryItem(
                id=stable_id("saved-discovery-failure", [identity, turn["attempt"]]),
                kind="failure", label=f"{turn['stage']} 调用未形成有效结果", state="failed",
                reasons=["model_output_invalid_or_truncated" if invalid
                         else "model_call_incomplete"],
            ))
    return SavedDiscoverySummary(
        completed_calls=len(turns),
        inflight_calls=sum(row.get("cost_status") == "unknown" and not row.get("result_ref")
                           for row in requests.values()),
        candidate_count=sum(item.kind in {"entity", "property", "relation"} for item in items),
        items=items,
    ), registry
