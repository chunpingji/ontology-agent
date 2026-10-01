"""One grounded lookup feedback turn before registering any discovery candidates."""

from copy import deepcopy

from .model import request_size
from .protocols import Discovery, discovery_model, stage_schema
from .source import references_cover

STRING = "http://www.w3.org/2001/XMLSchema#string"


def prepare_queries(engine, window, draft, capabilities):
    allowed = {c["id"]: c for c in capabilities}
    queries, records = [], []
    for index, proposed in enumerate(draft.lookup_requests):
        query_id = f"Q{index + 1}"
        record = {"query_id": query_id, "hypothesis": proposed.model_dump(mode="json")}
        try:
            c = allowed[proposed.capability_id]
            anchor = window.resolve(engine.ir, proposed.anchor)
            properties = {p["property_iri"] for p in c["properties"]}

            def value(quote):
                ref = window.resolve(engine.ir, quote)
                if not references_cover(ref, [anchor]):
                    raise ValueError("lookup_value_outside_hypothesis")
                return ref["text"]

            filters = []
            for f in proposed.properties:
                if f.property_iri not in properties:
                    raise ValueError("lookup_property_outside_capability")
                filters.append({"property_iri": f.property_iri,
                                "value": value(f.value), "datatype_iri": STRING})
            if len({f["property_iri"] for f in filters}) != len(filters):
                raise ValueError("lookup_duplicate_property")
            name = {"value": value(proposed.name), "match": "exact"} if proposed.name else None
            if not name and not filters:
                raise ValueError("lookup_requires_source_condition")
            queries.append({
                "query_id": query_id, "class_iri": c["class_iri"],
                "mapping_ids": [c["mapping_id"]], "name": name,
                "property_filters": filters, "limit": 5,
            })
            record["status"] = "ready"
        except (ValueError, KeyError) as exc:
            record.update(status="invalid", issues=[str(exc) if isinstance(exc, ValueError)
                                                    else "lookup_unknown_capability"])
        records.append(record)
    return queries, records


def feedback_result(response, requests, queries, capabilities):
    """Keep all returned competitors and status, but omit unrelated display properties."""
    filters = {q["query_id"]: {f["property_iri"] for f in q["property_filters"]}
               for q in queries}
    key_properties = {c["mapping_id"]: {
        iri for group in c["lookup_key_groups"]
        for iri in [*group["property_iris"], *group["scope_property_iris"]]
    } for c in capabilities}
    feedback = {"requests": requests, "results": [], "issues": response.get("issues", [])}
    for result in response.get("results", []):
        row = deepcopy(result)
        for index, c in enumerate(row["candidates"]):
            c["candidate_id"] = f"{row['query_id']}C{index + 1}"
            c["query_complete"] = row["complete"]
            c["query_truncated"] = row["truncated"]
            c["properties"] = [p for p in c["properties"]
                               if p["property_iri"] in (filters[row["query_id"]]
                                                        | key_properties[c["mapping_id"]])]
        feedback["results"].append(row)
    return feedback


def base_discovery(answer):
    return Discovery.model_validate(answer.model_dump(include=set(Discovery.model_fields)))


def draft_request(engine, work):
    batch = {"stage": "discover", "call_key": work["draft_call_key"]}
    request = engine.batch_request(batch)
    output = engine.call_result(work["draft_call_key"])
    return request, discovery_model("draft").model_validate(output)


def next_discovery(engine, window):
    """Prepare one local continuation; no model work or captured graph state."""
    info = engine.state["windows"][window.id]
    work = deepcopy(info.get("lookup_work"))
    if work is None:
        initial = engine.discovery_input(window)
        if initial is None:
            return None
        payload, schema = initial
        capabilities = []
        if engine.lookup:
            result = engine.lookup(
                "capabilities",
                engine.catalog,
                engine.state["windows"][window.id]["guidance_class_iris"],
            )
            capabilities = result["capabilities"]
            info = engine.state["windows"][window.id]
            engine.commit(
                {
                    "windows": {
                        window.id: {
                            **info,
                            "lookup": {
                                "status": "unavailable",
                                "issues": result.get("issues", []),
                            },
                        }
                    }
                }
            )
        if capabilities:
            draft_payload = {**payload, "lookup_mode": "draft", "lookup_capabilities": capabilities}
            draft_schema = stage_schema(
                "discover",
                discovery_mode="draft",
                lookup_capabilities=capabilities,
                source_ids=[s["source_id"] for s in window.sources],
                field_ids=[f["alias"] for f in window.fields],
                primary_source_ids=[r["source_id"] for r in payload["reading_scope"]],
            )
            if request_size("discover", draft_payload, draft_schema) <= engine.max_request_bytes:
                return draft_payload, draft_schema, "draft"
            info = engine.state["windows"][window.id]
            engine.commit(
                {
                    "windows": {
                        window.id: {
                            **info,
                            "lookup": {
                                "status": "not_completed",
                                "issues": ["lookup_draft_budget_exceeded"],
                            },
                        }
                    }
                }
            )
        return payload, schema, "plain"
    request, draft = draft_request(engine, work)
    capabilities = request["payload"]["lookup_capabilities"]
    if "feedback" not in work:
        queries, records = prepare_queries(engine, window, draft, capabilities)
        response = (
            engine.lookup(
                "query",
                engine.catalog,
                {
                    "capabilities": capabilities,
                    "queries": queries,
                },
            )
            if queries
            else {"results": [], "issues": ["no_valid_lookup_requests"]}
        )
        work["feedback"] = feedback_result(response, records, queries, capabilities)
        engine.commit({"windows": {window.id: {**info, "lookup_work": work}}})
    feedback = work["feedback"]
    candidates = {c["candidate_id"]: c for r in feedback["results"] for c in r["candidates"]}
    if not candidates:
        engine.register_discovery(window, base_discovery(draft), {
            "status": "no_candidates", "queries": len(draft.lookup_requests),
            "issues": [*feedback["issues"],
                       *(issue for row in feedback["requests"] for issue in row.get("issues", []))],
            "results": [{k: r.get(k) for k in ("query_id", "outcome", "complete", "truncated")}
                        for r in feedback["results"]],
        })
        return None
    payload = {
        **request["payload"],
        "lookup_mode": "refine",
        "draft": draft.model_dump(mode="json"),
        "lookup_feedback": feedback,
    }
    schema = stage_schema(
        "discover",
        discovery_mode="refine",
        lookup_candidates=list(candidates),
        source_ids=[s["source_id"] for s in window.sources],
        field_ids=[f["alias"] for f in window.fields],
        primary_source_ids=[r["source_id"] for r in payload["reading_scope"]],
    )
    if request_size("discover", payload, schema) > engine.max_request_bytes:
        engine.register_discovery(
            window,
            base_discovery(draft),
            {
                "status": "not_completed",
                "issues": ["lookup_feedback_budget_exceeded"],
            },
        )
        return None
    return payload, schema, "refine"


def finish_discovery(engine, window, batch, answer):
    """Apply only this batch or persist its draft reference for the next local step."""
    info = engine.state["windows"][window.id]
    if batch["step"] == "plain":
        engine.register_discovery(
            window, answer, info.get("lookup", {}), batch_id=batch["batch_id"]
        )
        return
    if batch["step"] == "draft":
        if not answer.lookup_requests:
            engine.register_discovery(
                window,
                base_discovery(answer),
                {"status": "not_requested"},
                batch_id=batch["batch_id"],
            )
        else:
            engine.commit(
                {
                    "windows": {
                        window.id: {
                            **info,
                            "lookup_work": {
                                "draft_call_key": batch["call_key"],
                            },
                        }
                    }
                },
                batch_id=batch["batch_id"],
            )
        return
    work = info["lookup_work"]
    _, draft = draft_request(engine, work)
    feedback = work["feedback"]
    candidates = {c["candidate_id"]: c for r in feedback["results"] for c in r["candidates"]}
    metadata = {
        "status": "feedback_applied",
        "candidates": candidates,
        "suggestions": [],
        "issues": [
            *feedback["issues"],
            *(issue for request in feedback["requests"] for issue in request.get("issues", [])),
        ],
        "queries": len(draft.lookup_requests),
        "results": [
            {k: r.get(k) for k in ("query_id", "outcome", "complete", "truncated")}
            for r in feedback["results"]
        ],
    }
    discovered = answer.replacement if answer.replacement is not None else base_discovery(draft)
    local_ids = {e.local_id for e in discovered.entities}
    for suggestion in answer.source_suggestions:
        if suggestion.local_id not in local_ids or any(
            key not in candidates for key in suggestion.candidate_ids
        ):
            metadata["issues"].append("lookup_invalid_source_suggestion")
        else:
            metadata["suggestions"].append(suggestion.model_dump(mode="json"))

    def fields(result):
        return [
            *result.document_source_fields,
            *result.unowned_fields,
            *(f for e in result.entities for f in e.source_fields),
        ]

    seen = {f.model_dump_json() for f in fields(discovered)}
    metadata["retained_fields"] = [f for f in fields(draft) if f.model_dump_json() not in seen]
    engine.register_discovery(window, discovered, metadata, batch_id=batch["batch_id"])
