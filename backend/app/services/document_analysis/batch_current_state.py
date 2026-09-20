"""Current batched Responses protocol checks, using the existing request/result tables."""

from __future__ import annotations

from app.models.document_analysis import DocumentRunRequest, DocumentRunResult
from app.services.document_analysis.run_store import HeadConflict
from app.services.extraction.ontology_guided.current_work import (
    call_request_key,
    validate_batch_tool_protocol,
)


def _request(store, run, protocol, attempt):
    from app.services.document_analysis.current_state import get_row

    key = call_request_key({"lineage_id": protocol["lineage_id"], "protocol_attempt": attempt})
    return get_row(store, run, "calls:requests", key, model=DocumentRunRequest)


def check_receipt(protocol, receipt, fingerprint):
    pending = protocol["pending_request"]
    members = {task["task_id"]: task for task in protocol["work_unit"]["members"]}
    if (pending is None or receipt["lineage_id"] != protocol["lineage_id"]
            or receipt.get("protocol_attempt") != pending["attempt"]
            or receipt.get("stage_group_seq") != pending["stage_group_seq"]
            or receipt.get("member_task_ids") != pending["member_task_ids"]
            or receipt.get("member_lineage_ids") != [
                members[task_id]["claim_lineage_id"] for task_id in pending["member_task_ids"]
            ] or receipt.get("input_hash") != pending["request_hash"]
            or receipt.get("run_fingerprint") != fingerprint
            or receipt["stage"].removeprefix("ontology_guided_") != pending["stage"]
            or call_request_key(receipt) != pending["reservation_key"]):
        raise HeadConflict("batch reservation differs from its pending request")
    for task_id in pending["member_task_ids"]:
        if (receipt.get("subject_ref") is not None
                and receipt["subject_ref"] != members[task_id]["subject"]):
            raise HeadConflict("batch receipt subject differs from its members")
        member = protocol["member_states"][task_id]
        if (member["last_participating_request_attempt"] != pending["attempt"]
                or member["last_stage_group_seq"] != pending["stage_group_seq"]):
            raise HeadConflict("member participation is not the pending request")


def protocol_results(store, run, protocol, *, unconfirmed_attempts=()):
    from app.services.document_analysis.current_state import (
        load_protocol_record,
        load_protocol_result,
    )
    from app.services.extraction.ontology_guided.tool_contracts import (
        TOOL_DEFINITIONS,
        ToolCall,
        ToolErrorResult,
    )

    unit_id = protocol["lineage_id"]
    turns = {}
    previous = 0
    for ref in protocol["turn_refs"]:
        result = load_protocol_result(store, run, unit_id, ref, "model_turn")
        attempt = result["attempt"]
        if (attempt <= previous or attempt not in protocol["completed_attempts"]
                or result["stage"] != protocol["stage"]
                or result.get("stage_group_seq") != protocol["stage_group_seq"]
                or result.get("member_task_ids") != protocol["stage_member_ids"]):
            raise HeadConflict("batch turns do not belong to the current ordered group")
        request = _request(store, run, protocol, attempt)
        receipt = request["reservation"] if request else {}
        if (receipt.get("input_hash") != result["input_hash"]
                or receipt.get("run_fingerprint") != run.run_fingerprint
                or receipt.get("stage_group_seq") != result["stage_group_seq"]
                or receipt.get("member_task_ids") != result["member_task_ids"]
                or receipt.get("stage", "").removeprefix("ontology_guided_") != result["stage"]
                or (request["result_ref"] != ref
                    and not (attempt in unconfirmed_attempts and request["result_ref"] is None))):
            raise HeadConflict("batch turn differs from its reserved physical request")
        turns[attempt] = ref, result
        previous = attempt
    tool_keys = set()
    for ref in protocol["completed_tool_results"]:
        result = load_protocol_result(store, run, unit_id, ref, "tool_result")
        key = result["attempt"], result["call_id"]
        if key in tool_keys or key[0] not in turns:
            raise HeadConflict("batch tool has no unique current response")
        turn = turns[key[0]][1]
        calls = [item for item in turn["output_items"] if item.get("type") == "function_call"]
        ids = [item.get("call_id") for item in calls]
        if (turn["response_status"] != "completed" or turn["error"] is not None
                or "reference_error" in turn
                or turn["incomplete_details"] is not None or not turn["allowed_tool_names"]
                or any(not isinstance(x, str) or not x for x in ids)
                or len(ids) != len(set(ids)) or key[1] not in ids
                or any(part.get("type") == "refusal" for item in turn["output_items"]
                       if item.get("type") == "message" for part in item.get("content", [])
                       if isinstance(part, dict))):
            raise HeadConflict("batch tool is attached to an unconsumable response")
        call = next(item for item in calls if item["call_id"] == key[1])
        envelope = result["result"]
        from app.services.extraction.ontology_guided.tool_runtime import (
            _ToolFailure,
            route_member_tool,
        )

        try:
            owner, _ = route_member_tool(
                ToolCall(call_id=call["call_id"], name=call["name"],
                         arguments_json=call["arguments"]),
                dict.fromkeys(turn["member_task_ids"]),
            )
        except (KeyError, TypeError, ValueError, _ToolFailure):
            owner = None
        blocked = envelope.get("status") in {"blocked", "error"} and envelope.get("data") is None
        if (result.get("stage_group_seq") != turn["stage_group_seq"]
                or owner != result.get("member_task_id") or (owner is None and not blocked)):
            raise HeadConflict("batch tool member permission differs from its call")
        try:
            if call.get("name") not in turn["allowed_tool_names"] and not blocked:
                raise ValueError("tool was not offered")
            result_type = (ToolErrorResult if envelope.get("data") is None
                           else TOOL_DEFINITIONS[call["name"]].result_type)
            result_type.model_validate(envelope, strict=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise HeadConflict("batch tool result contract is invalid") from exc
        tool_keys.add(key)
    members = {task["task_id"]: task for task in protocol["work_unit"]["members"]}
    for task_id, state in protocol["member_states"].items():
        attempt = state["last_participating_request_attempt"]
        if attempt:
            request = _request(store, run, protocol, attempt)
            receipt = request["reservation"] if request else {}
            if (task_id not in receipt.get("member_task_ids", [])
                    or members[task_id]["claim_lineage_id"] not in receipt.get(
                        "member_lineage_ids", [])
                    or receipt.get("stage_group_seq") != state["last_stage_group_seq"]):
                raise HeadConflict("batch member participation has no reserved request")
        for value in state["materialized_refs"].values():
            result = load_protocol_result(
                store, run, unit_id, value.get("result_ref"), "tool_result",
            )
            if result.get("member_task_id") != task_id:
                raise HeadConflict("materialized reference belongs to another batch member")
        for field in ("discovery", "verification", "outcome"):
            ref = protocol[field + "_refs"].get(task_id)
            if ref is None:
                continue
            record = load_protocol_record(store, run, unit_id, ref, field, member_task_id=task_id)
            version = record["result_version"]
            if (version["assertion_generation"] > state["assertion_generation"]
                    or version["evidence_revision"] > state["evidence_revision"]
                    or version["last_participating_request_attempt"] > attempt
                    or version["stage_group_seq"] > state["last_stage_group_seq"]):
                raise HeadConflict("batch result version exceeds its member state")
            result_attempt = version["last_participating_request_attempt"]
            if result_attempt:
                request = _request(store, run, protocol, result_attempt)
                receipt = request["reservation"] if request else {}
                if (task_id not in receipt.get("member_task_ids", [])
                        or receipt.get("stage_group_seq") != version["stage_group_seq"]):
                    raise HeadConflict("batch member result has no participating request")
    return turns


def persist_protocol(store, run, protocol, old, result_changes):
    from app.services.document_analysis.current_state import put_rows

    validate_batch_tool_protocol(protocol)
    same_group = old is not None and protocol["stage_group_seq"] == old["stage_group_seq"]
    if old is not None:
        for field in ("version", "lineage_id", "work_unit", "api_protocol"):
            if protocol[field] != old[field]:
                raise HeadConflict("frozen batch identity changed")
        if (protocol["request_attempt"] < old["request_attempt"]
                or protocol["stage_group_seq"] < old["stage_group_seq"]
                or protocol["completed_attempts"][:len(old["completed_attempts"])]
                != old["completed_attempts"]):
            raise HeadConflict("batch request counters regressed")
        pending = old["pending_request"]
        if (pending and pending["attempt"] not in protocol["completed_attempts"]
                and protocol["pending_request"] != pending):
            raise HeadConflict("unknown batch request cannot be replaced or refunded")
        if same_group:
            for field in ("stage", "stage_member_ids", "active_instructions"):
                if protocol[field] != old[field]:
                    raise HeadConflict("batch current group identity changed")
            for field in ("turn_refs", "completed_tool_results"):
                if protocol[field][:len(old[field])] != old[field]:
                    raise HeadConflict("batch confirmed current group results changed")
        else:
            if pending is not None:
                raise HeadConflict("batch cannot change groups with a pending request")
            turns = protocol_results(store, run, old)
            from app.services.document_analysis.current_state import load_protocol_result

            completed = set()
            for ref in old["completed_tool_results"]:
                tool = load_protocol_result(store, run, old["lineage_id"], ref, "tool_result")
                completed.add((tool["attempt"], tool["call_id"]))
            calls = {(attempt, item["call_id"]) for attempt, (_, turn) in turns.items()
                     if turn["response_status"] == "completed" and turn["error"] is None
                     and turn["incomplete_details"] is None
                     for item in turn["output_items"] if item.get("type") == "function_call"}
            if calls - completed:
                raise HeadConflict("batch cannot change groups with unpaired tools")
    refs = set(protocol["turn_refs"] + protocol["completed_tool_results"])
    for field in ("discovery_refs", "verification_refs", "outcome_refs"):
        refs.update(protocol[field].values())
    for state in protocol["member_states"].values():
        refs.update(value.get("result_ref") for value in state["materialized_refs"].values())
    changes = {key: value for key, value in result_changes.items()
               if value["lineage_id"] == protocol["lineage_id"]}
    if not set(changes) <= refs:
        raise HeadConflict("batch results must be referenced by the current unit")
    put_rows(store, run, DocumentRunResult, "calls:results", changes, immutable=True)
    store.db.flush()
    completed_before = old["completed_attempts"] if old else []
    newly_completed = protocol["completed_attempts"][len(completed_before):]
    turns = protocol_results(store, run, protocol, unconfirmed_attempts=newly_completed)
    if newly_completed:
        pending = (old or {}).get("pending_request")
        if (pending is None or newly_completed != [pending["attempt"]]
                or pending["attempt"] not in turns
                or turns[pending["attempt"]][1]["allowed_tool_names"]
                != pending["allowed_tool_names"]):
            raise HeadConflict("batch response has no exact prior pending request")
    refreshed = False
    for task_id, state in protocol["member_states"].items():
        prior = old["member_states"][task_id] if old else None
        if prior is None:
            continue
        for field in ("scope_id", "reference_context"):
            if state.get(field) != prior.get(field):
                raise HeadConflict("frozen batch member identity changed")
        for field in ("assertion_generation", "evidence_revision", "tool_calls_used",
                      "last_participating_request_attempt", "last_stage_group_seq"):
            if state[field] < prior[field]:
                raise HeadConflict("batch member counters regressed")
        if prior["recovery_used"] and not state["recovery_used"]:
            raise HeadConflict("batch member recovery budget regressed")
        if state["evidence_revision"] == prior["evidence_revision"]:
            for field in ("context_hash", "evidence_hash", "context_authorization"):
                if state[field] != prior[field]:
                    raise HeadConflict("batch authorization changed without evidence revision")
        else:
            from app.services.extraction.ontology_guided.context import ContextAuthorization

            try:
                authorization = ContextAuthorization.model_validate(
                    state["context_authorization"], strict=True,
                )
            except (TypeError, ValueError) as exc:
                raise HeadConflict(
                    "batch authorization refresh requires valid source authority",
                ) from exc
            if (authorization.task_id != task_id
                    or authorization.ir_identity.document_hash != run.document_hash):
                raise HeadConflict("batch authorization has a foreign source")
            retrieved = False
            for result in changes.values():
                if (result["field"] != "tool_result"
                        or result["value"].get("member_task_id") != task_id):
                    continue
                record = result["value"]
                envelope = record["result"]
                data = envelope.get("data") or {}
                calls = turns.get(record["attempt"], (None, {}))[1].get("output_items", [])
                if (envelope.get("status") == "ok" and data.get("new_evidence") is True
                        and data.get("context_hash") == state["context_hash"]
                        and any(call.get("call_id") == record["call_id"]
                                and call.get("name") == "retrieve_evidence" for call in calls)):
                    retrieved = True
            if not retrieved:
                raise HeadConflict("batch authorization refresh requires its confirmed retrieval")
            refreshed = True
        if state["assertion_generation"] > prior["assertion_generation"]:
            discovery_ref = protocol["discovery_refs"].get(task_id)
            discovery = changes.get(discovery_ref, {})
            if (state["assertion_generation"] != prior["assertion_generation"] + 1
                    or discovery.get("field") != "discovery"
                    or discovery.get("member_task_id") != task_id
                    or discovery.get("result_version", {}).get("assertion_generation")
                    != state["assertion_generation"]
                    or discovery.get("value", {}).get("assertion_generation")
                    != state["assertion_generation"]
                    or protocol["verification_refs"].get(task_id) is not None
                    or protocol["outcome_refs"].get(task_id) is not None):
                raise HeadConflict("batch generation requires a new owned discovery result")
        if state["assertion_generation"] == prior["assertion_generation"]:
            fields = ["discovery_refs"]
            if state["evidence_revision"] == prior["evidence_revision"]:
                fields.extend(["verification_refs", "outcome_refs"])
            for field in fields:
                before = old[field].get(task_id)
                if before is not None and protocol[field].get(task_id) != before:
                    if field != "outcome_refs" or not protocol[field].get(task_id):
                        raise HeadConflict("committed batch member result changed")
                    from app.services.document_analysis.current_state import load_protocol_record
                    from app.services.extraction.ontology_guided.current_work import (
                        member_result_version,
                    )

                    previous_result = load_protocol_record(
                        store, run, protocol["lineage_id"], before, "outcome",
                        member_task_id=task_id,
                    )
                    if state["last_participating_request_attempt"] <= previous_result[
                        "result_version"
                    ]["last_participating_request_attempt"]:
                        raise HeadConflict(
                            "batch outcome changed without a new participating request",
                        )
                    load_protocol_record(
                        store, run, protocol["lineage_id"], protocol[field][task_id], "outcome",
                        member_task_id=task_id,
                        result_version=member_result_version(protocol, task_id),
                    )
    if (same_group and old["stage_input_items"] and not refreshed
            and protocol["stage_input_items"] != old["stage_input_items"]):
        raise HeadConflict("batch current input changed without a confirmed retrieval")
    for attempt in newly_completed:
        ref, _ = turns[attempt]
        request = _request(store, run, protocol, attempt)
        put_rows(store, run, DocumentRunRequest, "calls:requests", {
            call_request_key(request["reservation"]): {
                **request, "dispatch_state": "completed", "actual_cost": 1,
                "cost_status": "measured", "result_ref": ref,
            },
        })
    return len(newly_completed)
