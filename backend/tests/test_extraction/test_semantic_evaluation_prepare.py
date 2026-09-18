"""Independent DOCX preparation never needs a historical extraction job."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.evaluation import cmc_benchmark
from tests.test_extraction.test_ontology_guided_core import (
    PRODUCT,
    REPORT,
    FakeAdapter,
    ontology,
    sample,
)


@pytest.fixture
def independent_source(tmp_path, monkeypatch):
    from app.db import engine
    from app.services import ontology_engine
    from app.services.extraction import extraction_tasks
    from app.services.extraction.ontology_guided import ontology_plan

    sample(tmp_path)
    source = tmp_path / "source.docx"
    ontology_dir = tmp_path / "source-ontology"
    ontology_dir.mkdir()
    (ontology_dir / "fixture.ttl").write_text("# frozen ontology fixture\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ontology_dir", ontology_dir)
    monkeypatch.setattr(settings, "semantic_ranking_enabled", False)

    def forbidden(*args, **kwargs):
        raise AssertionError("independent preparation must not contact a database or model")

    monkeypatch.setattr(engine, "connect", forbidden)
    monkeypatch.setattr(cmc_benchmark, "slot_snapshot", forbidden)
    monkeypatch.setattr(cmc_benchmark, "emit", lambda *args, **kwargs: None)

    class IsolatedOntology:
        def __init__(self, *, ontology_dir, store_path):
            assert "prepared" in str(ontology_dir)
            assert Path(store_path).name == "isolated.sqlite3"
            assert Path(store_path).is_relative_to(tmp_path)
            self._world = SimpleNamespace(close=lambda: None)

        def load(self):
            return None

    monkeypatch.setattr(ontology_engine, "OntologyEngine", IsolatedOntology)
    monkeypatch.setattr(extraction_tasks, "semantic_schema_from_engine", lambda engine: {})
    monkeypatch.setattr(ontology_plan, "ontology_snapshot_from_engine",
                        lambda engine, root_class_iri: ontology())
    return source


def _prepare_args(source, output, root=PRODUCT):
    return cmc_benchmark.parser().parse_args([
        "prepare", "--source-docx", str(source), "--root-class-iri", root,
        "--output", str(output),
    ])


def test_local_docx_freezes_source_and_explicit_non_cmc_root_without_job_db(
    tmp_path, independent_source,
):
    output = tmp_path / "prepared"
    original = independent_source.read_bytes()
    cmc_benchmark.prepare(_prepare_args(independent_source, output))
    manifest = cmc_benchmark.read_json(output / "manifest.json")
    assert manifest["source_origin"] == "local_docx"
    assert manifest["class_iri"] == PRODUCT
    assert manifest["job_id"] is None and manifest["document_ref"] is None
    assert manifest["reference_is_recognition_input"] is False
    assert manifest["model_slots_status"] == "not_queried_during_prepare"
    assert (output / "source.docx").read_bytes() == original == independent_source.read_bytes()
    assert cmc_benchmark.digest_file(output / "source.docx") == manifest["document_hash"]
    assert (output / "ontology_snapshot.json").is_file()
    assert not (output / "reference.json").exists()


def test_selected_non_cmc_root_reaches_actual_active_executor(tmp_path, independent_source,
                                                             monkeypatch):
    from app.evaluation.semantic_ranking_evaluation import validate_ablation_pair
    from app.services.extraction.ontology_guided import model_adapter

    output = tmp_path / "prepared"
    cmc_benchmark.prepare(_prepare_args(independent_source, output))
    monkeypatch.setattr(cmc_benchmark, "slot_snapshot", lambda: [])
    monkeypatch.setattr(model_adapter, "configured_model_adapter", lambda: FakeAdapter())
    result_dir = tmp_path / "evaluation-run"
    args = cmc_benchmark.parser().parse_args([
        "run", "--prepared", str(output), "--output", str(result_dir),
        "--mode", "quality_guided", "--ranking-ablation", "A",
    ])
    cmc_benchmark.run(args)
    run = cmc_benchmark.read_json(result_dir / "run.json")
    result = cmc_benchmark.read_json(result_dir / "result.json")
    assert run["root_class_iri"] == result["root_class_iri"] == PRODUCT
    assert next(node for node in run["graph"]["nodes"] if node["root"])["class_iri"] == PRODUCT
    assert {call["predicate_iri"] for call in run["adapter_calls"]} == {"urn:appearance"}
    ablation = cmc_benchmark.read_json(result_dir / "ablation.json")
    assert ablation["shared"]["scope"]["root_class_iri"] == PRODUCT
    different_root = deepcopy(ablation)
    different_root["shared"]["scope"]["root_class_iri"] = REPORT
    with pytest.raises(ValueError, match="shared input, model, scope or budget"):
        validate_ablation_pair(
            ablation, different_root, allowed_factor_changes=[], fixed_pool=False,
        )


def test_prepare_rejects_existing_directory_without_touching_it(tmp_path, independent_source):
    output = tmp_path / "prepared"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        cmc_benchmark.prepare(_prepare_args(independent_source, output))
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in output.iterdir()) == ["keep.txt"]


def test_prepare_rejects_non_docx_and_missing_or_unknown_explicit_root(tmp_path,
                                                                   independent_source):
    invalid = tmp_path / "invalid.docx"
    invalid.write_text("not a Word archive", encoding="utf-8")
    output = tmp_path / "prepared-invalid"
    with pytest.raises(ValueError):
        cmc_benchmark.prepare(_prepare_args(invalid, output))
    assert not output.exists()
    args = _prepare_args(independent_source, tmp_path / "prepared-missing-root")
    args.root_class_iri = None
    with pytest.raises(ValueError, match="explicit root_class_iri"):
        cmc_benchmark.prepare(args)
    unknown = tmp_path / "prepared-unknown-root"
    with pytest.raises(ValueError, match="absent from the frozen ontology"):
        cmc_benchmark.prepare(_prepare_args(independent_source, unknown, "urn:UnknownRoot"))
    assert not (unknown / "manifest.json").exists()


def test_prepare_source_options_are_mutually_exclusive_and_required():
    parser = cmc_benchmark.parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["prepare", "--source-docx", "source.docx", "--document-ref", "upload-x",
                           "--root-class-iri", REPORT, "--output", "prepared"])
    with pytest.raises(SystemExit):
        parser.parse_args(["prepare", "--output", "prepared"])
