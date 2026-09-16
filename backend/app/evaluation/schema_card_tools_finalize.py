"""Complete only the unused third request on preserved tool-probe responses.

The original run must have stopped. Plans and candidate responses are never
regenerated; original HTTP attempts count toward the same three-request scope
budget. Existing semantic responses are reusable only for identical targets.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from app.evaluation.schema_card_probe import digest, read, write
from app.evaluation.schema_card_tools import (
    VERIFY_PROMPT,
    candidate_schema,
    finalize,
    freeze_candidates,
    invoke,
    model_identity,
    prepare,
    replay_citations,
    score_case,
    score_extended,
    source_prompt,
    summarize,
    validate_schema,
    verification_schema,
)


def semantic_target(frozen):
    return {"subjects": frozen["subjects"], "observations": frozen["observations"],
            "claims": [{key: value for key, value in claim.items()
                        if key not in {"binding", "metric_precheck"}}
                       for claim in frozen["claims"]]}


def complete_case(client, old, directory, run_id, cards):
    prior_error = old.get("error_code") or old.get("original_error")
    result = {"scope_id": old["scope_id"], "arm": old["arm"], "status": "failed",
              "calls": list(old["calls"]), "accepted": [], "observations": [],
              "unresolved": [], "rejected": [], "contract_passes": 0,
              "original_status": old["status"], "original_error": prior_error,
              "new_model_calls": 0}
    before = len(result["calls"])
    stage = "sources"
    try:
        sources = read(directory / "sources.json")
        stage = "plan"
        plan = read(directory / "plan" / "proposal.json")
        validate_schema(plan, read(directory / "plan" / "schema.json"))
        result["contract_passes"] = 1
        stage = "candidates"
        tools = read(directory / "tools.json")
        spans = tools["source_spans"]
        schema, subjects, bindings, menus = candidate_schema(plan, sources, cards, spans)
        raw = read(directory / "candidates" / "proposal.json")
        validate_schema(raw, schema)
        result["contract_passes"] = 2
        proposal = replay_citations(raw, sources, spans, isolate_errors=True)
        active, claims, observations, frame_rejected = freeze_candidates(
            proposal, subjects, bindings, sources)
        frozen = {"subjects": active, "claims": claims, "observations": observations}
        result["frames"] = {key: value for key, value in active.items() if key != "document"}
        vschema = verification_schema(active, claims, observations, sources)
        stage = "verification"
        semantic_file = directory / "verification" / "proposal.json"
        if semantic_file.exists():
            prior = read(directory / "frozen-candidates.json")
            if semantic_target(prior) != semantic_target(frozen):
                raise ValueError("changed_candidate_cannot_reuse_semantic_review")
            verification = read(semantic_file)
            validate_schema(verification, vschema)
            # The semantic target is identical, but binding/metric checks may
            # have changed. Persist the checks used to construct this result.
            write(directory / "frozen-candidates.json", frozen)
        else:
            if before != 2 or any(call.get("error_type") for call in result["calls"]):
                raise ValueError("third_request_not_available_without_retry")
            write(directory / "frozen-candidates.json", frozen)
            verification = invoke(
                client, directory / "verification", run_id, old["scope_id"], "verification",
                VERIFY_PROMPT, {"source": source_prompt(sources), "cards": menus,
                               **semantic_target(frozen)}, vschema, result["calls"],
            )
        result["contract_passes"] += 1
        result.update(finalize(active, claims, observations, verification, bindings, sources))
        result.update(status="complete", proposed_claim_count=len(claims),
                      proposed_observation_count=len(observations),
                      proposed_subject_count=len(active) - 1,
                      mechanical_rejection_count=sum(bool(c["binding"]["issues"]) for c in claims))
        result["rejected"].extend(frame_rejected)
        result["ner"] = {key: tools["propose_mentions"].get(key) for key in
                         ("execution_status", "coverage", "timing", "issues")}
        result["ner"]["span_count"] = len(tools["propose_mentions"].get("spans", []))
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["failure_stage"] = stage
        if isinstance(exc, ValueError) or type(exc).__name__ == "StructuredModelError":
            result["error_code"] = str(exc)[:200]
        prior_stage = (old["calls"][-1].get("stage") if old["calls"] else None)
        missing_proposal = directory / stage / "proposal.json"
        if (isinstance(exc, FileNotFoundError) and prior_error
                and old["status"] == "failed" and prior_stage == stage
                and exc.filename is not None and Path(exc.filename) == missing_proposal):
            # A timed-out model request never produced proposal.json. Report
            # that original stage failure instead of masking it as a new I/O error.
            result.update(error_type=old.get("error_type", type(exc).__name__),
                          error_code=prior_error, failure_stage=prior_stage,
                          completion_error_type=type(exc).__name__)
    result["new_model_calls"] = len(result["calls"]) - before
    write(directory / "result.json", result)
    return result


def main():
    from app.config import settings
    from app.services.llm.local_client import get_local_llm

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = read(args.source / "result.json")
    if original["status"] != "completed":
        raise ValueError("source_experiment_must_finish_first")
    prior_calls = sum(len(row["calls"]) for row in original["results"])
    if (prior_calls > 48 or len(original["results"]) != 16
            or any(len(row["calls"]) > 3 for row in original["results"])):
        raise ValueError("unexpected_source_budget")
    manifest, _, cards, _ = prepare(args.source, args.output)
    manifest.update(citation_policy="unique-nested-context-v2",
                    proposal_run_id=original["run_id"],
                    prior_model_calls=prior_calls, new_model_calls=0,
                    ner_probe=original.get("ner_probe"),
                    original_input_hashes=original["input_hashes"],
                    original_code_hashes=original["code_hashes"])
    # Reference bytes cannot change during this checker correction.
    for name in ("reference.json", "acceptance.json", "cases.json", "cards.json", "ir.json"):
        if digest(args.source / name) != digest(args.output / name):
            raise ValueError("frozen_input_changed:" + name)
    shutil.copyfile(__file__, args.output / "code" / Path(__file__).name)
    manifest["code_hashes"][Path(__file__).name] = digest(__file__)
    if (settings.local_llm_model != manifest["model"]
            or settings.local_llm_model_revision != manifest["declared_model_revision"]):
        raise ValueError("frozen_model_identity_mismatch")
    client = get_local_llm()
    if client is None or settings.local_llm_model not in model_identity(client, args.output):
        raise ValueError("configured_model_not_served")
    started = monotonic()
    write(args.output / "result.json", manifest)
    for old in original["results"]:
        directory = args.output / old["arm"] / old["scope_id"]
        shutil.copytree(args.source / old["arm"] / old["scope_id"], directory)
        result = complete_case(client, old, directory, manifest["run_id"] + "-" + old["arm"], cards)
        manifest["results"].append(result)
        manifest["new_model_calls"] += result["new_model_calls"]
        if prior_calls + manifest["new_model_calls"] > 48:
            raise ValueError("experiment_budget_exceeded")
        write(args.output / "result.json", manifest)
        print({"arm": old["arm"], "scope": old["scope_id"], "status": result["status"],
               "accepted": len(result["accepted"]), "error": result.get("error_code")}, flush=True)
    reference = read(args.output / "reference.json")
    extended = read(args.output / "acceptance.json")
    for result in manifest["results"]:
        directory = args.output / result["arm"] / result["scope_id"]
        result["acceptance"] = score_case(result, read(directory / "sources.json"),
                                          reference["cases"][result["scope_id"]])
        result["extended_acceptance"] = score_extended(result, extended)
        write(directory / "result.json", result)
    manifest.update(status="completed", finished_at=datetime.now(UTC).isoformat(),
                    completion_wall_seconds=round(monotonic() - started, 3),
                    summary=summarize(manifest["results"]))
    write(args.output / "result.json", manifest)
    write(args.output / "summary.json", manifest["summary"])


if __name__ == "__main__":
    main()
