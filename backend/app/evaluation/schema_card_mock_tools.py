"""Three-arm static-Mock equipment experiment; no business writes or automatic retries."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.evaluation import schema_card_gliner2 as ner_runner
from app.evaluation import schema_card_summary_tools as summary_runner
from app.evaluation.schema_card_mock_protocol import (
    CANDIDATE_PROMPT,
    EQ,
    PROPERTIES,
    VERIFY_PROMPT,
    finalize,
    freeze_proposal,
    proposal_schema,
    scoped_cards,
    verification_schema,
)
from app.evaluation.schema_card_mock_retrieval import prepare_retrieval
from app.evaluation.schema_card_probe import Recorder, digest, read, write
from app.evaluation.schema_card_tightened import source_prompt, validate_schema
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.gliner_extractor import _valid_source_span
from app.services.extraction.tool_validation.mock_entities import FrozenEquipmentCatalog

VERSION = "cmc-mock-equipment-three-arm-v1"
ARMS = ("E0", "E1", "E2")
MAX_CALLS = 18
MAX_INPUT_CHARACTERS = 60_000
MAX_NER_SPANS = 48
MAX_TOKENS = 8192


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def code_paths():
    paths = summary_runner.code_paths()
    here = Path(__file__).parent
    for name in ("schema_card_mock_tools.py", "schema_card_mock_protocol.py",
                 "schema_card_mock_retrieval.py"):
        paths[name] = here / name
    extraction = here.parent / "services/extraction"
    for name, path in {
        "mock_entities.py": extraction / "tool_validation/mock_entities.py",
        "external_records.py": extraction / "external_records.py",
        "equipment_source.py": extraction / "equipment_source.py",
    }.items():
        paths[name] = path
    return paths


def compact_ner(ner):
    seen, candidates = set(), []
    for span in sorted(ner["spans"], key=lambda s: (-s["score"], s["ref"], s["start"], s["end"],
                                                  s["label"])):
        key = (span["ref"], span["start"], span["end"], span["label"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append({k: span[k] for k in ("ref", "start", "end", "text", "label", "score")})
    return {"execution_status": ner["execution_status"], "spans": candidates[:MAX_NER_SPANS],
            "available_unique_spans": len(candidates),
            "omitted_spans": len(candidates[MAX_NER_SPANS:]),
            "selection": "score_desc_then_ref_offsets_label_fixed_top48",
            "semantic_status": "not_checked"}


def prepared_identity(manifest):
    return {k: manifest[k] for k in (
        "version", "source_sha256", "input_hashes", "metadata_hashes", "code_hashes", "case_hashes",
        "model", "declared_model_revision", "gliner2_model", "packages", "device",
        "max_calls", "max_input_characters", "max_ner_spans", "max_tokens",
    )}


def prepare(args):
    if args.output.exists():
        raise ValueError("new_output_directory_required")
    args.output.mkdir(parents=True)
    baseline = read(args.baseline / "result.json")
    if baseline.get("version") != summary_runner.VERSION or baseline.get("status") != "completed":
        raise ValueError("frozen_D_baseline_required_execution_finished_not_quality_passed")
    if digest(args.baseline / "source.docx") != summary_runner.SOURCE_SHA256:
        raise ValueError("source_document_changed")
    for name in ("ir.json", "cards.json"):
        if digest(args.baseline / name) != baseline["input_hashes"][name]:
            raise ValueError("frozen_baseline_input_changed")
    for name, expected in baseline["code_hashes"].items():
        if (name not in code_paths() or digest(code_paths()[name]) != expected
                or digest(args.baseline / "code" / name) != expected):
            raise ValueError("D_dependency_changed:" + name)
    ir = DocumentIR.model_validate(read(args.baseline / "ir.json"))
    if (ir.document_hash != summary_runner.SOURCE_SHA256
            or baseline["source_sha256"] != summary_runner.SOURCE_SHA256):
        raise ValueError("fixed_report_IR_identity_mismatch")
    metadata = summary_runner.load_metadata(args.baseline / "metadata", ir,
                                            digest(args.baseline / "ir.json"))
    catalog, records = read(args.baseline / "cards.json"), read(args.mock_file)
    mock = FrozenEquipmentCatalog.from_records(records)
    retrieval = prepare_retrieval(ir, metadata, catalog, records)
    if any(len(retrieval[key]["cases"]) != 3 for key in ("baseline", "mock_retrieval")):
        raise ValueError("three_complete_record_cases_required")
    for name in ("source.docx", "ir.json", "cards.json"):
        shutil.copy2(args.baseline / name, args.output / name)
    shutil.copytree(args.baseline / "metadata", args.output / "metadata")
    shutil.copy2(args.mock_file, args.output / "mock-equipment.json")
    write(args.output / "retrieval.json", retrieval)
    write(args.output / "scope-cards.json", scoped_cards(catalog))
    # Retain the existing Equipment concept and three property labels. No instance
    # names/IDs become labels; ProcessEquipment typing remains a Qwen source decision.
    vocabulary = ner_runner.build_extraction_vocabulary(catalog, [EQ + "Equipment"],
                                                       ontology_dir=args.ontology_dir)
    requested = {EQ + "Equipment", *PROPERTIES}
    vocabulary["entries"] = {k: v for k, v in vocabulary["entries"].items()
                             if v["iri"] in requested}
    vocabulary["groups"] = {
        group: [label for label in labels if label in vocabulary["entries"]]
        for group, labels in vocabulary["groups"].items()
    }
    if {v["iri"] for v in vocabulary["entries"].values()} != requested:
        raise ValueError("equipment_SKOS_vocabulary_incomplete")
    vocabulary["scope_note"] = (
        "Existing Equipment plus ID/name/specification; no Mock instance labels"
    )
    write(args.output / "vocabulary.json", vocabulary)
    cases = []
    for arm in ARMS:
        mode = "mock_retrieval" if arm == "E2" else "baseline"
        for case in retrieval[mode]["cases"]:
            local = deepcopy(case)
            local.update(arm=arm, scope_id="record-" + case["record_id"][:12])
            directory = args.output / arm / local["scope_id"]
            directory.mkdir(parents=True)
            write(directory / "case.json", local)
            write(directory / "sources.json", local["sources"])
            tools = (mock.search(local["sources"], limit=8) if arm != "E0" else
                     {"execution_status": "not_requested", "candidates": [],
                      "reason": "E0_mock_not_visible"})
            write(directory / "mock-search.json", tools)
            cases.append({"arm": arm, "scope_id": local["scope_id"],
                          "record_id": local["record_id"],
                          "directory": str(directory.relative_to(args.output)),
                          "case_sha256": digest(directory / "case.json"),
                          "source_sha256": digest(directory / "sources.json"),
                          "mock_search_sha256": digest(directory / "mock-search.json")})
    paths = code_paths()
    (args.output / "code").mkdir()
    for name, path in paths.items():
        shutil.copy2(path, args.output / "code" / name)
    input_files = ("source.docx", "ir.json", "cards.json", "mock-equipment.json", "retrieval.json",
                   "scope-cards.json", "vocabulary.json")
    manifest = {
        "version": VERSION, "run_id": "cmc-mock-" + uuid4().hex[:16],
        "started_at": datetime.now(UTC).isoformat(), "status": "prepared", "results": [],
        "source_sha256": summary_runner.SOURCE_SHA256,
        "model": baseline["model"], "declared_model_revision": baseline["declared_model_revision"],
        "gliner2_model": ner_runner.verify_model_files(args.model_path),
        "packages": ner_runner.package_versions(), "device": args.device,
        "code_hashes": {name: digest(path) for name, path in paths.items()},
        "input_hashes": {name: digest(args.output / name) for name in input_files},
        "metadata_hashes": {name: digest(args.output / "metadata" / name)
                            for name in summary_runner.METADATA_FILES},
        "case_hashes": cases, "max_calls": MAX_CALLS, "max_input_characters": MAX_INPUT_CHARACTERS,
        "max_ner_spans": MAX_NER_SPANS, "max_tokens": MAX_TOKENS,
        "reference_is_input": False, "summary_is_evidence": False,
        "static_mock_not_database_mock": True, "new_summary_calls": 0,
    }
    if (manifest["gliner2_model"] != baseline["gliner2_model"]
            or manifest["packages"] != baseline["packages"]
            or manifest["device"] != baseline["ner_device"]):
        raise ValueError("GLiNER_model_or_runtime_identity_changed")
    write(args.output / "result.json", manifest)
    return manifest, catalog, mock, vocabulary


def prepare_ner(args, manifest, vocabulary):
    groups = {g: labels for g, labels in vocabulary["groups"].items() if labels}
    descriptions = {label: entry["description"] for label, entry in vocabulary["entries"].items()}
    extractor = None
    cached, rows, hashes = {}, [], {}
    if args.prepared_tools:
        prior = read(args.prepared_tools / "result.json")
        if (prior["status"] != "ner_completed"
                or prepared_identity(prior) != prepared_identity(manifest)):
            raise ValueError("prepared_ner_identity_mismatch")
        for name, expected in prior["input_hashes"].items():
            if digest(args.prepared_tools / name) != expected:
                raise ValueError("prepared_input_changed")
        for name, expected in prior["code_hashes"].items():
            if digest(args.prepared_tools / "code" / name) != expected:
                raise ValueError("prepared_code_changed")
        for name, expected in prior["metadata_hashes"].items():
            if digest(args.prepared_tools / "metadata" / name) != expected:
                raise ValueError("prepared_metadata_changed")
        manifest["ner_reused_from"] = prior["run_id"]
    else:
        extractor = ner_runner.Gliner2Extractor(
            args.model_path, descriptions=descriptions, device=args.device,
            word_splitter="char", max_len=160,
        )
    started = monotonic()
    for case in manifest["case_hashes"]:
        relative = Path(case["directory"])
        directory = args.output / relative
        sources = read(directory / "sources.json")
        key = canonical_hash(sources)
        if args.prepared_tools:
            path = args.prepared_tools / relative / "ner.json"
            if digest(path) != prior["ner_hashes"][str(relative)]:
                raise ValueError("prepared_ner_changed")
            for name in ("case.json", "sources.json", "mock-search.json"):
                if digest(directory / name) != digest(args.prepared_tools / relative / name):
                    raise ValueError("prepared_case_changed")
            ner = read(path)
        elif key in cached:
            ner = deepcopy(cached[key])
        else:
            ner = ner_runner.propose_mentions(sources, groups=groups, extractor=extractor,
                                               labels_per_batch=1)
            cached[key] = deepcopy(ner)
        if ner["execution_status"] != "completed":
            raise ValueError("required_ner_not_completed")
        for span in ner["spans"]:
            if (span.get("ref") not in sources
                    or not _valid_source_span(span, sources[span["ref"]]["text"],
                                              set(vocabulary["entries"]))):
                raise ValueError("ner_source_span_mismatch")
            if span["label"] not in vocabulary["entries"]:
                raise ValueError("ner_label_outside_frozen_vocabulary")
        write(directory / "ner.json", ner)
        write(directory / "ner-model-view.json", compact_ner(ner))
        hashes[str(relative)] = digest(directory / "ner.json")
        rows.append({**{k: case[k] for k in ("arm", "scope_id", "record_id")},
                     "source_input_hash": key, "span_count": len(ner["spans"]),
                     "model_span_count": len(compact_ner(ner)["spans"])})
    manifest.update(status="ner_completed", ner_results=rows, ner_hashes=hashes,
                    ner_unique_inputs=len({r["source_input_hash"] for r in rows}),
                    ner_wall_seconds=(prior["ner_wall_seconds"] if args.prepared_tools
                                      else round(monotonic() - started, 3)))
    write(args.output / "result.json", manifest)


def invoke(client, directory, run_id, case_id, stage, prompt, payload, schema, calls):
    from app.services.llm.local_client import chat_with_schema
    from app.services.llm.model_runtime import model_scope

    if len(calls) >= 2:
        raise ValueError("case_request_budget_exhausted")
    user = json.dumps({"input": payload, "output_schema": schema}, ensure_ascii=False)
    if len(prompt) + len(user) > MAX_INPUT_CHARACTERS:
        raise ValueError("model_input_character_budget_exceeded")
    directory.mkdir()
    write(directory / "schema.json", schema)
    write(directory / "input-size.json", {"message_characters": len(prompt) + len(user),
                                           "maximum": MAX_INPUT_CHARACTERS})
    recorder = Recorder(client, directory)
    try:
        with model_scope(run_id=run_id, task_id=case_id, stage="mock_equipment_" + stage):
            result = chat_with_schema(
                recorder, system=prompt, user=user, schema=schema, schema_name="mock_" + stage,
                temperature=0, enable_thinking=False, max_tokens=MAX_TOKENS,
                max_attempts=1, timeout_retries=0, truncation_max_tokens=None,
                timeout_s=240, total_timeout_s=300, raise_on_error=True,
            )
        validate_schema(result, schema)
        write(directory / "proposal.json", result)
        return result
    finally:
        ledger = [{**row, "stage": stage} for row in recorder.calls]
        calls.extend(ledger)
        write(directory / "calls.json", ledger)


def run_case(client, root, case, manifest, catalog, mock):
    directory = root / case["directory"]
    row = {k: case[k] for k in ("arm", "scope_id", "record_id")}
    row.update(status="failed", calls=[], contracts_passed=0)
    stage = "input"
    try:
        local = read(directory / "case.json")
        sources = read(directory / "sources.json")
        search = read(directory / "mock-search.json")
        schema = proposal_schema(sources, [c["key"] for c in search["candidates"]])
        roles = {key: local["roles"][key] for key in ("primary_refs", "context_refs")}
        common = {"source": source_prompt(sources), "record_roles": roles,
                  "cards": scoped_cards(catalog)}
        stage = "candidates"
        proposal = invoke(client, directory / stage, manifest["run_id"],
                          case["arm"] + ":" + case["scope_id"], stage, CANDIDATE_PROMPT,
                          {**common, "ner": read(directory / "ner-model-view.json"),
                           "external_candidates": search}, schema, row["calls"])
        row["contracts_passed"] = 1
        frozen = freeze_proposal(proposal, sources, catalog, mock, search["candidates"])
        write(directory / "frozen-candidates.json", frozen)
        stage = "verification"
        verdicts = invoke(client, directory / stage, manifest["run_id"],
                          case["arm"] + ":" + case["scope_id"], stage, VERIFY_PROMPT,
                          {**common, "targets": frozen["targets"], "external_candidates": search},
                          verification_schema(frozen["targets"], sources), row["calls"])
        row["contracts_passed"] = 2
        row.update(finalize(frozen, verdicts, sources, catalog, mock), status="complete")
    except Exception as exc:
        row.update(error_type=type(exc).__name__, failure_stage=stage)
        if isinstance(exc, ValueError) or type(exc).__name__ == "StructuredModelError":
            row["error_code"] = str(exc)[:240]
    write(directory / "result.json", row)
    return row


def execute(args):
    manifest, catalog, mock, vocabulary = prepare(args)
    if args.prepare_only:
        return manifest
    try:
        prepare_ner(args, manifest, vocabulary)
        if args.ner_only:
            return manifest
        import pyshacl

        from app.config import settings
        from app.services.llm.local_client import get_local_llm

        if (settings.local_llm_model != manifest["model"]
                or settings.local_llm_model_revision != manifest["declared_model_revision"]):
            raise ValueError("configured_Qwen_identity_changed")
        client = get_local_llm()
        if (client is None
                or manifest["model"] not in ner_runner.model_identity(client, args.output)):
            raise ValueError("configured_Qwen_not_served")
        manifest["pyshacl_version"] = pyshacl.__version__
        manifest["status"] = "running"
        write(args.output / "result.json", manifest)
        cases = manifest["case_hashes"]
        sequence = []
        for baseline_case in [c for c in cases if c["arm"] == "E0"]:
            sequence.extend(next(c for c in cases if c["arm"] == arm
                                 and c["record_id"] == baseline_case["record_id"])
                            for arm in ("E0", "E1"))
        sequence += [c for c in cases if c["arm"] == "E2"]
        started = monotonic()
        for case in sequence:
            if sum(len(row["calls"]) for row in manifest["results"]) + 2 > MAX_CALLS:
                raise ValueError("experiment_request_budget_exhausted")
            row = run_case(client, args.output, case, manifest, catalog, mock)
            manifest["results"].append(row)
            write(args.output / "result.json", manifest)
            print({"arm": row["arm"], "scope": row["scope_id"], "status": row["status"],
                   "contracts": row["contracts_passed"], "error": row.get("error_code")},
                  flush=True)
        manifest.update(status="completed", qwen_wall_seconds=round(monotonic() - started, 3),
                        finished_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, ValueError):
            manifest["error_code"] = str(exc)[:240]
        write(args.output / "result.json", manifest)
        raise
    write(args.output / "result.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mock-file", type=Path, required=True)
    parser.add_argument("--ontology-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--prepared-tools", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prepare-only", action="store_true")
    group.add_argument("--ner-only", action="store_true")
    execute(parser.parse_args())


if __name__ == "__main__":
    main()
