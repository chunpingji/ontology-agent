"""D: frozen summary retrieval followed by the unchanged C NER/Qwen tool protocol.

Summaries select original IR records; they are never fact evidence or Qwen input.
This experiment does not reuse the old scope references or score against their silver labels.
"""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.evaluation import schema_card_gliner2 as c_runner
from app.evaluation.schema_card_evidence import build_sources
from app.evaluation.schema_card_probe import SOURCE_SHA256, digest, read, write
from app.evaluation.schema_card_tools import plan_schema, validate_schema
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.gliner_extractor import _valid_source_span
from app.services.extraction.ontology_guided.contracts import MetadataSnapshot
from app.services.extraction.ontology_guided.metadata import prepare_metadata

VERSION = "cmc-summary-retrieval-tools-v1"
ARM = "D"
SCOPES = (
    "introduction", "product_properties", "process_equipment", "quality_condition",
    "storage", "residue_missing", "pde_conflict", "toxicity_unknown",
)
MAX_MODEL_CALLS = 16
METADATA_FILES = ("metadata.json", "section-tree.json", "summaries.json", "manifest.json")
ROOT_INPUT_FILES = ("source.docx", "ir.json", "cards.json", "cases.json", "vocabularies.json")
SCOPE_INPUT_FILES = (
    "case.json", "sources.json", "demand.json", "retrieval.json", "coverage.json",
    "plan/proposal.json", "plan/schema.json", "vocabulary.json",
)


def retrieve_scope(ir, metadata, menus, catalog, scope_id):
    # The local helper is an evaluation dependency, never a runtime service import.
    from app.evaluation.schema_card_summary_retrieval import retrieve_scope as retrieve

    return retrieve(ir, metadata, menus, catalog, scope_id)


def code_paths():
    evaluation = Path(__file__).parent
    extraction = evaluation.parent / "services/extraction"
    paths = [evaluation / name for name in (
        "schema_card_tools.py", "schema_card_tightened.py", "schema_card_evidence.py",
        "schema_card_probe.py", "schema_card_gliner2.py", "schema_card_summary_tools.py",
        "schema_card_summary_retrieval.py",
    )]
    paths += sorted((extraction / "tool_validation").glob("*.py"))
    paths += [extraction / name for name in ("gliner_extractor.py", "gliner2_extractor.py")]
    paths += [extraction / "document_ir.py", extraction / "evidence_identity.py"]
    paths += [extraction / "ontology_guided" / name for name in (
        "contracts.py", "records.py", "retrieval.py", "metadata.py",
    )]
    paths += [c_runner.DEFAULT_OVERLAY]
    return {path.name: path for path in paths}


def verify_baseline(baseline, model_path, device):
    old = read(baseline / "result.json")
    if old.get("status") != "completed" or old.get("version") != c_runner.VERSION:
        raise ValueError("completed_boundary_C_baseline_required")
    if set(path.name for path in (baseline / "C").iterdir() if path.is_dir()) != set(SCOPES):
        raise ValueError("summary_fixed_eight_scopes_required")
    if digest(baseline / "source.docx") != SOURCE_SHA256 or old["source_sha256"] != SOURCE_SHA256:
        raise ValueError("fixed_source_document_mismatch")
    for name in ("ir.json", "cards.json"):
        if digest(baseline / name) != old["input_hashes"][name]:
            raise ValueError("baseline_input_changed:" + name)
    for scope in SCOPES:
        for name in ("plan/proposal.json", "vocabulary.json"):
            if digest(baseline / "C" / scope / name) != old["prepared_tool_hashes"][scope][name]:
                raise ValueError("baseline_demand_changed:" + scope + ":" + name)
    paths = code_paths()
    for name, expected in old["code_hashes"].items():
        if (name not in paths or digest(paths[name]) != expected
                or digest(baseline / "code" / name) != expected):
            raise ValueError("frozen_C_code_changed:" + name)
    if (c_runner.verify_model_files(model_path) != old["gliner2_model"]
            or c_runner.package_versions() != old["packages"]
            or device != old["ner_device"]
            or old["labels_per_batch"] != c_runner.LABELS_PER_BATCH
            or old["backend"] != c_runner.BACKEND
            or old["architecture"] != c_runner.ARCHITECTURE):
        raise ValueError("frozen_C_model_or_runtime_changed")
    ir = DocumentIR.model_validate(read(baseline / "ir.json"))
    if ir.document_hash != SOURCE_SHA256:
        raise ValueError("ir_source_mismatch")
    return old, ir, read(baseline / "cards.json")


def load_metadata(metadata_dir, ir, ir_sha256):
    """Reject another document/IR, changed artifacts and internally inconsistent snapshots."""
    manifest = read(metadata_dir / "manifest.json")
    if (manifest.get("source_sha256") != ir.document_hash
            or manifest.get("analysis_id") != ir.analysis_id
            or manifest.get("structure_hash") != ir.structure_hash
            or manifest.get("input_hashes", {}).get("ir") != ir_sha256):
        raise ValueError("summary_metadata_source_mismatch")
    for name in METADATA_FILES[:-1]:
        if manifest.get("output_hashes", {}).get(name) != digest(metadata_dir / name):
            raise ValueError("summary_metadata_artifact_changed:" + name)
    snapshot = MetadataSnapshot.model_validate(
        read(metadata_dir / "metadata.json")["metadata_snapshot"],
    )
    if (snapshot.document_hash != ir.document_hash or snapshot.analysis_id != ir.analysis_id
            or snapshot.structure_hash != ir.structure_hash
            or manifest.get("metadata_snapshot_id") != snapshot.snapshot_id
            or manifest.get("metadata_dependency_hash") != snapshot.dependency_hash):
        raise ValueError("summary_snapshot_source_mismatch")
    regenerated = prepare_metadata(
        ir, section_tree=read(metadata_dir / "section-tree.json"),
        summary_version=snapshot.summary_version,
        summary_model_identity=snapshot.summary_model_identity,
        generation_source=snapshot.generation_source,
    )
    if regenerated != snapshot:
        raise ValueError("summary_snapshot_content_mismatch")
    return snapshot


def load_demand(baseline, scope, catalog):
    """Read only menus and class/group choices; discard all historical citations."""
    directory = baseline / "C" / scope
    plan = read(directory / "plan/proposal.json")
    menus = read(directory / "subject-cards.json")
    selected = plan["get_schema_card"]
    expected = {f"s{i}": item["class_iri"] for i, item in enumerate(selected, 1)}
    if (set(menus) != {"document", *expected}
            or any(menus[sid]["class_iri"] != iri for sid, iri in expected.items())
            or any(item["class_iri"] not in catalog for item in selected)):
        raise ValueError("baseline_class_slot_mismatch:" + scope)
    demand = {
        "get_schema_card": deepcopy(selected),
        "propose_mentions": deepcopy(plan["propose_mentions"]),
        "menus": {sid: {key: deepcopy(menu[key]) for key in ("class_iri", "label", "fields")}
                  for sid, menu in menus.items()},
    }
    # Validate fixed choices without consulting the old source scope or its refs.
    validate_schema({**{key: demand[key] for key in ("get_schema_card", "propose_mentions")},
                     "inspect_evidence": []}, plan_schema({}, list(catalog)))
    _, _, _, expected_menus = c_runner.candidate_schema(
        {**{key: demand[key] for key in ("get_schema_card", "propose_mentions")},
         "inspect_evidence": []}, {}, catalog, {},
    )
    expected_menus = {
        sid: {key: menu[key] for key in ("class_iri", "label", "fields")}
        for sid, menu in expected_menus.items()
    }
    if demand["menus"] != expected_menus:
        raise ValueError("baseline_menu_does_not_match_catalog:" + scope)
    return demand


def rebuild_plan(demand, sources, catalog):
    plan = {key: deepcopy(demand[key]) for key in ("get_schema_card", "propose_mentions")}
    plan["inspect_evidence"] = [{"ref": ref} for ref in sources]
    schema = plan_schema(sources, list(catalog))
    validate_schema(plan, schema)
    return plan, schema


def scope_input_hashes(output):
    return {scope: {name: digest(output / ARM / scope / name) for name in SCOPE_INPUT_FILES}
            for scope in SCOPES}


def tool_hashes(output):
    return {scope: digest(output / ARM / scope / "tools.json") for scope in SCOPES}


def prepare_inputs(baseline, metadata_dir, output, ontology_dir, ir, metadata, catalog):
    """Generate new sources and plans before NER; never read C sources or selectors."""
    ir_dict, cases, profiles, retrieval_summary = ir.model_dump(mode="json"), [], {}, {}
    document_ref = read(metadata_dir / "manifest.json")["document_ref"]
    for scope in SCOPES:
        demand = load_demand(baseline, scope, catalog)
        selection = retrieve_scope(ir, metadata, demand["menus"], catalog, scope)
        case = selection["case"]
        case["document_ref"] = document_ref
        if case.get("scope_id") != scope or not case.get("source_units"):
            raise ValueError("summary_retrieval_no_sources_or_wrong_scope:" + scope)
        units = case["source_units"]
        if len({unit["evidence_id"] for unit in units}) != len(units):
            raise ValueError("summary_duplicate_source_unit:" + scope)
        for unit in units:
            if ir.unit(unit["evidence_id"]).model_dump(mode="json") != unit:
                raise ValueError("summary_unit_does_not_match_frozen_ir:" + scope)
        sources = build_sources(case, ir_dict)
        plan, schema = rebuild_plan(demand, sources, catalog)
        selected = [item["class_iri"] for item in plan["get_schema_card"]]
        vocabulary = c_runner.build_extraction_vocabulary(
            catalog, selected, ontology_dir=ontology_dir,
        )
        old_vocabulary = baseline / "C" / scope / "vocabulary.json"
        if vocabulary["missing"] or vocabulary != read(old_vocabulary):
            raise ValueError("frozen_C_vocabulary_changed:" + scope)
        profiles[scope] = vocabulary
        directory = output / ARM / scope
        (directory / "plan").mkdir(parents=True)
        for name, value in (
            ("case.json", case), ("sources.json", sources), ("demand.json", demand),
            ("plan/proposal.json", plan), ("plan/schema.json", schema),
            ("vocabulary.json", vocabulary), ("retrieval.json", selection["retrieval"]),
            ("coverage.json", selection["coverage"]),
        ):
            write(directory / name, value)
        cases.append(case)
        retrieval_summary[scope] = {
            "retrieval": str(Path(ARM) / scope / "retrieval.json"),
            "coverage": selection["coverage"],
            "ablation": selection["retrieval"].get("ablation", {}),
            "source_units": len(sources),
            "source_characters": sum(len(source["text"]) for source in sources.values()),
        }
    write(output / "cases.json", cases)
    write(output / "vocabularies.json", profiles)
    return cases, profiles, retrieval_summary


def validate_tool_sources(tools, sources, vocabulary, catalog, plan):
    """A prepared tool file may only cite the newly retrieved original units."""
    ner = tools["propose_mentions"]
    for span in ner.get("spans", []):
        source = sources.get(span.get("ref"))
        entry = vocabulary["entries"].get(span.get("label"))
        if (source is None or entry is None
                or not _valid_source_span(span, source["text"], set(vocabulary["entries"]))
                or span.get("concept_iri") != entry["iri"]
                or span.get("extraction_role") != entry["role"]):
            raise ValueError("prepared_mention_not_in_new_sources")
    inspection = c_runner.inspect_evidence(
        sources, [item["ref"] for item in plan["inspect_evidence"]],
    )
    if (tools["inspect_evidence"] != inspection
            or tools["source_spans"] != c_runner.span_catalog(sources, ner, inspection)
            or tools["get_schema_card"] != [
                c_runner.get_schema_card(catalog, item["class_iri"], list(catalog))
                for item in plan["get_schema_card"]
            ]):
        raise ValueError("prepared_tool_sources_changed")


def ner_row(scope, ner, groups, vocabulary_hash):
    return {"scope_id": scope, "execution_status": ner["execution_status"],
            "span_count": len(ner["spans"]), "timing": ner.get("timing"),
            "label_count": {group: len(labels) for group, labels in groups.items()},
            "vocabulary_sha256": vocabulary_hash}


def requested_groups(plan, vocabulary):
    requested = {item["group"] for item in plan["propose_mentions"]}
    return {group: vocabulary["groups"][group] for group in c_runner.GROUPS
            if group in requested and vocabulary["groups"][group]}


def prepare_ner(output, catalog, profiles, model_path, device):
    descriptions = {}
    for profile in profiles.values():
        for label, entry in profile["entries"].items():
            if label in descriptions and descriptions[label] != entry["description"]:
                raise ValueError("cross_scope_label_description_conflict:" + label)
            descriptions[label] = entry["description"]
    extractor = c_runner.Gliner2Extractor(
        model_path, descriptions=descriptions, device=device, word_splitter="char", max_len=160,
    )
    rows = []
    for scope in SCOPES:
        directory, vocabulary = output / ARM / scope, profiles[scope]
        sources, plan = read(directory / "sources.json"), read(directory / "plan/proposal.json")
        groups = requested_groups(plan, vocabulary)
        ner = (c_runner.propose_mentions(sources, groups=groups, extractor=extractor,
                                        labels_per_batch=c_runner.LABELS_PER_BATCH)
               if groups else {"execution_status": "not_requested", "spans": []})
        for span in ner["spans"]:
            span["concept_iri"] = vocabulary["entries"][span["label"]]["iri"]
            span["extraction_role"] = vocabulary["entries"][span["label"]]["role"]
        requested = {item["group"] for item in plan["propose_mentions"]}
        ner.update(backend=c_runner.BACKEND, architecture=c_runner.ARCHITECTURE,
                   vocabulary_profile=vocabulary["profile"],
                   vocabulary_sha256=digest(directory / "vocabulary.json"),
                   empty_requested_groups=sorted(requested - set(groups)))
        inspection = c_runner.inspect_evidence(sources, list(sources))
        tools = {
            "get_schema_card": [c_runner.get_schema_card(catalog, item["class_iri"], list(catalog))
                                for item in plan["get_schema_card"]],
            "inspect_evidence": inspection, "propose_mentions": ner,
            "source_spans": c_runner.span_catalog(sources, ner, inspection),
        }
        validate_tool_sources(tools, sources, vocabulary, catalog, plan)
        write(directory / "tools.json", tools)
        rows.append(ner_row(scope, ner, groups, digest(directory / "vocabulary.json")))
        print({"arm": ARM, "ner": rows[-1]}, flush=True)
    return rows


def reuse_prepared(prepared_dir, output, manifest, catalog, profiles):
    prepared = read(prepared_dir / "result.json")
    c_runner.verify_prepared_identity(prepared, manifest)
    for key in ("metadata_hashes", "scope_input_hashes", "baseline_demand_hashes", "analysis_id",
                "baseline_run_id", "max_model_calls", "model", "declared_model_revision",
                "max_tokens", "max_attempts", "timeout_retries", "truncation_max_tokens"):
        if prepared.get(key) != manifest[key]:
            raise ValueError("prepared_D_identity_changed:" + key)
    if ({name: digest(prepared_dir / name) for name in ROOT_INPUT_FILES}
            != manifest["input_hashes"]
            or {name: digest(prepared_dir / "metadata" / name) for name in METADATA_FILES}
            != manifest["metadata_hashes"]
            or {name: digest(prepared_dir / "code" / name) for name in manifest["code_hashes"]}
            != manifest["code_hashes"]
            or scope_input_hashes(prepared_dir) != manifest["scope_input_hashes"]):
        raise ValueError("prepared_D_actual_inputs_changed")
    if tool_hashes(prepared_dir) != prepared.get("prepared_tool_hashes"):
        raise ValueError("prepared_D_tool_digest_mismatch")
    rows = []
    for scope in SCOPES:
        current = output / ARM / scope
        tools = read(prepared_dir / ARM / scope / "tools.json")
        sources, plan = read(current / "sources.json"), read(current / "plan/proposal.json")
        validate_tool_sources(tools, sources, profiles[scope], catalog, plan)
        ner = tools["propose_mentions"]
        if ner["execution_status"] not in {"completed", "not_requested"}:
            raise ValueError("prepared_D_scope_incomplete:" + scope)
        rows.append(ner_row(scope, ner, requested_groups(plan, profiles[scope]),
                            digest(current / "vocabulary.json")))
    # Complete every check before mutating any destination tools.
    for scope in SCOPES:
        shutil.copyfile(prepared_dir / ARM / scope / "tools.json",
                        output / ARM / scope / "tools.json")
    manifest.update(ner_reused_from=prepared["run_id"],
                    ner_wall_seconds=prepared["ner_wall_seconds"])
    return rows


def run_candidate_case(client, directory, run_id, catalog):
    row = c_runner.run_candidate_case(client, directory, run_id, catalog)
    row.update(arm=ARM, plan_reused=False, class_slots_reused=True, refs_retrieved=True)
    write(directory / "result.json", row)
    return row


def run_qwen(client, output, manifest, catalog):
    if manifest["results"]:
        raise ValueError("D_run_already_has_model_results")
    for scope in SCOPES:
        calls = sum(len(row["calls"]) for row in manifest["results"])
        if calls + 2 > MAX_MODEL_CALLS:
            raise ValueError("summary_experiment_budget_exhausted")
        row = run_candidate_case(client, output / ARM / scope, manifest["run_id"] + "-D", catalog)
        manifest["results"].append(row)
        write(output / "result.json", manifest)
        if len(row["calls"]) > 2 or calls + len(row["calls"]) > MAX_MODEL_CALLS:
            raise ValueError("summary_experiment_budget_exceeded")
        print({"arm": ARM, "scope": scope, "status": row["status"],
               "accepted": len(row["accepted"]), "error": row.get("error_code")}, flush=True)


def summarize(rows):
    summary = c_runner.summarize_c(rows)
    summary.pop("local_checklists_passed")
    summary.pop("reused_plan_count")
    summary.update(arm=ARM, quality_scoring="not_performed_on_old_scope_silver",
                   complete_document_quality_claimed=False)
    return summary


def execute(args):
    # A new output is mandatory; never overwrite a frozen C or D run.
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "version": VERSION, "arm": ARM, "run_id": "cmc-summary-tools-" + uuid4().hex[:16],
        "started_at": datetime.now(UTC).isoformat(), "status": "preparing", "results": [],
        "max_model_calls": MAX_MODEL_CALLS, "max_attempts": 1, "timeout_retries": 0,
        "truncation_max_tokens": None, "max_tokens": 8192, "temperature": 0,
        "reference_is_model_input": False, "summary_is_fact_evidence": False,
        "summary_is_qwen_input": False, "complete_document_quality_claimed": False,
    }
    write(args.output / "result.json", manifest)
    stage = "baseline_and_model_identity"
    try:
        old, ir, catalog = verify_baseline(args.baseline, args.model_path, args.device)
        stage = "metadata_identity"
        metadata = load_metadata(args.metadata_dir, ir, digest(args.baseline / "ir.json"))
        for name in ("source.docx", "ir.json", "cards.json"):
            shutil.copyfile(args.baseline / name, args.output / name)
        for child in ("metadata", "code"):
            (args.output / child).mkdir()
        for name in METADATA_FILES:
            shutil.copyfile(args.metadata_dir / name, args.output / "metadata" / name)
        paths = code_paths()
        for name, path in paths.items():
            shutil.copyfile(path, args.output / "code" / name)
        manifest.update(
            source_sha256=old["source_sha256"], analysis_id=ir.analysis_id,
            ontology_hash=old["ontology_hash"], baseline_run_id=old["run_id"],
            baseline_comparison=str(args.baseline / "result.json"),
            model=old["model"], declared_model_revision=old["declared_model_revision"],
            gliner2_model=old["gliner2_model"], packages=old["packages"], ner_device=args.device,
            backend=c_runner.BACKEND, architecture=c_runner.ARCHITECTURE,
            labels_per_batch=c_runner.LABELS_PER_BATCH, reused_plan_arm="C_class_slots_only",
            metadata_hashes={name: digest(args.output / "metadata" / name)
                             for name in METADATA_FILES},
            code_hashes={name: digest(path) for name, path in paths.items()},
            baseline_demand_hashes={scope: {
                name: digest(args.baseline / "C" / scope / name)
                for name in ("subject-cards.json", "plan/proposal.json", "vocabulary.json")
            } for scope in SCOPES},
        )
        stage = "summary_retrieval"
        _, profiles, retrieval_summary = prepare_inputs(
            args.baseline, args.metadata_dir, args.output, args.ontology_dir, ir, metadata, catalog,
        )
        manifest.update(
            status="prepared", retrieval_summary=retrieval_summary,
            input_hashes={name: digest(args.output / name) for name in ROOT_INPUT_FILES},
            scope_input_hashes=scope_input_hashes(args.output),
        )
        write(args.output / "result.json", manifest)
        if args.prepare_only:
            return manifest
        stage = "prepared_tools_identity" if args.prepared_tools else "ner"
        started = monotonic()
        if args.prepared_tools:
            rows = reuse_prepared(args.prepared_tools, args.output, manifest, catalog, profiles)
        else:
            rows = prepare_ner(args.output, catalog, profiles, args.model_path, args.device)
            manifest["ner_wall_seconds"] = round(monotonic() - started, 3)
        manifest.update(ner_results=rows, prepared_tool_hashes=tool_hashes(args.output),
                        status="ner_completed" if all(row["execution_status"] in {
                            "completed", "not_requested"} for row in rows) else "ner_incomplete")
        write(args.output / "result.json", manifest)
        if args.ner_only or manifest["status"] != "ner_completed":
            return manifest
        stage = "qwen_identity"
        import pyshacl

        from app.config import settings
        from app.services.llm.local_client import get_local_llm

        manifest["pyshacl_version"] = pyshacl.__version__
        if (settings.local_llm_model != manifest["model"]
                or settings.local_llm_model_revision != manifest["declared_model_revision"]):
            raise ValueError("frozen_qwen_identity_mismatch")
        client = get_local_llm()
        if client is None or settings.local_llm_model not in c_runner.model_identity(
            client, args.output,
        ):
            raise ValueError("configured_qwen_not_served")
        stage, started = "qwen", monotonic()
        manifest["status"] = "running"
        write(args.output / "result.json", manifest)
        run_qwen(client, args.output, manifest, catalog)
        manifest.update(status="completed", finished_at=datetime.now(UTC).isoformat(),
                        qwen_wall_seconds=round(monotonic() - started, 3),
                        summary=summarize(manifest["results"]))
        write(args.output / "summary.json", manifest["summary"])
    except Exception as exc:
        manifest.update(status="failed", failure_stage=stage, error_type=type(exc).__name__,
                        finished_at=datetime.now(UTC).isoformat())
        if isinstance(exc, ValueError):
            manifest["error_code"] = str(exc)[:240]
        write(args.output / "result.json", manifest)
        raise
    write(args.output / "result.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "metadata-dir", "output", "ontology-dir", "model-path"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--ner-only", action="store_true")
    parser.add_argument("--prepared-tools", type=Path)
    args = parser.parse_args(argv)
    if args.prepare_only and args.prepared_tools:
        parser.error("--prepared-tools cannot be combined with --prepare-only")
    execute(args)


if __name__ == "__main__":
    main()
