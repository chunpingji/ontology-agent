"""Grounded identifier partitions, independent of relation selection and source identity."""

from collections import defaultdict
from copy import deepcopy

from .ontology import identity_guidance, legal_property
from .planning import context_window
from .protocols import ReferentCandidates, stage_schema
from .source import identity, quote_for_reference, reference, references_cover

STRING = "http://www.w3.org/2001/XMLSchema#string"


def key_context(engine, window, class_iri):
    """Bounded mapped-key recall. Locate literals, without deciding word boundaries or splits."""
    if not engine.lookup:
        return {"key_candidates": [], "source_status": [], "issues": ["no_queryable_mapping"]}
    capabilities = engine.lookup("capabilities", engine.catalog, [class_iri])
    allowed = [c for c in capabilities["capabilities"] if c["class_iri"] == class_iri]
    if not allowed:
        return {"key_candidates": [], "source_status": [], "issues": capabilities.get("issues", [])}
    queries = [{"query_id": f"KQ{i}", "class_iri": class_iri,
                "mapping_ids": [c["mapping_id"]], "limit": 50}
               for i, c in enumerate(allowed)]
    response = engine.lookup("query", engine.catalog, {"capabilities": allowed, "queries": queries})
    candidates = []
    for result in response.get("results", []):
        capability = allowed[int(result["query_id"][2:])]
        keys = {p for g in capability["lookup_key_groups"]
                for p in [*g["property_iris"], *g["scope_property_iris"]]}
        keys.update(p["property_iri"] for p in capability["identity_properties"])
        for row in result["candidates"]:
            matched = []
            for prop in row["properties"]:
                if prop["property_iri"] not in keys:
                    continue
                for value in prop["values"]:
                    literal = value["value"]
                    if not isinstance(literal, str) or not literal:
                        continue
                    for source in window.sources:
                        offset, occurrence = 0, 0
                        while (start := source["text"].find(literal, offset)) >= 0:
                            matched.append({
                                "property_iri": prop["property_iri"], "value": literal,
                                "quote": {"source_id": source["source_id"], "text": literal,
                                          "occurrence": occurrence},
                                "boundary_status": "not_checked",
                            })
                            offset, occurrence = start + 1, occurrence + 1
            if matched:
                candidates.append({
                    "record_ref": row["record_ref"], "source_entity_iri": row["source_entity_iri"],
                    "class_iri": row["class_iri"], "label": row["label"],
                    "key_occurrences": matched,
                    "key_values": [p for p in row["properties"] if p["property_iri"] in keys],
                    "identifier_namespace": row["identifier_namespace"],
                    "business_scope_status": row["business_scope_status"],
                    "identity_status": "not_checked",
                })
    return {
        "key_candidates": candidates, "lookup_capabilities": allowed,
        "source_status": [{k: r.get(k) for k in (
            "query_id", "outcome", "complete", "truncated", "issues",
        )} for r in response.get("results", [])], "issues": response.get("issues", []),
    }


def overlap(a, b):
    return (a["source_id"] == b["source_id"] and a["start"] < b["end"]
            and b["start"] < a["end"])


def member_reference(member, refs, ir):
    selected = [refs[key] for key in member.expression_ids]
    if len({ref["source_id"] for ref in selected}) != 1:
        raise ValueError("member_identifiers_cross_sources")
    return reference(ir, selected[0]["source_id"], min(r["start"] for r in selected),
                     max(r["end"] for r in selected))


def register_span(ir, spans, ref):
    """The program owns both the physical location and text of every catalog entry."""
    ref = reference(ir, ref["source_id"], ref["start"], ref["end"])
    key = "P" + identity(ir.document_hash, ref["source_id"], ref["start"], ref["end"])
    spans[key] = ref
    return key


def initial_spans(engine, window, entities, context):
    spans = {}
    identifiers = []
    source_id = entities[0]["referent"]["source_id"]
    refs = window.quotes(engine.ir, [s["source_id"] for s in window.sources
                                    if s["evidence_id"] == source_id])
    refs.extend(e["referent"] for e in entities)
    for ref in refs:
        if ref["source_id"] == source_id and quote_for_reference(window, ref) is not None:
            register_span(engine.ir, spans, ref)
    # Source fields are available without a mapped source; their values are candidates,
    # not accepted identifiers. Names/whole-source context alone do not grant eligibility.
    for entity in entities:
        for fid in entity["field_ids"]:
            for ref in engine.state["fields"][fid]["value_evidence"]:
                if ref["source_id"] == source_id and quote_for_reference(window, ref) is not None:
                    identifiers.append(register_span(engine.ir, spans, ref))
    for candidate in context["key_candidates"]:
        for occurrence in candidate["key_occurrences"]:
            ref = window.resolve(engine.ir, occurrence["quote"])
            if ref["source_id"] == source_id:
                identifiers.append(register_span(engine.ir, spans, ref))
    return spans, list(dict.fromkeys(identifiers))


def span_input(window, spans, identifiers):
    return [{"span_id": key, "text": ref["text"],
             "source_id": quote_for_reference(window, ref)["source_id"],
             "start": ref["start"], "end": ref["end"], "boundary_status": "not_checked",
             "identifier_candidate": key in identifiers}
            for key, ref in spans.items()]


def resolve_span(spans, key):
    if key not in spans:
        raise ValueError("unknown_evidence_span")
    return spans[key]


def validate_partitions(answer, spans, ir, properties, identifier_span_ids):
    if answer.new_spans:
        raise ValueError("identifier_spans_not_registered")
    if not answer.expressions:
        if answer.partitions:
            raise ValueError("empty_expressions_with_partitions")
        return {}
    if not answer.partitions:
        raise ValueError("identifier_partition_missing")
    refs = {e.id: resolve_span(spans, e.span_id) for e in answer.expressions}
    if any(e.span_id not in identifier_span_ids for e in answer.expressions):
        raise ValueError("span_not_identifier_candidate")
    if len(refs) != len(answer.expressions):
        raise ValueError("duplicate_expression_id")
    if any(e.property_iri not in properties for e in answer.expressions):
        raise ValueError("expression_property_outside_identity_card")
    if len({ref["source_id"] for ref in refs.values()}) != 1:
        raise ValueError("member_identifiers_cross_sources")
    if len({(e.span_id, e.property_iri) for e in answer.expressions}) != len(answer.expressions):
        raise ValueError("duplicate_expression_span")
    if len({p.id for p in answer.partitions}) != len(answer.partitions):
        raise ValueError("duplicate_partition_id")
    leaves = [r for r in refs.values() if not any(
        other != r and references_cover(other, [r]) for other in refs.values()
    )]
    signatures = set()
    for partition in answer.partitions:
        if len({m.id for m in partition.members}) != len(partition.members):
            raise ValueError("duplicate_member_id")
        flat = [key for member in partition.members for key in member.expression_ids]
        if any(key not in refs for key in flat):
            raise ValueError("unknown_expression_reference")
        if len(flat) != len(set(flat)):
            raise ValueError("expression_assigned_to_multiple_members")
        if any(not references_cover(leaf, [refs[key] for key in flat]) for leaf in leaves):
            raise ValueError("partition_omits_group_mention")
        signature = tuple(sorted(tuple(sorted(m.expression_ids)) for m in partition.members))
        if signature in signatures:
            raise ValueError("duplicate_partition")
        signatures.add(signature)
        for index, member in enumerate(partition.members):
            member_anchor = member_reference(member, refs, ir)
            if any(
                not references_cover(refs[key], [member_anchor]) for key in member.expression_ids
            ):
                raise ValueError("identifier_outside_member_anchor")
            for other in partition.members[index + 1:]:
                if overlap(member_anchor, member_reference(other, refs, ir)) or any(
                    overlap(refs[a], refs[b])
                    for a in member.expression_ids for b in other.expression_ids
                ):
                    raise ValueError("overlapping_members_in_same_partition")
    return refs


def exact_queries(answer, refs, capabilities, class_iri):
    queries = []
    for expression in answer.expressions:
        mappings = [c["mapping_id"] for c in capabilities if any(
            p["property_iri"] == expression.property_iri for p in c["properties"]
        ) and c["class_iri"] == class_iri]
        if mappings:
            queries.append({
                "query_id": expression.id, "class_iri": class_iri, "mapping_ids": mappings,
                "limit": 5, "property_filters": [{
                    "property_iri": expression.property_iri,
                    "value": refs[expression.id]["text"], "datatype_iri": STRING,
                }],
            })
    return queries


def resolve_selection(selection, answer, refs, spans, ir):
    """Require semantic support and source coverage; self-rating is auxiliary only."""
    selected = next((p for p in answer.partitions if p.id == selection.selected_partition_id),
                    None)
    if selection.selected_partition_id is not None and selected is None:
        raise ValueError("unknown_selected_partition")
    support = [resolve_span(spans, key) for key in selection.evidence_span_ids]
    if selection.verdict == "unresolved":
        return None, support, "semantic_unresolved"
    if any(not references_cover(member_reference(m, refs, ir), support)
           for m in selected.members):
        return None, support, "selection_evidence_does_not_cover_members"
    return selected, support, None


def query_feedback(engine, answer, refs, class_iri):
    if not engine.lookup:
        return {"results": [], "issues": ["no_queryable_mapping"]}
    capabilities = engine.lookup("capabilities", engine.catalog, [class_iri])
    queries = exact_queries(answer, refs, capabilities["capabilities"], class_iri)
    if not queries:
        return {"results": [], "issues": capabilities.get("issues", [])
                or ["no_matching_identifier_mapping"]}
    response = engine.lookup("query", engine.catalog, {
        "capabilities": capabilities["capabilities"], "queries": queries,
    })
    key_properties = {c["mapping_id"]: {
        iri for group in c["lookup_key_groups"]
        for iri in [*group["property_iris"], *group["scope_property_iris"]]
    } for c in capabilities["capabilities"]}
    # All competitors and completeness remain visible; unrelated display fields do not.
    for row in response.get("results", []):
        expression = next(e for e in answer.expressions if e.id == row["query_id"])
        for c in row["candidates"]:
            c["properties"] = [p for p in c["properties"] if p["property_iri"] in (
                key_properties[c["mapping_id"]] | {expression.property_iri}
            )]
    return {**response, "capabilities": capabilities["capabilities"]}


def rebind_clues(engine, window, replacements, changes):
    """Preserve a collective clue as a collective clue after physical refinement."""
    def expand(keys):
        return list(dict.fromkeys(member for key in keys
                                  for member in replacements.get(key, [key])))

    for domain in ("hints", "reference_cues"):
        for key, original in engine.state.get(domain, {}).items():
            owners = [original.get("subject_id"), original.get("object_id"),
                      *original.get("object_ids", []), *original.get("target_ids", []),
                      *original.get("ambiguous_subject_members", [])]
            if not set(replacements).intersection(owners):
                continue
            row = deepcopy(original)
            subject = row.get("subject_id")
            if subject in replacements:
                members = replacements[subject]
                row["subject_id"] = members[0] if len(members) == 1 else None
                if len(members) > 1:
                    row["ambiguous_subject_members"] = list(members)
                    row.update(status="waiting", reason_code="ambiguous_subject_members")
            elif row.get("ambiguous_subject_members"):
                row["ambiguous_subject_members"] = expand(row["ambiguous_subject_members"])
            if "object_id" in row or "object_ids" in row:
                objects = row.get("object_ids", [row.get("object_id")])
                row["object_ids"] = expand([obj for obj in objects if obj is not None])
                row.pop("object_id", None)
            if "target_ids" in row:
                row["target_ids"] = expand(row["target_ids"])
            changes[domain][key] = row
            if row.get("ambiguous_subject_members"):
                oid, observation = engine.observation(
                    window, "关系线索主体归属待确定",
                    "原主体已细分为多个物理成员；保留完整线索，不推断每个成员均成立",
                    row.get("evidence", []), kind="relation",
                    candidate_subject_ids=row["ambiguous_subject_members"],
                )
                observation.update(state="unresolved", reason_code="ambiguous_subject_members")
                changes["observations"][oid] = observation


def run_referent_alignment(engine, base_window):
    buckets = defaultdict(list)
    for entity in engine.entities(base_window):
        if entity.get("identity_binding") or entity.get("referent_unresolved"):
            continue
        card = engine.catalog.classes.get(entity.get("class_iri"))
        if (card is None or not entity.get("referent") or entity["state"] == "accepted"
                or entity["window_id"] != base_window.id):
            continue
        guidance = identity_guidance(card)
        if any(p["property_kind"] == "data" and p["constraint_status"] == "resolved"
               and STRING in p["datatype_iris"] for p in guidance["identity_properties"]):
            buckets[(card.iri, entity["referent"]["source_id"])].append(entity)
    for (class_iri, _), entities in buckets.items():
        task_id = identity("referent_group", class_iri, sorted(
            (e["referent"]["source_id"], e["referent"]["start"], e["referent"]["end"])
            for e in entities
        ))
        if engine.state.get("referent_work", {}).get(task_id, {}).get("done"):
            continue
        card = engine.catalog.classes[class_iri]
        guidance = identity_guidance(card, annotation_contracts=engine.catalog.annotation_contracts)
        properties = [p["iri"] for p in guidance["identity_properties"]
                      if p["property_kind"] == "data" and p["constraint_status"] == "resolved"
                      and STRING in p["datatype_iris"]
                      and legal_property(engine.catalog, class_iri, p["iri"])]
        window = context_window(engine.ir, base_window, entities, engine.state.get("fields", {}))
        work = deepcopy(engine.state.get("referent_work", {}).get(task_id, {}))
        if "key_context" not in work:
            work["key_context"] = key_context(engine, window, class_iri)
            engine.commit({"referent_work": {task_id: work}})
        if "span_catalog" not in work:
            work["span_catalog"], work["identifier_span_ids"] = initial_spans(
                engine, window, entities, work["key_context"],
            )
            engine.commit({"referent_work": {task_id: work}})
        payload = {
            **work["key_context"],
            "sources": window.payload()["sources"],
            "mentions": [{"anchor": quote_for_reference(window, e["referent"]),
                          "label": e["label"]} for e in entities],
            "class_definition": {"iri": card.iri, "label": card.label,
                                 "description": card.description,
                                 "definition": [d.model_dump(mode="json") for d in card.definition],
                                 **guidance},
        }
        source_ids = [s["source_id"] for s in window.sources]
        group_source_ids = [s["source_id"] for s in window.sources
                            if s["evidence_id"] == entities[0]["referent"]["source_id"]]
        payload["group_source_ids"] = group_source_ids
        while True:
            spans = work["span_catalog"]
            identifiers = work["identifier_span_ids"]
            payload.update(evidence_spans=span_input(window, spans, identifiers),
                           new_spans_allowed=not work.get("spans_extended", False))
            if work.get("proposal_feedback"):
                payload["proposal_feedback"] = work["proposal_feedback"]
            if "proposal" not in work:
                engine._call_window = window
                engine._call_targets = [{"domain": "referent_work", "id": task_id,
                                         "dependency_hash": identity(task_id, payload)}]
                answer = engine.call("referent_candidates", payload, stage_schema(
                    "referent_candidates", source_ids=group_source_ids,
                    property_iris=properties,
                    span_ids=identifiers, new_spans_allowed=payload["new_spans_allowed"],
                ))
                # Failed answers remain current too; resume cannot repeat a paid proposal.
                work["proposal"] = answer.model_dump(mode="json")
                engine.commit({"referent_work": {task_id: work}})
            answer = ReferentCandidates.model_validate(work["proposal"])
            if not answer.new_spans:
                break
            if work.get("spans_extended"):
                raise ValueError("identifier_span_extension_exhausted")
            if answer.expressions or answer.partitions:
                raise ValueError("identifier_span_request_with_partitions")
            if any(quote.source_id not in source_ids for quote in answer.new_spans):
                raise ValueError("source_outside_reading_window")
            if any(quote.source_id not in group_source_ids for quote in answer.new_spans):
                if work.get("proposal_corrected"):
                    raise ValueError("identifier_span_outside_group")
                work["proposal_corrected"] = True
                work["proposal_feedback"] = {
                    "issue": "identifier_span_outside_group",
                    "allowed_source_ids": group_source_ids,
                }
                work.pop("proposal")
                engine.commit({"referent_work": {task_id: work}})
                continue
            expanded = dict(spans)
            expanded_identifiers = list(identifiers)
            for quote in answer.new_spans:
                ref = window.resolve(engine.ir, quote)
                if ref["source_id"] != entities[0]["referent"]["source_id"]:
                    raise ValueError("identifier_span_outside_group")
                key = register_span(engine.ir, expanded, ref)
                if key in expanded_identifiers:
                    raise ValueError("identifier_span_already_available")
                expanded_identifiers.append(key)
            work.update(span_catalog=expanded, identifier_span_ids=expanded_identifiers,
                        spans_extended=True)
            work.pop("proposal")
            engine.commit({"referent_work": {task_id: work}})
        refs = validate_partitions(answer, spans, engine.ir, properties, identifiers)
        if not refs:
            engine.commit({"referent_work": {task_id: {**work, "done": True}}})
            continue
        # A refinement may change object count, but cannot silently omit a disjoint mention.
        anchor = reference(engine.ir, next(iter(refs.values()))["source_id"],
                           min(r["start"] for r in refs.values()),
                           max(r["end"] for r in refs.values()))
        covered_ids = {e["id"] for e in entities if any(
            overlap(e["referent"], ref) for ref in refs.values()
        )}
        if not covered_ids:
            engine.commit({
                "entities": {e["id"]: {
                    **e, "state": "unresolved", "referent_unresolved": True,
                    "reason": "编号分组未覆盖该草案提及，不能建立编号归属",
                } for e in entities},
                "referent_work": {task_id: {
                    **work, "done": True, "selection_issue": "no_input_mention_covered",
                }},
            })
            continue
        if "feedback" not in work:
            work["feedback"] = query_feedback(engine, answer, refs, class_iri)
            engine.commit({"referent_work": {task_id: work}})
        engine._call_window = window
        engine._call_targets = [{"domain": "referent_work", "id": task_id,
                                 "dependency_hash": identity(task_id, work["proposal"],
                                                             work["feedback"])}]
        selection = engine.call("referent_selection", {
            **payload, "proposal": work["proposal"], "lookup_feedback": work["feedback"],
        }, stage_schema("referent_selection", source_ids=source_ids,
                        partition_ids=[p.id for p in answer.partitions], span_ids=spans))
        selected, support, selection_issue = resolve_selection(
            selection, answer, refs, spans, engine.ir,
        )
        decision_reason = selection.reason
        if selection_issue == "selection_evidence_does_not_cover_members":
            decision_reason = "所选证据未覆盖全部成员；" + decision_reason
        changes = defaultdict(dict)
        member_ids = []
        if selected is None:
            for e in entities:
                changes["entities"][e["id"]] = {
                    **e, "state": "unresolved", "referent_unresolved": True,
                    "reason": "指称分组未确定；" + decision_reason,
                }
        else:
            for e in entities:
                if e["id"] not in covered_ids:
                    changes["entities"][e["id"]] = {
                        **e, "state": "unresolved", "referent_unresolved": True,
                        "reason": "编号分组未覆盖该草案提及，不能建立编号归属",
                    }
            expressions = {e.id: e for e in answer.expressions}
            results = {r["query_id"]: r for r in work["feedback"].get("results", [])}
            for member in selected.members:
                ref = member_reference(member, refs, engine.ir)
                key = identity("bound_mention", task_id, selected.id, member.id)
                member_ids.append(key)
                bindings, fields = [], []
                for expression_id in member.expression_ids:
                    expression = expressions[expression_id]
                    value = refs[expression_id]
                    field_id = identity("field", [value])
                    fields.append(field_id)
                    changes["fields"][field_id] = {
                        "id": field_id, "alias": "", "label": "", "value": value["text"],
                        "missing": False, "evidence": [value], "value_evidence": [value],
                        "source_aliases": [], "row": None,
                    }
                    result = results.get(expression_id, {})
                    bindings.append({
                        "field_id": field_id, "span_id": expression.span_id,
                        "property_iri": expression.property_iri,
                        "value": value["text"], "quote": value,
                        "source_candidates": result.get("candidates", []),
                        "query_complete": result.get("complete", False),
                        "query_truncated": result.get("truncated", False),
                        "identity_status": "not_checked",
                    })
                # Retain unrelated fields only when their original owner is exactly this
                # member. Collective attributes require new ownership evidence.
                for old in entities:
                    if old["referent"] != ref:
                        continue
                    fields.extend(fid for fid in old["field_ids"] if not any(
                        overlap(fref, eref)
                        for fref in engine.state["fields"][fid]["value_evidence"]
                        for eref in refs.values()
                    ))
                changes["entities"][key] = {
                    "id": key, "label": ref["text"], "name": ref, "referent": ref,
                    "role": entities[0]["role"], "class_iri": class_iri,
                    "class_label": card.label, "state": "candidate", "reason": selection.reason,
                    "evidence": [ref, *support], "type_evidence": support,
                    "window_id": base_window.id, "field_ids": list(dict.fromkeys(fields)),
                    "identity_binding": {"group_id": task_id, "partition_id": selected.id,
                                         "member_id": member.id, "identifiers": bindings},
                }
            replaced = covered_ids
            for eid in replaced:
                changes["entities"][eid] = None
                changes["source_candidates"][eid] = None
            replacements = {
                old["id"]: [key for key in member_ids if overlap(
                    old["referent"], changes["entities"][key]["referent"],
                )] for old in entities if old["id"] in replaced
            }
            rebind_clues(engine, base_window, replacements, changes)
            current_ids = engine.state["window_entities"][base_window.id]["ids"]
            changes["window_entities"][base_window.id] = {
                "ids": [key for key in current_ids if key not in replaced] + member_ids,
            }
            # Existing source observations retain their evidence without dangling owners.
            for oid, obs in engine.state.get("observations", {}).items():
                if (replaced.intersection(obs.get("candidate_subject_ids", []))
                        or obs.get("subject_id") in replaced or obs.get("object_id") in replaced):
                    changes["observations"][oid] = {
                        **obs, "candidate_subject_ids": [], "subject_id": None, "object_id": None,
                    }
        alternatives = [
            p.id + ": " + " + ".join(
                "[" + ", ".join(refs[e]["text"] for e in m.expression_ids) + "]"
                for m in p.members
            ) for p in answer.partitions
        ]
        oid, observation = engine.observation(
            base_window, "编号指称候选分组",
            "；".join(alternatives) + "；" + (
                "选择 " + selected.id if selected else "指称未决"
            ) + "；" + decision_reason,
            [anchor], kind="entity", candidate_subject_ids=member_ids,
        )
        changes["observations"][oid] = observation
        changes["referent_work"][task_id] = {
            **work, "done": True, "selection": selection.model_dump(mode="json"),
            "selected_partition_id": selected.id if selected else None,
            "selection_issue": selection_issue, "member_ids": member_ids,
        }
        engine.commit(dict(changes))
    engine.advance("entity_review")


def binding_input(entity, window):
    binding = entity.get("identity_binding")
    if binding is None:
        return None
    return {**binding, "identifiers": [
        {**{k: v for k, v in row.items() if k not in {"field_id", "quote"}},
         "quote": quote_for_reference(window, row["quote"])}
        for row in binding["identifiers"]
    ]}


def validate_bound_property(subject, field, mapping, component):
    identifiers = (subject.get("identity_binding") or {}).get("identifiers", [])
    bound = [i for i in identifiers if i["field_id"] == field["id"]]
    if bound and not any(i["property_iri"] == mapping.predicate_iri
                         and i["value"] == component["value"] for i in bound):
        raise ValueError("property_conflicts_with_identifier_binding")
