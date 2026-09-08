"""Offline quality reporting never upgrades coverage or validation into accuracy."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.evaluation.quality_analysis import (
    analyze_quality_artifacts,
    analyze_quality_run,
    audit_coverage,
    audit_merges,
    audit_references,
    audit_task_failures,
    property_review_queue,
    schema_reachability,
    summarize_quality_calls,
)

from .test_root_guided_analysis import fixture


class QualityAnalysisTests(unittest.TestCase):
    def test_primary_score_excludes_root_but_root_paths_retain_it(self):
        ir, reference, entities, relationships = fixture()
        candidates = [*entities, *relationships]
        run = {"candidates": candidates, "completion": "complete"}
        before = deepcopy(run)
        report = analyze_quality_artifacts(
            run, {"completion": "complete"}, {"candidates": candidates}, {}, {}, ir, reference,
            snapshots=[{"elapsed_seconds": 1, "candidates": candidates}],
        )
        self.assertEqual(report["validated_extracted_only"]["micro"]["tp"], 4)
        self.assertEqual(report["root_paths"]["matched_reachable_entity_count"], 2)
        self.assertTrue(report["snapshot_history"]["last_snapshot_equals_final_run"])
        self.assertEqual(run, before)
        self.assertIsNone(report["model_server_slots_unchanged"])
        json.dumps(report)

    def test_citation_issues_not_lost_or_double_counted_without_outcomes(self):
        tasks = [
            {"task": {"task_id": "a"}, "issues": ["non_atomic_source_citation"],
             "outcomes": []},
            {"task": {"task_id": "b"}, "issues": ["ambiguous_source_quote"],
             "outcomes": [{"code": "ambiguous_source_quote"}]},
        ]
        report = audit_task_failures(tasks, diagnostics=["ambiguous_source_quote"])
        self.assertEqual(report["citation_error_attempt_count"], 2)
        self.assertEqual(report["citation_error_code_counts"],
                         {"ambiguous_source_quote": 1, "non_atomic_source_citation": 1})

    def test_model_slot_changes_are_not_labeled_as_production_fact_writes(self):
        ir, reference, entities, relationships = fixture()
        run = {"candidates": [*entities, *relationships], "completion": "complete"}
        report = analyze_quality_artifacts(
            run, {"completion": "complete", "slots_before": [{"n_prompt_tokens": 1}],
                  "slots_after": [{"n_prompt_tokens": 2}]}, {}, {}, {}, ir, reference,
        )
        self.assertFalse(report["model_server_slots_unchanged"])
        self.assertNotIn("production_slots_unchanged", report)
        self.assertIn("not production facts", report["model_server_slot_note"])

    def test_unknown_closed_id_is_not_automatically_a_source_citation_error(self):
        report = audit_task_failures([{"issues": ["unknown_model_reference"]}])
        self.assertEqual(report["citation_error_attempt_count"], 0)
        self.assertEqual(report["task_attempt_code_counts"]["unknown_model_reference"], 1)

    def test_graph_revision_failures_are_distinct_from_silver_matching(self):
        _, _, entities, relationships = fixture()
        relationships[0]["object"]["revision"] = 2
        report = audit_references([*entities, *relationships])
        self.assertEqual(report["by_reason"], {"stale_candidate_revision": 1})
        self.assertEqual(report["failures"][0]["field"], "object")

    def test_empty_queue_and_some_routed_predicates_are_not_full_coverage(self):
        checkpoint = {"queue": [], "coverage": [
            {"subject_id": "root", "predicate_iri": "p1", "record_id": "r1",
             "status": "examined"},
        ], "route_cache": {"route": {"ledger": [
            {"record_id": "r1", "status": "routed", "predicate_iris": ["p1"]},
            {"record_id": "r2", "status": "route_negative_not_extracted", "predicate_iris": []},
        ]}}}
        candidates = [{"candidate_id": "root", "class_iri": "Root", "kind": "entity",
                       "validation_status": "passed"}]
        schema = {"Root": {"relationships": [{"iri": "p1"}, {"iri": "p2"}]}}
        report = audit_coverage(checkpoint, {}, candidates, schema)
        self.assertEqual(report["expected_instance_predicate_record_count"], 4)
        self.assertEqual(report["unexamined_instance_predicate_record_count"], 3)
        self.assertFalse(report["all_recorded_instance_slots_examined"])
        self.assertFalse(report["full_document_exhaustive_completion_demonstrated"])

    def test_schema_closure_handles_cycles_and_subclasses_without_domain_constants(self):
        schema = {
            "Root": {"relationships": [{"iri": "p", "range": ["Base"]}]},
            "Base": {"properties": [{"iri": "x"}]},
            "Child": {"parents": ["Base"], "relationships": [{"iri": "q", "range": ["Root"]}]},
            "Unreachable": {},
        }
        report = schema_reachability(schema, ["Root"])
        self.assertEqual(report["reachable_class_iris"], ["Base", "Child", "Root"])
        self.assertEqual(report["legal_class_relationship_triples"], 3)

    def test_merge_conflicts_and_mention_revisions_are_deduplicated(self):
        event = {"alias_map": {"b": "a"},
                 "conflicts": [{"code": "conflicting_instance_iris", "candidate_ids": ["a", "c"]}],
                 "mentions": {"a": [{"candidate_id": "a", "revision": 1}]}}
        report = audit_merges({"merge_log": [event, event], "aliases": {"b": "a"}}, {})
        self.assertEqual(report["merged_source_id_count"], 1)
        self.assertEqual(report["original_mention_revision_count"], 1)
        self.assertEqual(len(report["unique_identity_conflicts"]), 1)

    def test_conflicting_values_keep_units_conditions_and_sources_for_review(self):
        candidates = [{"candidate_id": f"p{index}", "kind": "property",
                       "subject": {"candidate_id": "a"}, "predicate_iri": "p",
                       "assertion_status": polarity,
                       "literal": {"kind": "number", "normalized_value": value,
                                   "canonical_unit": "mg"}, "provenance": [{"note": value}]}
                      for index, value, polarity in ((1, "10", "affirmed"),
                                                     (2, "20", "conditional"))]
        report = property_review_queue(candidates)
        self.assertEqual(len(report["groups"]), 1)
        self.assertEqual(report["groups"][0]["distinct_value_context_count"], 2)
        self.assertEqual(report["groups"][0]["candidates"][1]["assertion_status"], "conditional")
        self.assertEqual(report["groups"][0]["candidates"][0]["literal"]["canonical_unit"], "mg")

    def test_resumed_snapshot_clock_does_not_produce_invented_first_time(self):
        ir, reference, entities, relationships = fixture()
        candidates = [*entities, *relationships]
        report = analyze_quality_artifacts(
            {"candidates": candidates, "completion": "incomplete"},
            {"completion": "incomplete", "elapsed_seconds_total": 20,
             "elapsed_seconds_this_segment": 10}, {}, {}, {}, ir, reference,
            snapshots=[{"elapsed_seconds": 10, "candidates": candidates},
                       {"elapsed_seconds": 1, "candidates": candidates}],
        )
        self.assertEqual(report["snapshot_history"]["status"], "unavailable")
        self.assertIsNone(report["snapshot_history"]["first_observed_reference_correct_root_path_seconds"])

    def test_active_run_is_rejected_before_reading_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "run.in_progress.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "active run"):
                analyze_quality_run(directory, directory, reference_path="not-read.json")

    def test_duplicate_ids_are_reported_even_with_no_incoming_reference(self):
        _, _, entities, _ = fixture()
        report = audit_references([entities[1], entities[1]])
        self.assertEqual(report["by_reason"], {"duplicate_current_candidate_id": 1})

    def test_binding_subject_predicate_and_provenance_must_match_owner(self):
        _, _, entities, relationships = fixture()
        relationships[0]["bindings"] = [{"subject_candidate_id": "wrong",
                                         "predicate_iri": "wrong", "object_candidate_id": "a",
                                         "provenance_indexes": [999]}]
        report = audit_references([*entities, *relationships])
        self.assertEqual(report["by_reason"], {"binding_metadata_mismatch": 2,
                                              "binding_provenance_index_invalid": 1})

    def test_routing_menu_and_source_statistics_are_not_falsely_zero(self):
        traces = [{"stage": "route_records", "model_call_number": 1, "input_tokens": 12,
                   "user": json.dumps({"local_menu": {"p0": {}, "p1": {}},
                                       "records": {"r0": {"source": ["abc", "de"]}}}),
                   "response": {"records": []}}]
        report = summarize_quality_calls(traces, [{"call": 1, "wall_seconds": 2}])
        row = report["calls"][0]
        self.assertEqual(row["distinct_menu_predicate_count"], 2)
        self.assertEqual(row["source_record_count"], 1)
        self.assertEqual(row["source_fragment_count"], 2)
        self.assertEqual(row["source_characters"], 5)
        self.assertNotIn("target_fragment_count", row)
        self.assertEqual(report["by_stage"]["retrieval.route_records"]["input_tokens_sum"], 12)

    def test_swapped_summary_is_rejected_before_reference_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, value):
                data = json.dumps(value).encode()
                (root / name).write_bytes(data)
                return hashlib.sha256(data).hexdigest()
            ir_hash = write("ir.json", {"document_hash": "doc"})
            schema_hash = write("schema.json", {})
            summary_hash = write("summaries.json", {"summary": "frozen"})
            identity = {"document_hash": "doc", "runtime_hash": "runtime", "ontology_hash": "owl"}
            write("manifest.json", {**identity, "settings": {}, "ir_hash": ir_hash,
                                    "schema_hash": schema_hash, "summary_hash": summary_hash})
            write("result.json", {**identity, "model_settings": {}, "input_hashes": {
                "ir": ir_hash, "schema": schema_hash, "summaries": summary_hash,
            }})
            for name in ("run.json", "checkpoint.json", "plan.json"):
                write(name, {})
            write("summaries.json", {"summary": "swapped"})
            with self.assertRaisesRegex(ValueError, "summary hash differs"):
                analyze_quality_run(root, root, reference_path="not-read.json")


if __name__ == "__main__":
    unittest.main()
