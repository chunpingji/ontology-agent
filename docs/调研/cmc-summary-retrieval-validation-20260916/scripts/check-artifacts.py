#!/usr/bin/env python3
"""Read-only D summary-retrieval/Qwen artifact audit; no model or application imports.

Usage: python check-artifacts.py RUN_ROOT [--ner NER_ROOT]
Prints JSON. Exit 0 means all required checks passed on a completed experiment;
1 means a violated invariant; 2 means missing, skipped or unfinished validation.
"""

import argparse
import hashlib
import json
from collections import Counter
from importlib.metadata import version
from pathlib import Path

from jsonschema.validators import validator_for


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key:" + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non_json_constant:" + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def citation_content(value):
    if isinstance(value, list):
        return [citation_content(item) for item in value]
    if isinstance(value, dict):
        if {"ref", "quote"} <= set(value):
            return {key: value[key] for key in ("ref", "quote")}
        return {key: citation_content(item) for key, item in value.items()}
    return value


def raw_claims(proposal):
    claims = []
    for sid, subject in proposal["subjects"].items():
        for group, kind in (("attributes", "property"), ("relations", "relation")):
            for field, values in subject[group].items():
                for value in values:
                    claims.append(
                        {
                            "id": f"c{len(claims) + 1}",
                            "subject_id": sid,
                            "field": field,
                            "kind": kind,
                            "proposal": value,
                        }
                    )
    return claims


class Audit:
    def __init__(self):
        self.checks = []
        self.technical_failures = []
        self.raw = {}

    def record(self, name, status, detail=None, required=True):
        item = {"check": name, "status": status, "required": required}
        if detail is not None:
            item["detail"] = detail
        self.checks.append(item)

    def check(self, name, condition, detail=None):
        self.record(name, "passed" if condition else "failed", detail)

    def read(self, path, *, required=True):
        if not path.is_file():
            self.record("file:" + str(path), "missing", required=required)
            return None
        try:
            return strict_json(path.read_text())
        except (OSError, ValueError) as exc:
            self.record("json:" + str(path), "failed", str(exc))
            return None

    def hashed(self, path, expected, name):
        if not path.is_file():
            self.record(name, "missing", str(path))
            return
        actual = digest(path)
        self.check(
            name,
            isinstance(expected, str) and actual == expected,
            {"file": str(path), "actual_sha256": actual, "expected_sha256": expected},
        )

    def equal_files(self, left, right, name):
        if not left.is_file() or not right.is_file():
            self.record(
                name, "missing", [str(p) for p in (left, right) if not p.is_file()]
            )
            return
        self.check(name, digest(left) == digest(right))

    def schema(self, payload, schema, name):
        def local_refs(value):
            if isinstance(value, dict):
                if "$ref" in value and not value["$ref"].startswith("#"):
                    raise ValueError("external_schema_reference_forbidden")
                for child in value.values():
                    local_refs(child)
            elif isinstance(value, list):
                for child in value:
                    local_refs(child)

        try:
            local_refs(schema)
            cls = validator_for(schema)
            cls.check_schema(schema)
            errors = sorted(
                cls(schema).iter_errors(payload), key=lambda error: error.json_path
            )
            self.check(
                name,
                not errors,
                [error.json_path + ": " + error.message for error in errors[:10]],
            )
            return not errors
        except Exception as exc:
            self.record(name, "failed", type(exc).__name__ + ": " + str(exc))
            return False

    def stage(self, directory, scope, stage, result):
        prefix = scope + "/" + stage
        requests = sorted(directory.glob("http-*-request.json"))
        responses = sorted(directory.glob("http-*-response.json"))
        if not directory.exists():
            self.record(
                prefix + "/execution", "skipped", "stage_not_attempted_or_pending"
            )
            return [], 0, len(requests)
        schema = self.read(directory / "schema.json")
        calls = self.read(directory / "calls.json")
        if calls is None:
            self.record(prefix + "/ledger", "skipped", "stage_ledger_not_finalized")
            return [], 0, len(requests)
        self.check(prefix + "/no_retry", len(calls) <= 1 and len(requests) <= 1)
        self.check(prefix + "/request_ledger_count", len(calls) == len(requests))
        expected_requests = {
            f"http-{call['http_call']:02d}-request.json" for call in calls
        }
        self.check(
            prefix + "/request_identity",
            {p.name for p in requests} == expected_requests
            and all(c.get("stage") == stage and c.get("http_call") == 1 for c in calls),
        )
        self.check(
            prefix + "/response_identity",
            {p.name for p in responses}
            <= {f"http-{call['http_call']:02d}-response.json" for call in calls},
        )
        contract_passed = 0
        for call in calls:
            number = call["http_call"]
            request = self.read(directory / f"http-{number:02d}-request.json")
            response_path = directory / f"http-{number:02d}-response.json"
            if request is not None and schema is not None:
                actual = request.get("response_format", {}).get("json_schema", {})
                self.check(
                    prefix + "/requested_schema",
                    actual.get("strict") is True and actual.get("schema") == schema,
                )
                try:
                    user = strict_json(
                        next(
                            m["content"]
                            for m in request["messages"]
                            if m["role"] == "user"
                        )
                    )
                    self.check(
                        prefix + "/prompt_schema", user["output_schema"] == schema
                    )
                    if stage == "candidates":
                        menus = self.read(directory.parent / "subject-cards.json")
                        self.check(
                            prefix + "/prompt_cards", user["input"]["subjects"] == menus
                        )
                except (KeyError, StopIteration, ValueError) as exc:
                    self.record(prefix + "/prompt_schema", "failed", str(exc))
            if not response_path.is_file() and call.get("error_type"):
                preserved = result is not None and result.get("status") == "failed"
                self.check(
                    prefix + "/transport_failure_preserved",
                    preserved,
                    {
                        "error_type": call["error_type"],
                        "status_code": call.get("status_code"),
                    },
                )
                self.record(
                    prefix + "/raw_http_schema",
                    "skipped",
                    "transport_failure_no_response",
                )
                self.technical_failures.append({"scope": scope, "stage": stage, **call})
                continue
            response = self.read(response_path)
            if response is None or schema is None:
                continue
            try:
                choice = response["choices"][0]
                self.check(
                    prefix + "/response_choice",
                    len(response["choices"]) == 1 and choice.get("index") == 0,
                )
                self.check(
                    prefix + "/response_ledger",
                    call.get("usage", {}) == (response.get("usage") or {})
                    and call.get("finish_reason") == choice["finish_reason"],
                )
                self.check(prefix + "/completion_finish", choice["finish_reason"] == "stop",
                           {"finish_reason": choice["finish_reason"]})
                self.check(
                    prefix + "/model_request",
                    request.get("model") == self.manifest["model"],
                )
                content = choice["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("raw_message_content_not_string")
                payload = strict_json(content)
                valid = self.schema(payload, schema, prefix + "/raw_http_schema")
                proposal = self.read(directory / "proposal.json", required=valid)
                if proposal is not None:
                    self.check(
                        prefix + "/raw_equals_saved_proposal", payload == proposal
                    )
                if valid and proposal == payload and choice["finish_reason"] == "stop":
                    contract_passed += 1
                    self.raw[(scope, stage)] = payload
                else:
                    self.check(
                        prefix + "/contract_failure_preserved",
                        result is not None and result.get("status") == "failed",
                    )
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                self.record(prefix + "/raw_http_schema", "failed", str(exc))
                self.check(
                    prefix + "/contract_failure_preserved",
                    result is not None and result.get("status") == "failed",
                )
        return calls, contract_passed, len(requests)

    def disposition(self, directory, result):
        scope = directory.name
        if result.get("status") != "complete":
            self.record(scope + "/candidate_partition", "skipped", "scope_not_complete")
            return
        frozen = self.read(directory / "frozen-candidates.json")
        raw = self.raw.get((scope, "candidates"))
        if frozen is None or raw is None:
            self.record(
                scope + "/candidate_partition",
                "missing",
                "frozen_or_raw_candidates_unavailable",
            )
            return
        original = raw_claims(raw)
        expected = [
            {k: row[k] for k in ("id", "subject_id", "field", "kind", "proposal")}
            for row in frozen["claims"]
        ]
        self.check(
            scope + "/raw_to_frozen_claims",
            citation_content(original) == citation_content(expected),
        )
        self.check(
            scope + "/raw_to_frozen_observations",
            citation_content(raw["observations"])
            == citation_content([o["proposal"] for o in frozen["observations"]]),
        )
        wanted = [c["id"] for c in frozen["claims"]]
        wanted += [o["id"] for o in frozen["observations"]]
        self.check(scope + "/frozen_ids_unique", len(wanted) == len(set(wanted)))
        seen, frame_rejections = [], []
        for group in ("accepted", "rejected", "unresolved"):
            for row in result[group]:
                if row.get("kind") == "frame":
                    frame_rejections.append(row["subject_id"])
                else:
                    seen.append(row.get("candidate_id"))
        observations = {o["id"]: o["proposal"] for o in frozen["observations"]}
        for row in result["observations"]:
            matches = [
                cid
                for cid, proposal in observations.items()
                if proposal == row and cid not in seen
            ]
            if len(matches) != 1:
                self.record(
                    scope + "/retained_observation_id",
                    "failed",
                    {
                        "reason": "missing_or_ambiguous_frozen_match",
                        "possible_ids": matches,
                    },
                )
            else:
                seen.append(matches[0])
        self.check(
            scope + "/candidate_partition",
            Counter(seen) == Counter(wanted) and len(seen) == len(set(seen)),
            {
                "missing": list((Counter(wanted) - Counter(seen)).elements()),
                "extra": list((Counter(seen) - Counter(wanted)).elements()),
            },
        )
        self.check(
            scope + "/declared_candidate_counts",
            result.get("proposed_claim_count") == len(frozen["claims"])
            and result.get("proposed_observation_count") == len(frozen["observations"])
            and result.get("proposed_subject_count") == len(frozen["subjects"]) - 1,
        )
        self.check(
            scope + "/active_frames",
            result.get("frames")
            == {
                sid: row for sid, row in frozen["subjects"].items() if sid != "document"
            },
        )
        nonempty = [
            sid
            for sid, row in raw["subjects"].items()
            if sid != "document" and any(row["anchor"].values())
        ]
        active = [sid for sid in frozen["subjects"] if sid != "document"]
        self.check(
            scope + "/subject_partition",
            Counter(nonempty) == Counter(active + frame_rejections)
            and len(frame_rejections) == len(set(frame_rejections)),
        )
        verification = self.raw.get((scope, "verification"), {})
        self.check(
            scope + "/verification_id_coverage",
            set(verification.get("claims", {})) == {c["id"] for c in frozen["claims"]}
            and set(verification.get("observations", {})) == set(observations)
            and set(verification.get("types", {})) == set(active),
        )
        for check in result.get("metric_checks", []):
            if check.get("metric", {}).get("execution_status") == "failed":
                cid = check["candidate_id"]
                kept = [r for r in result["unresolved"] if r.get("candidate_id") == cid]
                self.check(
                    scope + "/metric_failure_preserved:" + cid,
                    len(kept) == 1
                    and kept[0].get("execution_status") == "failed"
                    and kept[0].get("validation_status") == "incomplete",
                )

    def run(self, root, ner):
        manifest = self.read(root / "result.json")
        if manifest is None:
            return
        self.manifest = manifest
        completed = manifest.get("status") == "completed"
        self.record("experiment_completed", "passed" if completed else "skipped",
                    manifest.get("status"))
        self.check("D_identity", manifest.get("arm") == "D"
                   and manifest.get("version") == "cmc-summary-retrieval-tools-v1")
        self.check("budget_configuration", manifest.get("max_model_calls") == 16
                   and manifest.get("max_attempts") == 1
                   and manifest.get("timeout_retries") == 0
                   and manifest.get("truncation_max_tokens") is None)
        self.check("evaluation_boundaries", all(manifest.get(key) is False for key in (
            "reference_is_model_input", "summary_is_fact_evidence", "summary_is_qwen_input",
            "complete_document_quality_claimed")))
        cases = self.read(root / "cases.json") or []
        scopes = [case["scope_id"] for case in cases]
        expected_scopes = {
            "introduction", "product_properties", "process_equipment", "quality_condition",
            "storage", "residue_missing", "pde_conflict", "toxicity_unknown",
        }
        self.check("eight_scopes", len(scopes) == 8 and set(scopes) == expected_scopes)
        self.check("root_input_inventory", set(manifest.get("input_hashes", {})) == {
            "source.docx", "ir.json", "cards.json", "cases.json", "vocabularies.json"})
        self.check("metadata_inventory", set(manifest.get("metadata_hashes", {})) == {
            "metadata.json", "section-tree.json", "summaries.json", "manifest.json"})
        self.check("code_inventory", {
            "schema_card_summary_tools.py", "schema_card_summary_retrieval.py", "retrieval.py",
            "metadata.py", "schema_card_tools.py", "gliner2_extractor.py",
        } <= set(manifest.get("code_hashes", {})))
        for group, directory in (("input_hashes", root), ("metadata_hashes", root / "metadata"),
                                 ("code_hashes", root / "code")):
            for name, expected in manifest.get(group, {}).items():
                self.hashed(directory / name, expected, group + "/" + name)
        self.hashed(root / "source.docx", manifest.get("source_sha256"), "source_identity")
        ir = self.read(root / "ir.json")
        meta_manifest = self.read(root / "metadata/manifest.json")
        meta = self.read(root / "metadata/metadata.json")
        if meta_manifest is not None and meta is not None and ir is not None:
            snapshot = meta["metadata_snapshot"]
            for key in ("analysis_id", "structure_hash"):
                self.check("metadata/" + key,
                           ir[key] == meta_manifest[key] == snapshot[key])
            self.check("metadata/document", ir["document_hash"] == snapshot["document_hash"]
                       == meta_manifest["source_sha256"] == manifest["source_sha256"])
            self.check("metadata/ir_file", meta_manifest["input_hashes"]["ir"]
                       == manifest["input_hashes"]["ir.json"])
            self.check("metadata/snapshot_identity", snapshot["snapshot_id"]
                       == meta_manifest["metadata_snapshot_id"]
                       and snapshot["dependency_hash"] == meta_manifest["metadata_dependency_hash"])
            for name, expected in meta_manifest["output_hashes"].items():
                self.hashed(root / "metadata" / name, expected, "metadata_inner/" + name)
            self.check("metadata/chapter_checks", len(meta_manifest["validation"]["chapter_checks"])
                       == 32 and all(row["matched"] for row in
                                     meta_manifest["validation"]["chapter_checks"]))
        if ner is not None:
            prepared = self.read(ner / "result.json")
            if prepared is not None:
                self.check("ner_reuse_identity", prepared.get("status") == "ner_completed"
                           and prepared["run_id"] == manifest.get("ner_reused_from"))
                for key in ("input_hashes", "code_hashes", "metadata_hashes", "scope_input_hashes",
                            "prepared_tool_hashes", "baseline_demand_hashes", "analysis_id",
                            "source_sha256", "packages", "gliner2_model", "ner_device",
                            "labels_per_batch", "model", "declared_model_revision"):
                    self.check("ner_manifest/" + key, prepared.get(key) == manifest.get(key))
                for name in manifest.get("input_hashes", {}):
                    self.equal_files(root / name, ner / name, "ner_root/" + name)
        root_rows = manifest.get("results", [])
        root_ids = [row.get("scope_id") for row in root_rows]
        self.check("root_scope_ids", len(root_ids) == len(set(root_ids))
                   and set(root_ids) <= expected_scopes)
        if completed:
            self.check("root_scope_coverage", set(root_ids) == expected_scopes)
        else:
            self.record("root_scope_coverage", "skipped", "run_not_complete")
        stages, scope_calls, requests, complete_count = [], [], 0, 0
        for scope in scopes:
            directory = root / "D" / scope
            frozen = manifest.get("scope_input_hashes", {}).get(scope, {})
            self.check(scope + "/input_inventory", set(frozen) == {
                "case.json", "sources.json", "demand.json", "retrieval.json", "coverage.json",
                "plan/proposal.json", "plan/schema.json", "vocabulary.json"})
            for name, expected in frozen.items():
                self.hashed(directory / name, expected, scope + "/input_hash/" + name)
                if ner is not None:
                    self.equal_files(directory / name, ner / "D" / scope / name,
                                     scope + "/ner_input/" + name)
            self.hashed(directory / "tools.json", manifest.get("prepared_tool_hashes", {}).get(scope),
                        scope + "/tools_hash")
            if ner is not None:
                self.equal_files(directory / "tools.json", ner / "D" / scope / "tools.json",
                                 scope + "/ner_tools")
            tool = self.read(directory / "tools.json")
            if tool is not None:
                self.check(scope + "/ner_completed", tool["propose_mentions"]["execution_status"]
                           in {"completed", "not_requested"})
                self.hashed(directory / "vocabulary.json",
                            tool["propose_mentions"]["vocabulary_sha256"],
                            scope + "/ner_vocabulary")
            self.check(scope + "/no_plan_http", not list((directory / "plan").glob("http-*")))
            result_path = directory / "result.json"
            result = self.read(result_path) if result_path.is_file() or completed else None
            calls, contracts = [], 0
            for stage in ("candidates", "verification"):
                ledger, passed, attempted = self.stage(directory / stage, scope, stage, result)
                calls.extend(ledger)
                contracts += passed
                requests += attempted
            stages.extend(calls)
            self.check(scope + "/call_budget", len(calls) <= 2)
            if result is None:
                self.record(scope + "/scope_result", "skipped", "scope_not_finalized")
                continue
            self.check(scope + "/identity", result.get("arm") == "D"
                       and result.get("scope_id") == scope and result.get("refs_retrieved") is True
                       and result.get("class_slots_reused") is True
                       and result.get("plan_reused") is False)
            self.check(scope + "/ledger", result.get("calls") == calls)
            self.check(scope + "/contract_passes", result.get("contract_passes") == contracts)
            scope_calls.extend(result.get("calls", []))
            root_row = next((row for row in root_rows if row.get("scope_id") == scope), None)
            self.check(scope + "/root_copy", root_row == result)
            complete_count += result.get("status") == "complete"
            if result.get("status") == "complete":
                self.check(scope + "/complete_state", len(calls) == contracts == 2
                           and not any(k in result for k in ("error_type", "failure_stage")))
            else:
                self.check(scope + "/failure_state", result.get("status") == "failed"
                           and bool(result.get("error_type")) and bool(result.get("failure_stage")))
                self.record(scope + "/model_validation_complete", "skipped", "scope_failed")
            self.disposition(directory, result)
        root_calls = [call for row in root_rows for call in row.get("calls", [])]
        self.check("total_call_budget", requests <= 16 and len(stages) <= 16)
        if completed:
            self.check("three_level_ledgers", stages == scope_calls == root_calls)
            self.record("all_scopes_validated", "passed" if complete_count == 8 else "skipped",
                        {"completed": complete_count, "expected": 8})
            summary = self.read(root / "summary.json")
            self.check("summary_copy", summary == manifest.get("summary"))
            if summary is not None:
                expected = {
                    "scopes": len(root_rows), "completed": complete_count,
                    "new_http_calls": len(root_calls),
                    "contracts_passed": sum(row["contract_passes"] for row in root_rows),
                    "accepted_candidates": sum(len(row["accepted"]) for row in root_rows),
                    "observations": sum(len(row["observations"]) for row in root_rows),
                    "unresolved": sum(len(row["unresolved"]) for row in root_rows),
                    "rejected": sum(len(row["rejected"]) for row in root_rows),
                    "prompt_tokens": sum(call.get("usage", {}).get("prompt_tokens", 0)
                                         for call in root_calls),
                    "completion_tokens": sum(call.get("usage", {}).get("completion_tokens", 0)
                                             for call in root_calls),
                    "usage_reported_calls": sum(bool(call.get("usage")) for call in root_calls),
                    "usage_missing_calls": sum(not call.get("usage") for call in root_calls),
                }
                self.check("summary_counts", all(summary.get(k) == v for k, v in expected.items()),
                           expected)
                self.check("D_not_old_silver_scored", summary.get("quality_scoring")
                           == "not_performed_on_old_scope_silver"
                           and "local_checklists_passed" not in summary)
        else:
            self.record("three_level_ledgers", "skipped", "run_not_complete")
        self.counts = {"http_request_files": requests, "stage_calls": len(stages),
                       "scope_calls": len(scope_calls), "root_calls": len(root_calls),
                       "completed_scopes": complete_count, "expected_scopes": 8}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--ner", type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    ner = args.ner.resolve() if args.ner else None
    audit = Audit()
    try:
        audit.run(root, ner)
    except Exception as exc:
        audit.record("audit_exception", "failed", type(exc).__name__ + ": " + str(exc))
    counts = Counter(row["status"] for row in audit.checks)
    required = [row for row in audit.checks if row["required"]]
    status = ("failed" if any(row["status"] == "failed" for row in required) else
              "incomplete" if any(row["status"] != "passed" for row in required) else "passed")
    failed = [row["check"] for row in required if row["status"] == "failed"]
    model_contract_failures = [name for name in failed if name.endswith(
        ("/raw_http_schema", "/completion_finish"))]
    artifact_failures = [name for name in failed if name not in model_contract_failures]
    print(json.dumps({
        "audit": "independent_D_summary_retrieval_artifact_validation_v1",
        "schema_validator": {"package": "jsonschema", "version": version("jsonschema")},
        "target": str(root), "ner": str(ner) if ner else None, "status": status,
        "check_counts": dict(counts), "call_counts": getattr(audit, "counts", {}),
        "technical_failures": audit.technical_failures, "checks": audit.checks,
        "failure_classification": {
            "model_response_contract_failed_checks": model_contract_failures,
            "artifact_identity_ledger_or_preservation_failed_checks": artifact_failures,
            "meaning": "A recorded model contract failure remains a failed check and keeps "
            "the overall audit failed; it is not relabeled as artifact corruption or success.",
        },
        "quality_evaluation": "not_performed_by_this_consistency_audit",
    }, ensure_ascii=False, indent=2))
    return {"passed": 0, "failed": 1, "incomplete": 2}[status]


if __name__ == "__main__":
    raise SystemExit(main())
