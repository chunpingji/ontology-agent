#!/usr/bin/env python3
"""Read-only GLiNER2.5/Qwen artifact audit; no application or model imports.

Usage: python check-artifacts.py TARGET [--ner NER] [--baseline B]
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

    def run(self, root, ner, baseline):
        manifest = self.read(root / "result.json")
        if manifest is None:
            return
        self.manifest = manifest
        self.record(
            "experiment_completed",
            "passed" if manifest.get("status") == "completed" else "skipped",
            manifest.get("status"),
        )
        cases = self.read(root / "cases.json") or []
        scopes = [case["scope_id"] for case in cases]
        self.check(
            "frozen_scopes", len(scopes) == 8 and len(scopes) == len(set(scopes))
        )
        self.check(
            "budget_configuration",
            manifest.get("max_model_calls") == 16
            and manifest.get("max_attempts") == 1
            and manifest.get("timeout_retries") == 0,
        )
        self.hashed(
            root / "source.docx", manifest.get("source_sha256"), "source_document_hash"
        )
        for group, prefix in (("input_hashes", root), ("code_hashes", root / "code")):
            self.check(group + "/nonempty", bool(manifest.get(group)))
            for name, expected in manifest.get(group, {}).items():
                self.hashed(prefix / name, expected, group + "/" + name)
        self.hashed(
            root / "vocabularies.json",
            manifest.get("vocabulary_sha256"),
            "vocabulary_hash",
        )
        previous = self.read(ner / "result.json")
        old = self.read(baseline / "result.json")
        if previous is not None:
            self.check(
                "ner_identity",
                previous.get("status") == "ner_completed"
                and previous.get("run_id") == manifest.get("ner_reused_from"),
            )
            for key in (
                "source_sha256",
                "ontology_hash",
                "input_hashes",
                "code_hashes",
                "gliner2_model",
                "ner_device",
                "labels_per_batch",
                "version",
                "backend",
                "architecture",
                "packages",
                "prepared_tool_hashes",
                "vocabulary_sha256",
            ):
                self.check(
                    "ner_manifest/" + key, previous.get(key) == manifest.get(key)
                )
            for name in [
                "source.docx",
                "vocabularies.json",
                *manifest.get("input_hashes", {}),
            ]:
                self.equal_files(root / name, ner / name, "ner_file/" + name)
            for name in manifest.get("code_hashes", {}):
                self.equal_files(
                    root / "code" / name, ner / "code" / name, "ner_code/" + name
                )
        if old is not None:
            self.check(
                "baseline_identity",
                old.get("run_id") == manifest.get("baseline_run_id")
                and old.get("status") == "completed",
            )
            for name in ["source.docx", *manifest.get("input_hashes", {})]:
                self.equal_files(root / name, baseline / name, "baseline_input/" + name)
        all_stage_calls, all_scope_calls, request_count, scopes_complete = [], [], 0, 0
        root_rows = manifest.get("results", [])
        root_ids = [row.get("scope_id") for row in root_rows]
        self.check(
            "root_scope_ids",
            len(root_ids) == len(set(root_ids)) and set(root_ids) <= set(scopes),
        )
        if manifest.get("status") == "completed":
            self.check("root_scope_coverage", set(root_ids) == set(scopes))
        else:
            self.record(
                "root_scope_coverage",
                "skipped",
                {"pending": sorted(set(scopes) - set(root_ids))},
            )
        for scope in scopes:
            directory = root / "C" / scope
            for name, expected in (
                manifest.get("prepared_tool_hashes", {}).get(scope, {}).items()
            ):
                self.hashed(
                    directory / name, expected, scope + "/prepared_hash/" + name
                )
                self.equal_files(
                    directory / name,
                    ner / "C" / scope / name,
                    scope + "/ner_file/" + name,
                )
            self.check(
                scope + "/prepared_inventory",
                set(manifest.get("prepared_tool_hashes", {}).get(scope, {}))
                == {
                    "sources.json",
                    "plan/proposal.json",
                    "plan/schema.json",
                    "vocabulary.json",
                    "tools.json",
                },
            )
            for name in ("sources.json", "plan/proposal.json", "plan/schema.json"):
                self.equal_files(
                    directory / name,
                    baseline / "B" / scope / name,
                    scope + "/baseline/" + name,
                )
            provenance = self.read(directory / "plan/provenance.json")
            if provenance is not None:
                self.check(
                    scope + "/plan_provenance",
                    provenance.get("arm") == "B"
                    and provenance.get("scope_id") == scope
                    and provenance.get("new_model_calls") == 0,
                )
                self.hashed(
                    directory / "plan/proposal.json",
                    provenance.get("proposal_sha256"),
                    scope + "/plan_hash",
                )
            tools = self.read(directory / "tools.json")
            if tools is not None:
                mentions = tools["propose_mentions"]
                self.check(
                    scope + "/ner_complete",
                    mentions.get("execution_status") in {"completed", "not_requested"},
                )
                self.hashed(
                    directory / "vocabulary.json",
                    mentions.get("vocabulary_sha256"),
                    scope + "/ner_vocabulary_hash",
                )
            result = self.read(directory / "result.json")
            stage_calls, passed = [], 0
            for stage in ("candidates", "verification"):
                calls, contracts, requests = self.stage(
                    directory / stage, scope, stage, result
                )
                stage_calls.extend(calls)
                passed += contracts
                request_count += requests
            self.check(
                scope + "/no_plan_http",
                not list((directory / "plan").glob("http-*-request.json")),
            )
            self.check(scope + "/scope_call_budget", len(stage_calls) <= 2)
            all_stage_calls.extend(stage_calls)
            if result is None:
                continue
            scopes_complete += result.get("status") == "complete"
            self.check(
                scope + "/scope_identity",
                result.get("scope_id") == scope and result.get("arm") == "C",
            )
            self.check(scope + "/scope_ledger", result.get("calls") == stage_calls)
            all_scope_calls.extend(result.get("calls", []))
            root_row = next((r for r in root_rows if r.get("scope_id") == scope), None)
            if root_row is None:
                self.record(
                    scope + "/root_ledger", "missing", "scope_result_not_in_root"
                )
            else:
                self.check(scope + "/root_ledger", root_row == result)
            self.check(
                scope + "/contract_passes", result.get("contract_passes") == passed
            )
            if result.get("status") == "complete":
                self.check(
                    scope + "/complete_state",
                    passed == 2
                    and len(stage_calls) == 2
                    and not any(k in result for k in ("error_type", "failure_stage")),
                )
            else:
                self.check(
                    scope + "/failure_state",
                    result.get("status") == "failed"
                    and bool(result.get("error_type"))
                    and bool(result.get("failure_stage")),
                )
                self.record(
                    scope + "/model_validation_complete",
                    "skipped",
                    result.get("error_code", result.get("error_type")),
                )
            self.disposition(directory, result)
        root_calls = [call for row in root_rows for call in row.get("calls", [])]
        self.check(
            "total_call_budget", request_count <= 16 and len(all_stage_calls) <= 16
        )
        if manifest.get("status") == "completed":
            self.check(
                "three_level_ledgers", all_stage_calls == all_scope_calls == root_calls
            )
            self.record(
                "all_scopes_validated",
                "passed" if scopes_complete == len(scopes) else "skipped",
                {"completed": scopes_complete, "expected": len(scopes)},
            )
            summary = self.read(root / "summary.json")
            self.check("summary_snapshot", summary == manifest.get("summary"))
            if summary is not None:
                expected = {
                    "scopes": len(root_rows),
                    "completed": scopes_complete,
                    "new_http_calls": len(root_calls),
                    "contracts_passed": sum(r["contract_passes"] for r in root_rows),
                    "accepted_candidates": sum(len(r["accepted"]) for r in root_rows),
                    "observations": sum(len(r["observations"]) for r in root_rows),
                    "unresolved": sum(len(r["unresolved"]) for r in root_rows),
                    "rejected": sum(len(r["rejected"]) for r in root_rows),
                    "prompt_tokens": sum(
                        c.get("usage", {}).get("prompt_tokens", 0) for c in root_calls
                    ),
                    "completion_tokens": sum(
                        c.get("usage", {}).get("completion_tokens", 0)
                        for c in root_calls
                    ),
                }
                self.check(
                    "summary_counts",
                    all(summary.get(k) == v for k, v in expected.items()),
                    expected,
                )
        else:
            self.record("three_level_ledgers", "skipped", "root_still_running")
        self.counts = {
            "http_request_files": request_count,
            "stage_calls": len(all_stage_calls),
            "scope_calls": len(all_scope_calls),
            "root_calls": len(root_calls),
            "completed_scopes": scopes_complete,
            "expected_scopes": len(scopes),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--ner", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    root = args.target.resolve()
    ner = args.ner.resolve() if args.ner else root.parent / "ner"
    baseline = (
        args.baseline.resolve()
        if args.baseline
        else (root.parent.parent / "schema-card-qwen-cmc-tools-20260916" / "checked")
    )
    audit = Audit()
    try:
        audit.run(root, ner, baseline)
    except Exception as exc:
        audit.record("audit_exception", "failed", type(exc).__name__ + ": " + str(exc))
    counts = Counter(row["status"] for row in audit.checks)
    required = [row for row in audit.checks if row["required"]]
    status = (
        "failed"
        if any(row["status"] == "failed" for row in required)
        else (
            "incomplete"
            if any(row["status"] != "passed" for row in required)
            else "passed"
        )
    )
    print(
        json.dumps(
            {
                "audit": "independent_read_only_artifact_validation_v1",
                "schema_validator": {
                    "package": "jsonschema",
                    "version": version("jsonschema"),
                },
                "target": str(root),
                "ner": str(ner),
                "baseline": str(baseline),
                "status": status,
                "check_counts": dict(counts),
                "call_counts": getattr(audit, "counts", {}),
                "technical_failures": audit.technical_failures,
                "checks": audit.checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return {"passed": 0, "failed": 1, "incomplete": 2}[status]


if __name__ == "__main__":
    raise SystemExit(main())
