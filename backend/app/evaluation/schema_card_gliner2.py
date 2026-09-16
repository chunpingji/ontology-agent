"""GLiNER2.5 boundary + frozen SKOS experiment on historical CMC tool plans.

The historical B plans are fixed inputs, not new model calls. Each C scope has
at most two new Qwen requests. Vocabulary compilation never reads a reference.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic

from app.evaluation.schema_card_evidence import build_sources
from app.evaluation.schema_card_probe import digest, read, write
from app.evaluation.schema_card_tools import (
    CANDIDATE_PROMPT,
    GROUPS,
    VERIFY_PROMPT,
    candidate_schema,
    finalize,
    freeze_candidates,
    invoke,
    model_identity,
    prepare,
    replay_citations,
    score_case,
    score_extended,
    source_prompt,
    span_catalog,
    validate_schema,
    verification_schema,
)
from app.services.extraction.gliner2_extractor import Gliner2Extractor
from app.services.extraction.tool_validation.evidence import get_schema_card, inspect_evidence
from app.services.extraction.tool_validation.mentions import propose_mentions
from app.services.extraction.tool_validation.vocabulary import (
    DEFAULT_OVERLAY,
    build_extraction_vocabulary,
)

MODEL_REPO = "fastino/gliner2.5-multi-v1"
MODEL_REVISION = "aaecfe45db1d828c963717054ccb868e8ad1f1d5"
BACKEND = "gliner2.5"
ARCHITECTURE = "boundary"
VERSION = "cmc-gliner25-boundary-skos-v1"
MAX_MODEL_CALLS = 16
PACKAGES = ("gliner2", "transformers", "huggingface_hub", "torch", "protobuf", "tokenizers")
REQUIRED_MODEL_FILES = frozenset({
    "config.json", "encoder_config/config.json", "tokenizer_config.json",
    "tokenizer.json", "model.safetensors",
})
LABELS_PER_BATCH = 1
TOOL_FILES = ("sources.json", "plan/proposal.json", "plan/schema.json", "vocabulary.json",
              "tools.json")


def invoke_bounded(client, directory, run_id, scope, stage, prompt, payload, schema, calls):
    if len(calls) >= 2:
        raise ValueError("gliner2_scope_request_budget_exhausted")
    return invoke(client, directory, run_id, scope, stage, prompt, payload, schema, calls)


def model_tool_view(tools):
    """Keep every source-backed mention; omit duplicate spans and batch diagnostics."""
    ner = tools["propose_mentions"]
    keep = {"tool", "backend", "architecture", "execution_status", "semantic_status",
            "fact_eligible", "spans", "issues", "vocabulary_profile", "vocabulary_sha256",
            "empty_requested_groups"}
    return {**tools, "propose_mentions": {key: value for key, value in ner.items() if key in keep}}


def run_candidate_case(client, directory, run_id, cards):
    """Consume prepared local tools; no plan request or NER retry happens here."""
    result = {"arm": "C", "scope_id": directory.name, "status": "failed", "calls": [],
              "accepted": [], "observations": [], "unresolved": [], "rejected": [],
              "contract_passes": 0, "plan_reused": True, "new_request_budget": 2}
    stage = "prepared_tools"
    try:
        sources, plan = read(directory / "sources.json"), read(directory / "plan/proposal.json")
        validate_schema(plan, read(directory / "plan/schema.json"))
        tools = read(directory / "tools.json")
        ner = tools["propose_mentions"]
        result["ner"] = {key: ner.get(key) for key in
                         ("execution_status", "coverage", "timing", "issues", "limits")}
        result["ner"]["span_count"] = len(ner.get("spans", []))
        if ner["execution_status"] not in {"completed", "not_requested"}:
            raise ValueError("gliner2_requested_but_incomplete")
        spans = tools["source_spans"]
        schema, subjects, bindings, menus = candidate_schema(plan, sources, cards, spans)
        write(directory / "subject-cards.json", menus)
        stage = "candidates"
        raw = invoke_bounded(
            client, directory / stage, run_id, directory.name, stage, CANDIDATE_PROMPT,
            {"source": source_prompt(sources), "subjects": menus,
             "tool_results": model_tool_view(tools)},
            schema, result["calls"],
        )
        result["contract_passes"] = 1
        proposal = replay_citations(raw, sources, spans, isolate_errors=True)
        active, claims, observations, frame_rejected = freeze_candidates(
            proposal, subjects, bindings, sources,
        )
        frozen = {"subjects": active, "claims": claims, "observations": observations}
        write(directory / "frozen-candidates.json", frozen)
        result["frames"] = {key: value for key, value in active.items() if key != "document"}
        vschema = verification_schema(active, claims, observations, sources)
        stage = "verification"
        semantic_claims = [{key: value for key, value in claim.items()
                            if key not in {"binding", "metric_precheck"}} for claim in claims]
        verdicts = invoke_bounded(
            client, directory / stage, run_id, directory.name, stage, VERIFY_PROMPT,
            {"source": source_prompt(sources), "cards": menus, "subjects": active,
             "claims": semantic_claims, "observations": observations}, vschema, result["calls"],
        )
        result["contract_passes"] = 2
        result.update(finalize(active, claims, observations, verdicts, bindings, sources))
        result["rejected"].extend(frame_rejected)
        result.update(status="complete", proposed_claim_count=len(claims),
                      proposed_observation_count=len(observations),
                      proposed_subject_count=len(active) - 1)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, failure_stage=stage)
        if isinstance(exc, ValueError) or type(exc).__name__ == "StructuredModelError":
            result["error_code"] = str(exc)[:200]
    write(directory / "result.json", result)
    return result


def summarize_c(rows):
    calls = [call for row in rows for call in row["calls"]]
    return {
        "scopes": len(rows), "completed": sum(r["status"] == "complete" for r in rows),
        "new_http_calls": len(calls), "reused_plan_count": len(rows),
        "contracts_passed": sum(r["contract_passes"] for r in rows),
        "accepted_candidates": sum(len(r["accepted"]) for r in rows),
        "observations": sum(len(r["observations"]) for r in rows),
        "unresolved": sum(len(r["unresolved"]) for r in rows),
        "rejected": sum(len(r["rejected"]) for r in rows),
        "local_checklists_passed": sum(r.get("acceptance", {}).get("all_pass", False)
                                       for r in rows),
        "prompt_tokens": sum(c.get("usage", {}).get("prompt_tokens", 0) for c in calls),
        "completion_tokens": sum(c.get("usage", {}).get("completion_tokens", 0) for c in calls),
        "usage_reported_calls": sum(bool(c.get("usage")) for c in calls),
        "usage_missing_calls": sum(not c.get("usage") for c in calls),
        "token_count_scope": "reported_usage_only",
        "http_seconds": round(sum(c["seconds"] for c in calls), 3),
        "finish_reasons": dict(Counter(c.get("finish_reason", "error") for c in calls)),
    }


def verify_model_files(model_path):
    model_path = Path(model_path)
    manifest = read(model_path / "DOWNLOAD-MANIFEST.json")
    if not isinstance(manifest, dict):
        raise ValueError("gliner2_model_manifest_invalid")
    if manifest.get("repo") != MODEL_REPO or manifest.get("revision") != MODEL_REVISION:
        raise ValueError("gliner2_frozen_model_identity_mismatch")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("gliner2_model_manifest_files_empty")
    paths, resolved_paths = set(), set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not entry["path"] or type(entry.get("bytes")) is not int
                or entry["bytes"] <= 0 or not isinstance(entry.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
            raise ValueError("gliner2_model_manifest_entry_invalid")
        name = entry["path"]
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("model_manifest_path_outside_directory")
        if relative.as_posix() != name or "\\" in name:
            raise ValueError("gliner2_model_manifest_path_not_canonical")
        path = model_path / name
        resolved = path.resolve()
        if not resolved.is_relative_to(model_path.resolve()):
            raise ValueError("model_manifest_path_outside_directory")
        if name in paths or resolved in resolved_paths:
            raise ValueError("gliner2_model_manifest_duplicate_path:" + name)
        paths.add(name)
        resolved_paths.add(resolved)
    missing = REQUIRED_MODEL_FILES - paths
    if missing:
        raise ValueError("gliner2_model_required_files_missing:" + ",".join(sorted(missing)))
    for entry in entries:
        path = model_path / entry["path"]
        if not path.is_file():
            raise ValueError("gliner2_model_file_missing:" + entry["path"])
        # The fixed F32 checkpoint is over 1 GB; hash without loading it all into RAM.
        with path.open("rb") as stream:
            actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != entry["bytes"] or actual_digest != entry["sha256"]:
            raise ValueError("gliner2_model_digest_mismatch:" + entry["path"])
    config = read(model_path / "config.json")
    if not isinstance(config, dict) or config.get("architecture") != ARCHITECTURE:
        raise ValueError("gliner2_model_architecture_mismatch")
    return manifest


def package_versions():
    return {name: importlib.metadata.version(name) for name in PACKAGES}


def verify_prepared_identity(prepared, manifest):
    if prepared.get("status") != "ner_completed":
        raise ValueError("prepared_ner_must_be_completed")
    for key in ("source_sha256", "ontology_hash", "input_hashes", "code_hashes",
                "gliner2_model", "ner_device", "labels_per_batch", "reused_plan_arm",
                "version", "backend", "architecture", "packages"):
        if key not in prepared or prepared[key] != manifest[key]:
            raise ValueError("prepared_ner_identity_changed:" + key)


def prepared_tool_hashes(output, cases):
    return {case["scope_id"]: {name: digest(output / "C" / case["scope_id"] / name)
                              for name in TOOL_FILES} for case in cases}


def verify_prepared_tools(prepared_dir, prepared, baseline, cases, ir):
    if digest(prepared_dir / "vocabularies.json") != prepared["vocabulary_sha256"]:
        raise ValueError("prepared_vocabulary_digest_mismatch")
    if prepared_tool_hashes(prepared_dir, cases) != prepared["prepared_tool_hashes"]:
        raise ValueError("prepared_tool_digest_mismatch")
    for case in cases:
        scope = case["scope_id"]
        directory = prepared_dir / "C" / scope
        if read(directory / "sources.json") != build_sources(case, ir):
            raise ValueError("prepared_source_does_not_match_frozen_ir:" + scope)
        for name in ("plan/proposal.json", "plan/schema.json"):
            if digest(directory / name) != digest(baseline / "B" / scope / name):
                raise ValueError("prepared_plan_does_not_match_baseline:" + scope)


def prepare_tools(baseline, output, cards, cases, ir, ontology_dir, model_path, device):
    """Freeze every selected vocabulary before loading models or reading silver labels."""
    profiles, descriptions = {}, {}
    for case in cases:
        scope = case["scope_id"]
        old = baseline / "B" / scope
        directory = output / "C" / scope
        directory.mkdir(parents=True)
        (directory / "plan").mkdir()
        sources = build_sources(case, ir)
        if sources != read(old / "sources.json"):
            raise ValueError("frozen_scope_source_mismatch:" + scope)
        plan = read(old / "plan/proposal.json")
        validate_schema(plan, read(old / "plan/schema.json"))
        selected = [item["class_iri"] for item in plan["get_schema_card"]]
        vocabulary = build_extraction_vocabulary(cards, selected, ontology_dir=ontology_dir)
        if vocabulary["missing"]:
            raise ValueError("extraction_vocabulary_incomplete:" + scope)
        profiles[scope] = vocabulary
        for label, entry in vocabulary["entries"].items():
            if label in descriptions and descriptions[label] != entry["description"]:
                raise ValueError("cross_scope_label_description_conflict:" + label)
            descriptions[label] = entry["description"]
        write(directory / "vocabulary.json", vocabulary)
        write(directory / "sources.json", sources)
        write(directory / "plan/proposal.json", plan)
        shutil.copyfile(old / "plan/schema.json", directory / "plan/schema.json")
        write(directory / "plan/provenance.json", {
            "source_run": str(baseline), "arm": "B", "scope_id": scope,
            "proposal_sha256": digest(old / "plan/proposal.json"), "new_model_calls": 0,
        })
    write(output / "vocabularies.json", profiles)
    extractor = Gliner2Extractor(model_path, descriptions=descriptions, device=device,
                                word_splitter="char", max_len=160)
    rows = []
    for case in cases:
        scope = case["scope_id"]
        directory = output / "C" / scope
        sources, plan = read(directory / "sources.json"), read(directory / "plan/proposal.json")
        vocabulary = profiles[scope]
        requested = {item["group"] for item in plan["propose_mentions"]}
        groups = {group: vocabulary["groups"][group] for group in GROUPS
                  if group in requested and vocabulary["groups"][group]}
        if groups:
            ner = propose_mentions(sources, groups=groups, extractor=extractor,
                                   labels_per_batch=LABELS_PER_BATCH)
            for span in ner["spans"]:
                span["concept_iri"] = vocabulary["entries"][span["label"]]["iri"]
                span["extraction_role"] = vocabulary["entries"][span["label"]]["role"]
        else:
            ner = {"execution_status": "not_requested", "spans": []}
        ner.update(backend=BACKEND, architecture=ARCHITECTURE,
                   vocabulary_profile=vocabulary["profile"],
                   vocabulary_sha256=digest(directory / "vocabulary.json"),
                   empty_requested_groups=sorted(requested - set(groups)))
        inspection = inspect_evidence(
            sources, list(dict.fromkeys(item["ref"] for item in plan["inspect_evidence"])),
        )
        selected = [item["class_iri"] for item in plan["get_schema_card"]]
        tools = {
            "get_schema_card": [get_schema_card(cards, iri, list(cards)) for iri in selected],
            "inspect_evidence": inspection, "propose_mentions": ner,
            "source_spans": span_catalog(sources, ner, inspection),
        }
        write(directory / "tools.json", tools)
        rows.append({"scope_id": scope, "execution_status": ner["execution_status"],
                     "span_count": len(ner["spans"]), "timing": ner.get("timing"),
                     "label_count": {group: len(labels) for group, labels in groups.items()},
                     "vocabulary_sha256": digest(directory / "vocabulary.json")})
        print({"gliner2_scope": scope, **rows[-1]}, flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ontology-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--ner-only", action="store_true")
    parser.add_argument("--prepared-tools", type=Path)
    args = parser.parse_args()
    old = read(args.baseline / "result.json")
    if old["status"] != "completed":
        raise ValueError("baseline_must_be_completed")
    model_manifest = verify_model_files(args.model_path)
    manifest, cases, cards, ir = prepare(args.baseline, args.output)
    for name in ("reference.json", "acceptance.json", "cases.json", "cards.json", "ir.json"):
        if digest(args.baseline / name) != digest(args.output / name):
            raise ValueError("frozen_input_changed:" + name)
    if 2 * len(cases) > MAX_MODEL_CALLS:
        raise ValueError("gliner2_frozen_scope_budget_exceeded")
    manifest.update(version=VERSION, backend=BACKEND, architecture=ARCHITECTURE,
                    max_model_calls=MAX_MODEL_CALLS,
                    gliner2_model=model_manifest, ner_device=args.device,
                    labels_per_batch=LABELS_PER_BATCH, reused_plan_arm="B",
                    comparison_scope="NER model plus vocabulary bundle; not isolated attribution",
                    packages=package_versions())
    service = Path(__file__).parents[1] / "services/extraction"
    for path in (Path(__file__), service / "gliner2_extractor.py", DEFAULT_OVERLAY):
        shutil.copyfile(path, args.output / "code" / path.name)
        manifest["code_hashes"][path.name] = digest(path)
    started = monotonic()
    if args.prepared_tools:
        prepared = read(args.prepared_tools / "result.json")
        verify_prepared_identity(prepared, manifest)
        verify_prepared_tools(args.prepared_tools, prepared, args.baseline, cases, ir)
        shutil.copytree(args.prepared_tools / "C", args.output / "C")
        shutil.copyfile(args.prepared_tools / "vocabularies.json",
                        args.output / "vocabularies.json")
        ner_rows = prepared["ner_results"]
        manifest["ner_reused_from"] = prepared["run_id"]
        manifest["ner_wall_seconds"] = prepared["ner_wall_seconds"]
    else:
        ner_rows = prepare_tools(args.baseline, args.output, cards, cases, ir,
                                 args.ontology_dir, args.model_path, args.device)
        manifest["ner_wall_seconds"] = round(monotonic() - started, 3)
    manifest["ner_results"] = ner_rows
    manifest["vocabulary_sha256"] = digest(args.output / "vocabularies.json")
    manifest["prepared_tool_hashes"] = prepared_tool_hashes(args.output, cases)
    manifest["status"] = "ner_completed" if all(
        row["execution_status"] in {"completed", "not_requested"} for row in ner_rows
    ) else "ner_incomplete"
    write(args.output / "result.json", manifest)
    if args.ner_only or manifest["status"] != "ner_completed":
        return
    import pyshacl

    from app.config import settings
    from app.services.llm.local_client import get_local_llm

    manifest["pyshacl_version"] = pyshacl.__version__
    if (settings.local_llm_model != manifest["model"]
            or settings.local_llm_model_revision != manifest["declared_model_revision"]):
        raise ValueError("frozen_qwen_identity_mismatch")
    client = get_local_llm()
    if client is None or settings.local_llm_model not in model_identity(client, args.output):
        raise ValueError("configured_qwen_not_served")
    started = monotonic()
    manifest["status"] = "running"
    write(args.output / "result.json", manifest)
    for case in cases:
        if sum(len(row["calls"]) for row in manifest["results"]) + 2 > MAX_MODEL_CALLS:
            raise ValueError("gliner2_experiment_budget_exhausted")
        directory = args.output / "C" / case["scope_id"]
        row = run_candidate_case(client, directory, manifest["run_id"] + "-C", cards)
        manifest["results"].append(row)
        if sum(len(r["calls"]) for r in manifest["results"]) > manifest["max_model_calls"]:
            raise AssertionError("gliner2_experiment_budget_exceeded")
        write(args.output / "result.json", manifest)
        print({"scope": row["scope_id"], "status": row["status"],
               "accepted": len(row["accepted"]), "error": row.get("error_code")}, flush=True)
    reference = read(args.output / "reference.json")
    extended = read(args.output / "acceptance.json")
    for row in manifest["results"]:
        directory = args.output / "C" / row["scope_id"]
        row["acceptance"] = score_case(row, read(directory / "sources.json"),
                                       reference["cases"][row["scope_id"]])
        row["acceptance"]["evaluated"] = row["status"] == "complete"
        row["extended_acceptance"] = score_extended(row, extended)
        write(directory / "result.json", row)
    manifest.update(status="completed", finished_at=datetime.now(UTC).isoformat(),
                    qwen_wall_seconds=round(monotonic() - started, 3),
                    summary=summarize_c(manifest["results"]))
    write(args.output / "result.json", manifest)
    write(args.output / "summary.json", manifest["summary"])


if __name__ == "__main__":
    main()
