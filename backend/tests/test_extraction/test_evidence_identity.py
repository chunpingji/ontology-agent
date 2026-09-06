import pytest

from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id


def test_canonical_identity_is_order_independent_but_sequence_sensitive():
    assert evidence_hash({"b": {2, 1}, "a": "𠀀"}) == evidence_hash({"a": "𠀀", "b": {1, 2}})
    assert evidence_hash([1, 2]) != evidence_hash([2, 1])
    assert stable_id("sample", "file") != stable_id("production", "file")


@pytest.mark.parametrize("field", ["role", "revision", "scope", "ontology", "model", "policy"])
def test_each_declared_dependency_mutation_changes_identity(field):
    fields = ("role", "revision", "scope", "ontology", "model", "policy")
    dependencies = dict.fromkeys(fields, "v1")
    assert evidence_hash(dependencies) != evidence_hash({**dependencies, field: "v2"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "key"}, object()])
def test_non_replayable_values_are_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        canonical_json(value)
