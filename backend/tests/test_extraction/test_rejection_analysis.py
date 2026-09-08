"""Recorded rejections, protocol failures and pending work stay distinct."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app.evaluation.rejection_analysis import (
    analyze_rejection_artifacts,
    analyze_rejection_run,
    main,
)


def fixture():
    run = {"completion": "incomplete", "tasks": [
        {"task": {"task_id": "a"}, "status": "complete"},
        {"task": {"task_id": "b"}, "status": "complete"},
    ]}
    checkpoint = {"coverage": [
        {"subject_id": "product", "predicate_iri": "api", "record_id": "r1",
         "task_ids": ["a"], "retrieval_phase": 1},
        {"subject_id": "product", "predicate_iri": "api", "record_id": "r2",
         "task_ids": ["b"], "retrieval_phase": 2},
    ], "queue": []}
    traces = [
        {"task_id": "a", "stage": "verify_entity_types", "response": {"decisions": [
            {"candidate_id": "c0", "supported": False, "reason": "not an API"}]}},
        {"task_id": "b", "stage": "recall", "response": {"entities": []}},
    ]
    return run, checkpoint, traces


class RejectionAnalysisTests(unittest.TestCase):
    def test_rejected_type_continues_to_different_record_and_phase(self):
        run, checkpoint, traces = fixture()
        before = deepcopy((run, checkpoint, traces))
        report = analyze_rejection_artifacts(run, checkpoint, traces)
        event = report["events"][0]
        self.assertEqual(event["context"]["locations"][0]["predicate_iri"], "api")
        continuation = event["continuation"]
        self.assertEqual(continuation["status"], "same_subject_predicate_model_call_observed")
        self.assertEqual(continuation["witness"]["context"]["locations"][0]["retrieval_phase"], 2)
        self.assertEqual((run, checkpoint, traces), before)
        json.dumps(report)

    def test_pending_queue_does_not_prove_continuation(self):
        run, checkpoint, traces = fixture()
        run["tasks"] = run["tasks"][:1]
        checkpoint["queue"] = [checkpoint["coverage"][1]]
        report = analyze_rejection_artifacts(run, checkpoint, traces[:1])
        continuation = report["events"][0]["continuation"]
        self.assertEqual(continuation["status"], "pending_only")
        self.assertFalse(continuation["continued_with_model_call"])
        self.assertFalse(continuation["later_record_attempted"])
        self.assertEqual(continuation["pending_same_subject_predicate_count"], 1)

    def test_type_binding_reference_and_whole_model_refusal_are_distinct(self):
        traces = [
            {"stage": "recall", "response": {"entities": [{"supported": False}]}},
            {"stage": "verify_binding", "response": {"supported": False,
                                                       "refusal_reason": "not bound"}},
            {"stage": "verify_reference", "response": {"supported": False, "reason": "no bridge"}},
            {"stage": "recall", "response": {"entities": [], "refusal_reason": "cannot handle"}},
            {"stage": "recall", "response": {"assertions": []}},
        ]
        report = analyze_rejection_artifacts({}, {}, traces)
        self.assertEqual(report["response_event_counts"], {
            "type_rejection": 1, "binding_rejection": 1, "reference_rejection": 1,
            "model_refusal": 1, "no_candidates": 1,
        })
        self.assertEqual(report["events"][0]["reason_status"], "missing")
        self.assertIsNone(report["events"][0]["reason"])
        self.assertEqual(report["events"][1]["reason"], "not bound")

    def test_identity_false_with_type_true_is_not_a_type_rejection(self):
        traces = [{"stage": "verify_entity_types", "response": {"decisions": [
            {"candidate_id": "c0", "supported": True, "identity_supported": False, "reason": "API"},
            {"candidate_id": "c1", "supported": True, "reason": "API"},
        ]}}]
        report = analyze_rejection_artifacts({}, {}, traces)
        self.assertEqual(report["response_event_counts"], {"identity_not_supported": 1})
        self.assertEqual(report["identity_observations"][1]["identity_status"], "not_returned")
        self.assertIn("not_identity_specific", report["events"][0]["reason_scope"])

    def test_failures_deduplicate_task_mirrors_and_do_not_invent_responses(self):
        run, checkpoint, traces = fixture()
        run["tasks"][1].update(issues=["budget_exceeded", "non_atomic_source_citation"], outcomes=[
            {"code": "budget_exceeded", "stage": "verify_entity_types"},
            {"code": "non_atomic_source_citation", "stage": "recall"},
            {"code": "model_refusal", "stage": "recall"},
        ])
        report = analyze_rejection_artifacts(run, checkpoint, traces[:1])
        self.assertEqual(report["task_failure_counts"],
                         {"budget_failure": 1, "protocol_failure": 1})
        self.assertEqual(report["task_semantic_code_counts"], {"model_refusal": 1})
        self.assertNotIn("model_refusal", report["response_event_counts"])
        continuation = report["events"][0]["continuation"]
        self.assertEqual(continuation["status"], "same_subject_predicate_attempt_only_observed")
        self.assertFalse(continuation["continued_with_model_call"])

    def test_binding_failure_code_and_unknown_transport_error_stay_distinct(self):
        run = {"tasks": [
            {"task": {"task_id": "a"}, "issues": ["cooccurrence_only"], "outcomes": [
                {"code": "cooccurrence_only", "stage": "property_binding"}]},
            {"task": {"task_id": "b"}, "issues": ["ReadTimeout"]},
        ]}
        report = analyze_rejection_artifacts(run, {}, [{
            "task_id": "a", "stage": "verify_binding", "response": {"supported": False},
        }])
        self.assertEqual(report["response_event_counts"], {"binding_rejection": 1})
        self.assertEqual(report["task_semantic_code_counts"], {"cooccurrence_only": 1})
        self.assertEqual(report["task_failure_counts"], {"other_validation_failure": 1})
        self.assertEqual(report["task_failures"][0]["code"], "ReadTimeout")

    def test_same_record_next_validation_stage_does_not_count_as_continuation(self):
        run, checkpoint, traces = fixture()
        traces[1]["task_id"] = "a"
        run["tasks"] = run["tasks"][:1]
        report = analyze_rejection_artifacts(run, checkpoint, traces)
        self.assertEqual(report["events"][0]["continuation"]["status"], "no_later_record_observed")

    def test_routing_call_is_not_record_extraction(self):
        run, checkpoint, traces = fixture()
        run["tasks"] = run["tasks"][:1]
        traces[1]["stage"] = "route_records"
        report = analyze_rejection_artifacts(run, checkpoint, traces)
        self.assertFalse(report["events"][0]["continuation"]["later_record_attempted"])
        self.assertEqual(report["response_event_counts"], {"type_rejection": 1})

    def test_unrelated_predicate_continuation_is_not_claimed_for_original_slot(self):
        run, checkpoint, traces = fixture()
        checkpoint["coverage"][1]["predicate_iri"] = "appearance"
        report = analyze_rejection_artifacts(run, checkpoint, traces)
        continuation = report["events"][0]["continuation"]
        self.assertEqual(continuation["status"], "other_subject_or_predicate_model_call_observed")
        self.assertFalse(continuation["same_subject_predicate_continued"])

    def test_prefers_same_subject_predicate_even_if_other_record_happened_first(self):
        run, checkpoint, traces = fixture()
        checkpoint["coverage"].append({"task_ids": ["x"], "subject_id": "different",
                                       "predicate_iri": "other", "record_id": "r3"})
        traces.insert(1, {"task_id": "x", "stage": "recall", "response": {"entities": []}})
        report = analyze_rejection_artifacts(run, checkpoint, traces)
        self.assertEqual(report["events"][0]["continuation"]["witness"]["task_id"], "b")

    def test_ambiguous_coverage_is_not_guessed(self):
        run, checkpoint, traces = fixture()
        checkpoint["coverage"][1]["task_ids"].append("a")
        report = analyze_rejection_artifacts(run, checkpoint, traces)
        self.assertEqual(report["events"][0]["context"]["status"], "ambiguous")
        self.assertEqual(report["events"][0]["continuation"]["status"], "unavailable_context")

    def test_malformed_response_is_not_interpreted_as_empty_or_refusal(self):
        report = analyze_rejection_artifacts({}, {}, [{"stage": "recall", "response": "refused!"}])
        self.assertEqual(report["response_event_counts"], {})
        self.assertEqual(len(report["unreadable_responses"]), 1)

    def test_missing_binding_reason_and_local_aliases_remain_explicit(self):
        report = analyze_rejection_artifacts({}, {}, [
            {"task_id": "a", "stage": "verify_binding", "response": {
                "supported": False, "subject_candidate_id": "c0"}},
            {"task_id": "b", "stage": "verify_binding", "response": {
                "supported": False, "subject_candidate_id": "c0"}},
        ])
        first, second = report["events"]
        self.assertEqual(first["reason_status"], "missing")
        self.assertNotEqual(first["reference_alias_scope"], second["reference_alias_scope"])

    def test_refusal_and_partial_refusal_are_not_empty_recall(self):
        report = analyze_rejection_artifacts({}, {}, [
            {"stage": "recall", "response": {"refusal": "cannot process"}},
            {"stage": "recall", "response": {"entities": [{"supported": True}],
                                               "refusal_reason": "remaining omitted"}},
        ])
        self.assertEqual(report["response_event_counts"], {
            "model_refusal": 1, "model_partial_refusal": 1,
        })

    def test_active_marker_rejected_before_reading_any_artifacts_and_cli_outputs_no_json(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "run.in_progress.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "active run"):
                analyze_rejection_run(directory)
            with patch("sys.argv", ["rejection_analysis", "--run", directory]), \
                 patch("sys.stdout") as output, patch("sys.stderr"), self.assertRaises(SystemExit):
                main()
            output.write.assert_not_called()

    def test_finalized_incomplete_run_is_read_only_and_hashed(self):
        run, checkpoint, traces = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = {"run.json": json.dumps(run).encode(),
                   "checkpoint.json": json.dumps(checkpoint).encode(),
                   "trace.jsonl": "\n".join(json.dumps(t) for t in traces).encode()}
            for name, value in raw.items():
                (root / name).write_bytes(value)
            report = analyze_rejection_run(root)
            self.assertEqual(report["completion"], "incomplete")
            self.assertEqual(report["artifact_sha256"], {
                name: hashlib.sha256(value).hexdigest() for name, value in raw.items()
            })
            self.assertEqual({name: (root / name).read_bytes() for name in raw}, raw)
            self.assertEqual(report["artifact_identity_checks"]["input_id"]["status"],
                             "unavailable_missing_field")

    def test_mixed_run_checkpoint_identity_or_version_is_rejected_before_analysis(self):
        for key in ("input_id", "scheduler_version", "transport_version"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "run.json").write_text(json.dumps({
                    "completion": "incomplete", key: "first",
                }), encoding="utf-8")
                (root / "checkpoint.json").write_text(json.dumps({key: "second"}),
                                                      encoding="utf-8")
                (root / "trace.jsonl").write_text("", encoding="utf-8")
                with patch(
                    "app.evaluation.rejection_analysis.analyze_rejection_artifacts",
                ) as audit:
                    with self.assertRaisesRegex(ValueError, key + " differ"):
                        analyze_rejection_run(root)
                    audit.assert_not_called()

    def test_matching_run_checkpoint_identity_and_versions_are_reported(self):
        identity = {"input_id": "same", "scheduler_version": "v1", "transport_version": "v2"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run.json").write_text(json.dumps({
                "completion": "incomplete", **identity,
            }), encoding="utf-8")
            (root / "checkpoint.json").write_text(json.dumps(identity), encoding="utf-8")
            (root / "trace.jsonl").write_text("", encoding="utf-8")
            report = analyze_rejection_run(root)
            self.assertTrue(all(value["status"] == "matched"
                                for value in report["artifact_identity_checks"].values()))


if __name__ == "__main__":
    unittest.main()
