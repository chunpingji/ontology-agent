from rdflib import RDF, SH, XSD, Graph, Literal, Namespace

from app.services.extraction.shacl_core import run_local_shacl

NS = Namespace("urn:test:shacl:")


def graphs():
    data, shapes = Graph(), Graph()
    data.add((NS.claim, NS.value, Literal("abc")))
    shapes.add((NS.shape, RDF.type, SH.NodeShape))
    shapes.add((NS.shape, SH.targetNode, NS.claim))
    shapes.add((NS.shape, SH.property, NS.prop))
    shapes.add((NS.prop, SH.path, NS.value))
    shapes.add((NS.prop, SH.datatype, XSD.decimal))
    return data, shapes


def test_nonconformance_is_completed_and_focus_is_selected():
    data, shapes = graphs()
    result = run_local_shacl(data, shapes, expected_focus_nodes=[str(NS.claim)])
    assert result["evaluated"] and result["coverage"]["complete"]
    assert result["execution_status"] == "completed" and result["conforms"] is False
    assert result["report"]


def test_empty_targets_and_absent_focus_cannot_pass():
    data, shapes = graphs()
    shapes.remove((NS.shape, SH.targetNode, NS.claim))
    result = run_local_shacl(data, shapes, expected_focus_nodes=[str(NS.claim)])
    assert result["conforms"] is True and not result["coverage"]["complete"]
    shapes.add((NS.shape, SH.targetNode, NS.absent))
    result = run_local_shacl(data, shapes, expected_focus_nodes=[str(NS.absent)])
    assert not result["coverage"]["complete"]


def test_validation_failure_object_is_execution_failure(monkeypatch):
    from pyshacl.errors import ValidationFailure

    monkeypatch.setattr(
        "pyshacl.validate", lambda *a, **kw: (False, ValidationFailure("bad"), "bad")
    )
    result = run_local_shacl(*graphs(), expected_focus_nodes=[str(NS.claim)])
    assert result["execution_status"] == "failed" and not result["evaluated"]
    assert result["conforms"] is None


def test_forbidden_remote_inputs_are_not_opened():
    result = run_local_shacl("https://example.invalid/data", Graph(), expected_focus_nodes=[])
    assert result["execution_status"] == "failed" and "memory_graphs" in result["issues"][0]


def test_shared_shacl_core_has_no_execution_or_business_dependency():
    import ast
    from pathlib import Path

    from app.services.extraction import shacl_core

    tree = ast.parse(Path(shacl_core.__file__).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(item.name for item in node.names)
    assert not any(
        name.startswith(("app.models", "app.evaluation", "app.services")) for name in imports
    )
