"""Reference-free export preserves assertions, not claims of semantic correctness."""

import hashlib
import json
from copy import deepcopy

import pytest

from app.evaluation.quality_graph_export import export_quality_graph, main


def ref(cid, revision=1, **changes):
    return {"candidate_id": cid, "revision": revision, **changes}


def entity(cid, **changes):
    return {
        "candidate_id": cid, "revision": 1, "kind": "entity", "class_iri": "urn:Item",
        "text": "相同名称", "identity": {}, "validation_status": "passed",
        "assertion_status": "affirmed", "review_status": "pending",
        "provenance": [{"kind": "document", "anchors": [
            {"document_hash": "doc", "evidence_id": cid}], "excerpts": ["相同名称"]}],
        "bindings": [], "dependency_refs": [], **changes,
    }


def root(**changes):
    return entity("root", class_iri="urn:Report", identity={"document_root": "doc"}, **changes)


def assertion(cid, subject="root", object_id=None, **changes):
    candidate = entity(cid, kind="relationship" if object_id else "property")
    candidate.update({
        "subject": ref(subject), "object": ref(object_id) if object_id else None,
        "predicate_iri": "urn:uses" if object_id else "urn:volume",
        "literal": None if object_id else {
            "kind": "range", "raw_value": "5–10 L", "lower": "5", "upper": "10",
            "lower_inclusive": True, "upper_inclusive": False, "operator": "eq",
            "raw_unit": "L", "canonical_unit": "L", "dimension": "volume",
            "conversion_record": {"factor": "1"},
        },
        "bindings": [{"method": "explicit_assertion", "subject_candidate_id": subject,
                      "object_candidate_id": object_id,
                      "predicate_iri": "urn:uses" if object_id else "urn:volume",
                      "provenance_indexes": [0]}],
    })
    candidate.update(changes)
    return candidate


def export(*candidates, **run_fields):
    return export_quality_graph({"candidates": list(candidates), "completion": "incomplete",
                                 **run_fields})


def ids(graph, kind):
    return {candidate["candidate_id"] for candidate in graph[kind]}


def test_full_graph_preserves_complete_fields_and_does_not_mutate_input():
    prop = assertion("volume", applicable_at="2026-09-08", confidence=0.73,
                     validation_issues=[{"code": "recorded_note"}])
    original = {"candidates": [root(), prop], "completion": "incomplete"}
    before = deepcopy(original)
    result = export_quality_graph(original)
    assert original == before
    exported = result["full_validated_candidate_graph"]["properties"][0]
    assert {key: value for key, value in exported.items() if key != "graph_export"} == prop
    assert result["root_reachable_positive_graph"]["counts"] == {
        "nodes": 1, "properties": 1, "relationships": 0,
    }
    assert result["source"]["completion"] == "incomplete"
    assert not result["interpretation"]["semantic_accuracy_assessed"]
    assert not result["interpretation"]["reference_used"]
    assert not result["interpretation"]["validation_is_accuracy"]
    assert not result["interpretation"]["unscored_is_correct"]


@pytest.mark.parametrize("status", ["negated", "conditional", "uncertain", "hypothetical"])
def test_passed_nonpositive_relationships_are_retained_but_never_connect_root(status):
    edge = assertion("edge", object_id="child", assertion_status=status,
                     condition_provenance_indexes=[0] if status == "conditional" else [])
    result = export(root(), entity("child"), edge)
    assert ids(result["full_validated_candidate_graph"], "relationships") == {"edge"}
    positive = result["root_reachable_positive_graph"]
    assert ids(positive, "nodes") == {"root"}
    assert not positive["relationships"]
    assert not result["full_validated_candidate_graph"]["relationships"][0][
        "graph_export"]["positive_eligible"]


@pytest.mark.parametrize("field", ["condition_anchors", "condition_provenance_indexes"])
def test_affirmed_with_conditions_is_not_positive(field):
    result = export(root(), assertion("value", **{field: [0]}))
    assert not result["root_reachable_positive_graph"]["properties"]
    assert "conditioned_assertion" in result["positive_projection_exclusions"][0][
        "positive_exclusion_reasons"]


def test_review_rejected_passed_candidates_are_preserved_and_cascade_exclusion():
    result = export(root(), entity("child", review_status="rejected"),
                    assertion("edge", object_id="child"),
                    assertion("value", subject="child"))
    assert result["full_validated_candidate_graph"]["counts"] == {
        "nodes": 2, "properties": 1, "relationships": 1,
    }
    assert result["root_reachable_positive_graph"]["counts"] == {
        "nodes": 1, "properties": 0, "relationships": 0,
    }


@pytest.mark.parametrize("changes,reason", [
    ({"subject": ref("missing")}, "missing_candidate"),
    ({"subject": ref("root", 9)}, "stale_revision"),
    ({"object": ref("missing")}, "missing_candidate"),
    ({"object": ref("child", 9)}, "stale_revision"),
    ({"object": {"candidate_id": "child"}}, "invalid_or_missing_reference"),
    ({"object": ref("child", True)}, "invalid_or_missing_reference"),
    ({"object": ref("child", class_iri="urn:Wrong")}, "reference_class_mismatch"),
    ({"object": ref("child", instance_iri="urn:Wrong")}, "reference_identity_mismatch"),
    ({"path_root": ref("root", 9)}, "stale_revision"),
    ({"path_root": ref("child")}, "path_root_not_current_document_root"),
    ({"dependency_refs": [ref("missing")]}, "missing_candidate"),
    ({"scope": {"subject": ref("root", 9)}}, "stale_revision"),
])
def test_bad_exact_references_never_enter_positive_graph(changes, reason):
    edge = assertion("edge", object_id="child")
    edge.update(changes)
    result = export(root(), entity("child"), edge)
    assert not result["root_reachable_positive_graph"]["relationships"]
    assert len(result["full_validated_candidate_graph"]["relationships"]) == 1
    checks = result["positive_projection_exclusions"][0]["reference_checks"]
    assert reason in {check["status"] for check in checks}


@pytest.mark.parametrize("revision", [1, 2])
def test_duplicate_id_is_quarantined_without_latest_selection(revision):
    result = export(root(), entity("child"), entity("child", revision=revision),
                    assertion("edge", object_id="child"))
    assert result["summary"]["duplicate_candidate_ids"] == ["child"]
    assert len(result["full_validated_candidate_graph"]["nodes"]) == 3
    assert ids(result["root_reachable_positive_graph"], "nodes") == {"root"}


def test_same_names_do_not_merge_and_real_relationship_cycle_terminates():
    result = export(root(), entity("a"), entity("b"),
                    assertion("ra", object_id="a"), assertion("ab", "a", "b"),
                    assertion("ba", "b", "a"))
    positive = result["root_reachable_positive_graph"]
    assert ids(positive, "nodes") == {"root", "a", "b"}
    assert ids(positive, "relationships") == {"ra", "ab", "ba"}


def test_dangling_dependency_cascades_across_multiple_levels():
    broken = assertion("broken", dependency_refs=[ref("missing")])
    edge = assertion("edge", object_id="child", dependency_refs=[ref("broken")])
    prop = assertion("value", "child", dependency_refs=[ref("edge")])
    result = export(root(), entity("child"), broken, edge, prop)
    assert result["root_reachable_positive_graph"]["counts"] == {
        "nodes": 1, "properties": 0, "relationships": 0,
    }
    assert len(result["positive_projection_exclusions"]) == 3


def test_scope_subject_must_match_assertion_but_entity_scope_can_be_parent():
    child = entity("child", scope={"subject": ref("root")})
    good = assertion("edge", object_id="child", scope={"subject": ref("root")})
    bad = assertion("bad", subject="child", scope={"subject": ref("root")})
    result = export(root(), child, good, bad)
    assert ids(result["root_reachable_positive_graph"], "nodes") == {"root", "child"}
    assert not result["root_reachable_positive_graph"]["properties"]
    assert "scope_subject_mismatch" in result["positive_projection_exclusions"][0][
        "positive_exclusion_reasons"]


def test_binding_mismatch_or_missing_evidence_cannot_support_positive_assertion():
    wrong = assertion("wrong")
    wrong["bindings"][0]["subject_candidate_id"] = "child"
    missing = assertion("missing", bindings=[])
    result = export(root(), wrong, missing)
    assert len(result["full_validated_candidate_graph"]["properties"]) == 2
    assert not result["root_reachable_positive_graph"]["properties"]


def test_root_comes_from_document_identity_not_name_class_or_path_strings():
    misleading = entity("looks-like-root", class_iri="urn:Report", text="Root document")
    child = entity("child", relationship_path=["urn:uses"], path_root=ref("root"))
    result = export(root(), misleading, child)
    assert ids(result["root_reachable_positive_graph"], "nodes") == {"root"}
    mismatched = root()
    mismatched["identity"]["document_root"] = "different-document"
    assert not export(mismatched)["root_reachable_positive_graph"]["roots"]
    assert not export(root(), document_hash="different-document")[
        "root_reachable_positive_graph"]["roots"]


def test_disconnected_support_is_not_silently_added_and_references_remain_closed():
    # Child -> property -> orphan support: each round must remove downstream
    # dependents, and BFS must never reintroduce a withdrawn endpoint.
    support = assertion("support", "orphan")
    child = entity("child", dependency_refs=[ref("dependent")])
    dependent = assertion("dependent", dependency_refs=[ref("support")])
    result = export(root(), child, entity("orphan"), support, dependent,
                    assertion("edge", object_id="child"))
    assert result["summary"]["positive_candidates"] == 6
    positive = result["root_reachable_positive_graph"]
    assert positive["counts"] == {"nodes": 1, "properties": 0, "relationships": 0}


def test_dependency_cycles_are_allowed_unless_an_actual_reference_fails():
    result = export(root(), assertion("a", dependency_refs=[ref("b")]),
                    assertion("b", dependency_refs=[ref("a")]))
    assert ids(result["root_reachable_positive_graph"], "properties") == {"a", "b"}


def test_stored_path_root_must_match_actual_root_traversal():
    another_root = root()
    another_root["candidate_id"] = "other-root"
    edge = assertion("edge", object_id="child", path_root=ref("other-root"))
    result = export(root(), another_root, entity("child"), edge)
    assert result["summary"]["positive_candidates"] == 4
    positive = result["root_reachable_positive_graph"]
    assert ids(positive, "nodes") == {"root", "other-root"}
    assert not positive["relationships"]


def test_cli_reads_only_run_and_emits_json_to_stdout(tmp_path, capsys, monkeypatch):
    run_path = tmp_path / "run.json"
    contents = json.dumps({"candidates": [root()]}, ensure_ascii=False).encode()
    run_path.write_bytes(contents)
    sibling = tmp_path / "reference.json"
    sibling.write_text("must not be opened")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    read_bytes = type(run_path).read_bytes
    reads = []

    def guarded_read(path):
        reads.append(path)
        assert path == run_path
        return read_bytes(path)

    with monkeypatch.context() as patch:
        patch.setattr(type(run_path), "read_bytes", guarded_read)
        main([str(run_path)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert not captured.err
    assert reads == [run_path]
    assert result["source"]["run_sha256"] == hashlib.sha256(contents).hexdigest()
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == before


@pytest.mark.parametrize("run", [[], {}, {"candidates": {}}, {"candidates": [{}]}])
def test_malformed_candidate_identity_fails_explicitly(run):
    with pytest.raises(ValueError):
        export_quality_graph(run)
