#!/usr/bin/env python3
"""Read-only D input-equivalence check using the already frozen NER output.

Usage: backend/.venv/bin/python check-masked-equivalence.py RUN_ROOT
Prints JSON. No NER inference, Qwen call, or artifact write is performed.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path

REPOSITORY = Path("/opt/dev/chen/ontology-agent")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(REPOSITORY / "backend"))
    checks, scopes = [], {}

    def check(name, condition, detail=None):
        row = {"check": name, "status": "passed" if condition else "failed"}
        if detail is not None:
            row["detail"] = detail
        checks.append(row)
        if not condition:
            raise ValueError(name)

    manifest = read(root / "result.json")
    try:
        # Import pure reconstruction helpers from an empty cwd so Settings cannot
        # implicitly read the repository .env. No client or extractor is created.
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="cmc-masked-import-", dir="/tmp") as temporary:
            os.chdir(temporary)
            try:
                from app.evaluation import schema_card_summary_tools as runner
            finally:
                os.chdir(original_cwd)
        c = runner.c_runner
        check("D_identity", manifest.get("arm") == "D"
              and manifest.get("version") == runner.VERSION)
        for name, expected in manifest["code_hashes"].items():
            check("frozen_code/" + name, digest(root / "code" / name) == expected)
            check("reconstruction_code/" + name, digest(runner.code_paths()[name]) == expected)
        for name, expected in manifest["input_hashes"].items():
            check("root_input/" + name, digest(root / name) == expected)
        for name, expected in manifest["metadata_hashes"].items():
            check("metadata_input/" + name, digest(root / "metadata" / name) == expected)
        ir = runner.DocumentIR.model_validate(read(root / "ir.json"))
        catalog = read(root / "cards.json")
        frozen_metadata = runner.load_metadata(root / "metadata", ir, digest(root / "ir.json"))
        check("retrieval_metadata_source", frozen_metadata.analysis_id == ir.analysis_id)
        for scope in runner.SCOPES:
            directory = root / "D" / scope
            for name, expected in manifest["scope_input_hashes"][scope].items():
                check(scope + "/input_hash/" + name, digest(directory / name) == expected)
            check(scope + "/tools_hash", digest(directory / "tools.json")
                  == manifest["prepared_tool_hashes"][scope])
            retrieval = read(directory / "retrieval.json")
            enabled = retrieval["summary_enabled"]
            masked = retrieval["summary_masked"]
            demand = read(directory / "demand.json")
            vocabulary = read(directory / "vocabulary.json")
            actual_sources = read(directory / "sources.json")
            actual_plan = read(directory / "plan/proposal.json")
            actual_plan_schema = read(directory / "plan/schema.json")
            actual_tools = read(directory / "tools.json")
            check(scope + "/ner_completed", actual_tools["propose_mentions"]["execution_status"]
                  in {"completed", "not_requested"})
            rebuilt = []
            for selection in (enabled, masked):
                unit_ids = selection["selected_unit_ids"]
                check(scope + "/selected_ids_unique", len(unit_ids) == len(set(unit_ids)))
                units = [ir.unit(eid).model_dump(mode="json") for eid in unit_ids]
                sources = runner.build_sources({"source_units": units}, ir.model_dump(mode="json"))
                plan, plan_schema = runner.rebuild_plan(demand, sources, catalog)
                groups = runner.requested_groups(plan, vocabulary)
                rebuilt.append((sources, plan, plan_schema, groups))
            source_e, plan_e, plan_schema_e, groups_e = rebuilt[0]
            source_m, plan_m, plan_schema_m, groups_m = rebuilt[1]
            check(scope + "/enabled_sources_match_frozen", source_e == actual_sources)
            check(scope + "/enabled_plan_match_frozen", plan_e == actual_plan
                  and plan_schema_e == actual_plan_schema)
            check(scope + "/masked_sources_and_refs_equal", source_e == source_m)
            check(scope + "/masked_plan_equal", plan_e == plan_m and plan_schema_e == plan_schema_m)
            check(scope + "/ner_groups_equal", groups_e == groups_m)
            check(scope + "/ner_vocabulary_identity", actual_tools["propose_mentions"][
                "vocabulary_sha256"] == digest(directory / "vocabulary.json"))
            common_ner_identity = {
                "vocabulary": vocabulary, "model": manifest["gliner2_model"],
                "packages": manifest["packages"], "device": manifest["ner_device"],
                "labels_per_batch": manifest["labels_per_batch"],
                "adapter_sha256": manifest["code_hashes"]["gliner2_extractor.py"],
                "mentions_sha256": manifest["code_hashes"]["mentions.py"],
                "runner_sha256": manifest["code_hashes"]["schema_card_summary_tools.py"],
            }
            ner_input_e = {"sources": source_e, "groups": groups_e, **common_ner_identity}
            ner_input_m = {"sources": source_m, "groups": groups_m, **common_ner_identity}
            check(scope + "/full_ner_input_equal", ner_input_e == ner_input_m)
            # Only after proving identical NER inputs, reuse the one frozen output.
            reused_ner = deepcopy(actual_tools["propose_mentions"])
            inspection = c.inspect_evidence(source_m, [row["ref"] for row in plan_m["inspect_evidence"]])
            tools_m = {
                "get_schema_card": [c.get_schema_card(catalog, item["class_iri"], list(catalog))
                                    for item in plan_m["get_schema_card"]],
                "inspect_evidence": inspection, "propose_mentions": reused_ner,
                "source_spans": c.span_catalog(source_m, reused_ner, inspection),
            }
            runner.validate_tool_sources(tools_m, source_m, vocabulary, catalog, plan_m)
            runner.validate_tool_sources(actual_tools, source_e, vocabulary, catalog, plan_e)
            check(scope + "/all_frozen_tools_equal", actual_tools == tools_m)
            schema_e, _, _, cards_e = c.candidate_schema(
                plan_e, source_e, catalog, actual_tools["source_spans"])
            schema_m, _, _, cards_m = c.candidate_schema(
                plan_m, source_m, catalog, tools_m["source_spans"])
            payload_e = {"source": c.source_prompt(source_e), "subjects": cards_e,
                         "tool_results": c.model_tool_view(actual_tools)}
            payload_m = {"source": c.source_prompt(source_m), "subjects": cards_m,
                         "tool_results": c.model_tool_view(tools_m)}
            for name in ("source", "subjects", "tool_results"):
                check(scope + "/model_visible_" + name + "_equal", payload_e[name] == payload_m[name])
            check(scope + "/candidate_schema_equal", schema_e == schema_m)
            check(scope + "/full_candidate_input_equal", payload_e == payload_m)
            request_path = directory / "candidates/http-01-request.json"
            request_checked = request_path.is_file()
            if request_checked:
                request = read(request_path)
                user = json.loads(next(item["content"] for item in request["messages"]
                                       if item["role"] == "user"))
                system = next(item["content"] for item in request["messages"]
                              if item["role"] == "system")
                check(scope + "/actual_request_payload", user["input"] == payload_e
                      and user["output_schema"] == schema_e)
                check(scope + "/actual_request_schema", request["response_format"][
                    "json_schema"]["schema"] == schema_e)
                check(scope + "/actual_request_prompt", system == c.CANDIDATE_PROMPT)
                check(scope + "/actual_request_parameters", request["model"] == manifest["model"]
                      and request["temperature"] == manifest["temperature"]
                      and request["max_tokens"] == manifest["max_tokens"])
            verification_path = directory / "verification/http-01-request.json"
            verification_checked = verification_path.is_file()
            verification_hashes = None
            if verification_checked:
                # Conditional on reusing the SAME frozen candidate result. Only
                # its request inputs are inspected; no verification verdict is read.
                frozen = read(directory / "frozen-candidates.json")
                claims = [{key: value for key, value in claim.items()
                           if key not in {"binding", "metric_precheck"}}
                          for claim in frozen["claims"]]
                verification_e = {"source": c.source_prompt(source_e), "cards": cards_e,
                                  "subjects": frozen["subjects"], "claims": claims,
                                  "observations": frozen["observations"]}
                verification_m = {"source": c.source_prompt(source_m), "cards": cards_m,
                                  "subjects": frozen["subjects"], "claims": claims,
                                  "observations": frozen["observations"]}
                vschema_e = c.verification_schema(frozen["subjects"], frozen["claims"],
                                                frozen["observations"], source_e)
                vschema_m = c.verification_schema(frozen["subjects"], frozen["claims"],
                                                frozen["observations"], source_m)
                check(scope + "/verification_input_equal", verification_e == verification_m)
                check(scope + "/verification_schema_equal", vschema_e == vschema_m)
                request = read(verification_path)
                user = json.loads(next(item["content"] for item in request["messages"]
                                       if item["role"] == "user"))
                system = next(item["content"] for item in request["messages"]
                              if item["role"] == "system")
                check(scope + "/actual_verification_payload", user["input"] == verification_e
                      and user["output_schema"] == vschema_e)
                check(scope + "/actual_verification_schema", request["response_format"][
                    "json_schema"]["schema"] == vschema_e)
                check(scope + "/actual_verification_prompt", system == c.VERIFY_PROMPT)
                check(scope + "/actual_verification_parameters",
                      request["model"] == manifest["model"]
                      and request["temperature"] == manifest["temperature"]
                      and request["max_tokens"] == manifest["max_tokens"])
                verification_hashes = {
                    "reused_frozen_candidates_sha256": digest(directory / "frozen-candidates.json"),
                    "payload_sha256": canonical_digest(verification_e),
                    "schema_sha256": canonical_digest(vschema_e),
                }
            scopes[scope] = {
                "source_units": len(source_e), "ner_spans_reused": len(reused_ner["spans"]),
                "selected_record_order_equal": enabled["selected_record_ids"]
                == masked["selected_record_ids"],
                "input_equal": True, "actual_candidate_request_checked": request_checked,
                "actual_verification_request_checked": verification_checked,
                "verification_conditional_on_same_frozen_candidates": verification_hashes,
                "ner_input_sha256": canonical_digest(ner_input_e),
                "frozen_ner_output_sha256": canonical_digest(reused_ner),
                "source_sha256": canonical_digest(payload_e["source"]),
                "subject_cards_sha256": canonical_digest(cards_e),
                "model_tool_results_sha256": canonical_digest(payload_e["tool_results"]),
                "candidate_schema_sha256": canonical_digest(schema_e),
                "full_candidate_payload_sha256": canonical_digest(payload_e),
                "ner_reexecuted": False, "qwen_reexecuted": False,
            }
    except Exception as exc:
        checks.append({"check": "equivalence_exception", "status": "failed",
                       "detail": type(exc).__name__ + ": " + str(exc)})
    counts = Counter(row["status"] for row in checks)
    passed = counts.get("failed", 0) == 0 and len(scopes) == 8
    print(json.dumps({
        "audit": "D_masked_summary_input_equivalence_v1", "run_root": str(root),
        "run_id": manifest.get("run_id"), "status": "passed" if passed else "failed",
        "method": "Rebuild masked original sources/refs/plans/cards; verify identical NER inputs; "
        "reuse the SAME frozen NER; reconstruct and compare tools, schema and candidate payload.",
        "new_ner_calls": 0, "new_qwen_calls": 0,
        "candidate_or_verification_response_read": False,
        "conclusion": "Duplicate masked candidate Qwen call is unnecessary only under the "
        "verified identical source/cards/tools/schema/prompt/parameters and frozen NER reuse. "
        "This is input equivalence, not a second independent model evaluation or a quality gain.",
        "verification_note": "Every available verification request is compared after rebuilding "
        "its source/cards/schema and reusing the same frozen candidate output. Equality is "
        "conditional on that reuse; semantic verification responses are never inspected.",
        "check_counts": dict(counts), "scopes": scopes, "checks": checks,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
