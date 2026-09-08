"""An observed correct root path needs connected candidates and publication history."""

import unittest
from copy import deepcopy

from app.evaluation.graph_metrics import evaluate_graph
from app.evaluation.root_guided_analysis import root_paths, score_snapshot_history, summarize_calls


def fixture():
    ir = {"document_hash": "doc", "parser_version": "v1", "structure_hash": "structure",
          "evidence_units": [{"evidence_id": "u1", "text": "Report A B", "section_node_id": "s1",
                              "block_id": "b1", "paragraph_index": 0, "fragment_index": 0}]}
    source = [{"kind": "document", "anchors": [{"document_hash": "doc", "parser_version": "v1",
               "structure_hash": "structure", "evidence_id": "u1", "section_node_id": "s1",
               "block_id": "b1", "paragraph_index": 0, "fragment_index": 0,
               "span_start": 0, "span_end": 10}]}]
    entities = [{"candidate_id": key, "revision": 1, "kind": "entity", "text": text,
                 "class_iri": cls, "validation_status": "passed", "provenance": source}
                for key, text, cls in (("root", "Report", "urn:Report"),
                                       ("a", "A", "urn:Equipment"),
                                       ("b", "B", "urn:Equipment"))]
    entities[0]["identity"] = {"document_root": "doc"}
    relationships = [{"candidate_id": key, "kind": "relationship", "revision": 1,
                      "subject": {"candidate_id": subject, "revision": 1},
                      "object": {"candidate_id": obj, "revision": 1},
                      "predicate_iri": predicate, "validation_status": "passed",
                      "provenance": source}
                     for key, subject, obj, predicate in
                     (("r1", "root", "a", "urn:uses"), ("r2", "a", "b", "urn:contains"))]
    reference = {"schema_version": 1, "document_hash": "doc",
                 "annotation_level": "assistant_silver",
                 "entities": [{"id": c["candidate_id"], "class_iri": c["class_iri"],
                               "aliases": [c["text"]], "evidence": [{"evidence_id": "u1"}],
                               **({"is_document_root": True}
                                  if c["candidate_id"] == "root" else {})}
                              for c in entities],
                 "relationships": [{"id": c["candidate_id"], "predicate_iri": c["predicate_iri"],
                                    "subject": c["subject"]["candidate_id"],
                                    "object": c["object"]["candidate_id"],
                                    "evidence": [{"evidence_id": "u1"}]} for c in relationships],
                 "properties": [], "forbidden": [], "scopes": []}
    return ir, reference, entities, relationships


class RootGuidedAnalysisTests(unittest.TestCase):
    def test_unconnected_correct_downstream_edge_does_not_make_a_root_path(self):
        ir, reference, entities, relationships = fixture()
        candidates = [*entities, relationships[1]]
        metrics = evaluate_graph({"candidates": candidates}, reference, ir=ir)
        self.assertEqual(metrics["validated"]["relationship"]["tp"], 1)
        paths = root_paths(metrics, reference, candidates)
        self.assertEqual(paths["reference_reachable_entity_count"], 2)
        self.assertEqual(paths["matched_reachable_entity_count"], 0)

    def test_first_path_uses_snapshot_with_passed_connected_relationship(self):
        ir, reference, entities, relationships = fixture()
        pending = {**relationships[0], "validation_status": "pending"}
        snapshots = [
            {"elapsed_seconds": 1, "candidates": entities},
            {"elapsed_seconds": 2, "candidates": [*entities, pending]},
            {"elapsed_seconds": 3, "candidates": [*entities, *relationships]},
        ]
        history = score_snapshot_history(snapshots, reference, ir)
        self.assertEqual(history["first_observed_reference_correct_root_path_seconds"], 3)
        paths = history["first_observation"]["paths"]
        self.assertEqual(paths[1]["relationship_reference_ids"], ["r1", "r2"])
        self.assertEqual(history["timeline"][0]["validated_extracted_only_micro"]["tp"], 2)

    def test_duplicate_endpoint_candidates_are_not_merged_into_a_path(self):
        ir, reference, entities, relationships = fixture()
        duplicate = {**entities[1], "candidate_id": "copy"}
        edge = deepcopy(relationships[0])
        edge["object"]["candidate_id"] = "copy"
        candidates = [*entities, duplicate, edge]
        metrics = evaluate_graph({"candidates": candidates}, reference, ir=ir)
        # Exact entity matching selects one copy; a separate relation can still
        # score through its equivalent endpoint. Path audit must expose that gap.
        selected = next(m["candidate_id"] for m in metrics["validated"]["entity"]["matched"]
                        if m["reference_id"] == "a")
        edge["object"]["candidate_id"] = "a" if selected == "copy" else "copy"
        metrics = evaluate_graph({"candidates": candidates}, reference, ir=ir)
        self.assertEqual(metrics["validated"]["relationship"]["tp"], 1)
        paths = root_paths(metrics, reference, candidates)
        self.assertEqual(paths["matched_reachable_entity_count"], 0)
        self.assertEqual(paths["matched_edges_excluded_for_endpoint_candidate_mismatch"], ["r1"])

    def test_missing_history_has_no_inferred_first_time(self):
        ir, reference, _, _ = fixture()
        history = score_snapshot_history([], reference, ir)
        self.assertEqual(history["status"], "unavailable")
        self.assertIsNone(history["first_observed_reference_correct_root_path_seconds"])

    def test_invalid_history_times_fail_closed(self):
        ir, reference, entities, _ = fixture()
        for times in ((2, 1), (1, float("nan")), (1, -1)):
            with self.assertRaisesRegex(ValueError, "snapshot elapsed_seconds"):
                score_snapshot_history([{"elapsed_seconds": t, "candidates": entities}
                                        for t in times], reference, ir)

    def test_failed_call_does_not_shift_trace_alignment(self):
        calls = [{"call": 1, "wall_seconds": 20, "error": "TimeoutError"},
                 {"call": 2, "wall_seconds": 5}]
        trace = {"stage": "recall", "input_tokens": 99, "user": {"context": {
            "task": {"task_kind": "entity", "predicate_definition": {"classes": {
                "t1": {"properties": [{"iri": "urn:p"}]}, "t2": {}}}},
            "fragments": []}}, "response": {"entities": [{"mention": "A"}]}}
        result = summarize_calls([trace], calls)
        self.assertIsNone(result["calls"][0]["call_wall_seconds"])
        self.assertFalse(result["input_token_measurements_cover_all_logged_calls"])
        trace["model_call_number"] = 2
        result = summarize_calls([trace], calls)
        self.assertEqual(result["calls"][0]["call_wall_seconds"], 5)
        self.assertEqual(result["calls"][0]["class_menu_count"], 2)
        self.assertEqual(result["calls"][0]["distinct_menu_predicate_count"], 1)
        self.assertEqual(result["calls_without_aligned_trace"], [1])
        self.assertIsNone(result["output_token_count"])


if __name__ == "__main__":
    unittest.main()
