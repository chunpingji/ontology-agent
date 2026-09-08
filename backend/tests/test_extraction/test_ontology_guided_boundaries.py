"""Dependency boundaries for the shared ontology-guided recognition domain."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pydantic import BaseModel

from app.evaluation.citation_protocol import CitationProtocol as EvaluationCitationProtocol
from app.services.extraction.ontology_guided import contracts
from app.services.extraction.ontology_guided.citations import CitationProtocol

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
CORE = APP / "services" / "extraction" / "ontology_guided"
ACTIVE_EVALUATOR = APP / "evaluation" / "quality_guided_variant.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            values.add(module)
            if module == "app":
                values.update(f"app.{alias.name}" for alias in node.names)
    return values


def test_online_application_never_imports_evaluation_package():
    offenders = []
    for path in APP.rglob("*.py"):
        if path.is_relative_to(APP / "evaluation"):
            continue
        imports = _imports(path)
        if any(
            value == "app.evaluation" or value.startswith("app.evaluation.") for value in imports
        ):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == []


def test_evaluation_citation_protocol_is_a_thin_shared_core_import():
    assert EvaluationCitationProtocol is CitationProtocol


def test_active_evaluator_imports_shared_executor_not_legacy_runner():
    imports = _imports(ACTIVE_EVALUATOR)
    assert "app.services.extraction.ontology_guided.executor" in imports
    assert not any(
        value == "app.evaluation.legacy_quality_guided_variant"
        or value.startswith("app.evaluation.legacy_quality_guided_variant.")
        for value in imports
    )
    assert "app.services.extraction.extraction_tasks" not in imports
    assert "app.evaluation.ontology_guided_scorer" not in imports


def test_shared_core_has_no_persistence_review_or_evaluation_dependency():
    forbidden = (
        "app.evaluation",
        "app.models",
        "app.services.extraction.candidate_store",
        "app.services.extraction.extraction_tasks",
        "app.services.fact_commit",
        "app.services.ontology_instance_writer",
    )
    offenders: dict[str, list[str]] = {}
    for path in CORE.glob("*.py"):
        matches = sorted(
            value
            for value in _imports(path)
            if any(value == prefix or value.startswith(prefix + ".") for prefix in forbidden)
        )
        if matches:
            offenders[str(path.relative_to(BACKEND))] = matches
    assert offenders == {}


def test_shared_contracts_have_no_legacy_runner_selection_field():
    forbidden = {
        "legacy_mode",
        "legacy_runner",
        "new_mode",
        "new_runner",
        "runner_mode",
        "runner_version",
        "use_legacy",
    }
    offenders: dict[str, list[str]] = {}
    for name, value in inspect.getmembers(contracts, inspect.isclass):
        if value.__module__ != contracts.__name__ or not issubclass(value, BaseModel):
            continue
        fields = sorted(forbidden.intersection(value.model_fields))
        if fields:
            offenders[name] = fields
    assert offenders == {}
