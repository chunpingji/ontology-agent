from app.services.extraction.template_extraction_plan import build_instance_coverage
from app.services.fact_selector import FactSelector
from app.services.reporting.ast_template import ReportTemplate
from tests.test_extraction.test_fact_commit import entity
from tests.test_reporting.test_fact_selector import iri, property_value, relation, snapshot


def template(**overrides):
    return ReportTemplate.model_validate(
        {
            "template_id": "test",
            "sections": [
                {
                    "section_id": "s",
                    "title": "测试",
                    "groups": [],
                    "coverage": [
                        {
                            "kind": "ontology_relation",
                            "doc_class_iri": "urn:test:Drug",
                            "predicate_iri": "urn:uses",
                            "range_class_iri": "urn:Equipment",
                            "required_properties": ["urn:material"],
                            **overrides,
                        }
                    ],
                }
            ],
        }
    )


def selector():
    return FactSelector(
        snapshot(
            entity("A"),
            entity("B"),
            entity("E", "urn:Equipment"),
            entity("F", "urn:Equipment"),
            relation("ae", "A", "E"),
            relation("bf", "B", "F"),
            property_value("em", "E"),
        )
    )


def test_a_does_not_hide_b_missing_property():
    manifest = build_instance_coverage(template(), selector(), template_version="v1")
    rows = {t["subject_instance_iri"]: t for t in manifest["tasks"]}
    assert rows[iri("A")]["status"] == "filled"
    assert rows[iri("B")]["status"] == "missing"
    assert rows[iri("B")]["objects"][0]["missing_properties"] == ["urn:material"]


def test_nested_equipment_material_is_checked_per_scoped_equipment():
    facts = FactSelector(
        snapshot(
            entity("Report"),
            entity("E", "urn:Equipment"),
            entity("F", "urn:Equipment"),
            entity("Unrelated", "urn:Equipment"),
            entity("Steel", "urn:Material"),
            relation("re", "Report", "E"),
            relation("rf", "Report", "F"),
            relation("em", "E", "Steel", "urn:constructedOf"),
        )
    )
    manifest = build_instance_coverage(
        template(
            doc_class_iri="urn:Equipment",
            predicate_iri="urn:constructedOf",
            range_class_iri="urn:Material",
            required_properties=[],
            subject_root_class_iri="urn:test:Drug",
            subject_path=[{"predicate_iri": "urn:uses"}],
            quantifier="all",
            object_instance_iris=[iri("Steel")],
        ),
        facts,
    )
    rows = {t["subject_instance_iri"]: t for t in manifest["tasks"]}
    assert set(rows) == {iri("E"), iri("F")}
    assert rows[iri("E")]["status"] == "filled"
    assert rows[iri("F")]["status"] == "missing"


def test_all_and_max_need_closed_universe():
    assert (
        build_instance_coverage(template(quantifier="all"), selector())["tasks"][0]["status"]
        == "incomplete"
    )
    assert (
        build_instance_coverage(template(max_count=2), selector())["tasks"][0]["status"]
        == "incomplete"
    )


def test_explicit_object_set_is_closed_but_requires_each_object():
    manifest = build_instance_coverage(
        template(quantifier="all", object_instance_iris=[iri("E"), iri("F")]), selector()
    )
    assert all(t["status"] == "missing" for t in manifest["tasks"])
    assert manifest["tasks"][0]["object_universe_status"] == "complete"


def test_max_counts_incomplete_objects_too():
    facts = FactSelector(
        snapshot(
            entity("A"),
            entity("E", "urn:Equipment"),
            entity("F", "urn:Equipment"),
            relation("ae", "A", "E"),
            relation("af", "A", "F"),
            property_value("em", "E"),
        )
    )
    task = build_instance_coverage(template(max_count=1), facts)["tasks"][0]
    assert task["status"] == "conflict"
    assert task["reason"] == "max_count_exceeded"


def test_no_subject_not_vacuously_satisfied_and_uncommitted_subject_shown():
    facts = FactSelector(snapshot(entity("E", "urn:Equipment")))
    task = build_instance_coverage(template(min_count=0), facts)["tasks"][0]
    assert task["status"] == "incomplete" and task["reason"] == "subject_unresolved"
    task = build_instance_coverage(template(), facts, candidates=[entity("B")])["tasks"][0]
    assert task["status"] == "pending_review"
    assert task["subject_candidate_ref"]["candidate_id"] == "B"


def test_pending_negative_does_not_count_as_absent_and_changes_manifest_identity():
    facts = FactSelector(snapshot(entity("A"), entity("E", "urn:Equipment")))
    t = template(object_instance_iris=[iri("E")])
    before = build_instance_coverage(t, facts)
    after = build_instance_coverage(
        t, facts, candidates=[relation("no", "A", "E", polarity="negated")]
    )
    assert before["manifest_id"] != after["manifest_id"]
    assert after["tasks"][0]["status"] == "pending_review"


def test_committed_negative_has_separate_absent_not_filled_status():
    facts = FactSelector(
        snapshot(
            entity("A"), entity("E", "urn:Equipment"), relation("no", "A", "E", polarity="negated")
        )
    )
    task = build_instance_coverage(template(object_instance_iris=[iri("E")]), facts)["tasks"][0]
    assert task["status"] == "confirmed_absent" and task["negative_assertion_ids"]


def test_candidate_conflict_blocks_even_existing_positive_fact():
    bad = property_value("bad", "E", value="glass").model_copy(
        update={"validation_status": "conflict"}
    )
    task = build_instance_coverage(template(), selector(), candidates=[bad])["tasks"][0]
    assert task["status"] == "conflict"


def test_template_version_and_full_path_change_manifest():
    facts = selector()
    first = build_instance_coverage(template(), facts, template_version="v1")
    assert (
        first["manifest_id"]
        != build_instance_coverage(template(), facts, template_version="v2")["manifest_id"]
    )
    wrong = build_instance_coverage(
        template(predicate_path=[{"predicate_iri": "urn:wrong"}]), facts
    )
    assert wrong["tasks"][0]["status"] == "missing"
