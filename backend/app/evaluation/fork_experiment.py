"""Fork frozen experimental inputs into the current runtime without model calls.

Usage: python -m app.evaluation.fork_experiment --source OLD --output NEW
The destination must not exist. Reference annotations are copied for scoring only.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import math
import os
import platform
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.cmc_benchmark import (
    digest_file,
    emit,
    read_json,
    settings_snapshot,
    summary_metadata,
    tree_digest,
    write_json,
)

CORE_MODEL_KEYS = (
    "local_llm_enabled", "local_llm_model", "local_llm_model_revision",
    "local_llm_tokenizer_backend", "local_llm_server_model_path",
    "local_llm_tokenizer_path", "local_llm_temperature",
)
FROZEN_FILES = {
    "source.docx": "document_hash",
    "schema.json": "schema_hash",
    "ir.json": "ir_hash",
}


def current_runtime():
    import app

    return Path(app.__file__).resolve().parent


def analyze_source(path, filename):
    from app.services.extraction.word_analysis import analyze_word_core

    return analyze_word_core(path, source_filename=filename)


def validate_inputs(source, manifest, summary, current_settings):
    for filename, key in FROZEN_FILES.items():
        if not manifest.get(key) or digest_file(source / filename) != manifest[key]:
            raise ValueError(f"parent {filename} hash differs from manifest")
    if not manifest.get("ontology_hash") or (
        tree_digest(source / "ontology", ".ttl") != manifest["ontology_hash"]
    ):
        raise ValueError("parent ontology hash differs from manifest")
    ir = read_json(source / "ir.json")
    for key in ("document_hash", "analysis_id"):
        if not manifest.get(key) or ir.get(key) != manifest[key]:
            raise ValueError(f"parent IR {key} differs from manifest")
    parent_settings = manifest["settings"]
    for key in CORE_MODEL_KEYS:
        if key not in parent_settings or key not in current_settings:
            raise ValueError(f"core model setting is missing: {key}")
        if parent_settings[key] != current_settings[key]:
            raise ValueError(f"current core model setting differs from parent: {key}")
    if not parent_settings["local_llm_model_revision"]:
        raise ValueError("parent model revision is not pinned")
    if summary.get("document_hash") != manifest["document_hash"]:
        raise ValueError("summary document_hash differs from parent")
    if summary.get("analysis_id", manifest["analysis_id"]) != manifest["analysis_id"]:
        raise ValueError("summary analysis_id differs from parent")
    if summary.get("model_identity") != parent_settings["local_llm_model_revision"]:
        raise ValueError("summary model revision differs from parent")
    prompt = summary.get("summary_prompt_version")
    if not prompt or any(
        snapshot.get("word_tree_summary_prompt_version") != prompt
        for snapshot in (parent_settings, current_settings)
    ):
        raise ValueError("summary prompt version differs from frozen/current settings")
    cost = summary.get("generation_seconds")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or (
        not math.isfinite(cost) or cost < 0
    ):
        raise ValueError("summary generation_seconds must preserve a finite original cost")

    tick = time.perf_counter()
    analysis = analyze_source(source / "source.docx", manifest["source_filename"])
    parse_seconds = time.perf_counter() - tick
    if analysis.ir.analysis_id != manifest["analysis_id"]:
        raise ValueError("current parser does not reproduce parent analysis_id")
    expected_metadata = summary_metadata(analysis.structure)
    metadata = summary.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != set(expected_metadata):
        raise ValueError("summary chapter identities differ from reproduced analysis")
    for node_id, expected in expected_metadata.items():
        actual = metadata[node_id]
        if not isinstance(actual, dict) or actual.get("content_hash") != expected["content_hash"]:
            raise ValueError(f"summary content_hash differs for node {node_id}")
        if actual.get("summary_source") == "llm" and (
            actual.get("summary_model") != parent_settings["local_llm_model"]
        ):
            raise ValueError(f"summary model differs for node {node_id}")
    return parse_seconds


def fork_experiment(source, output):
    source = Path(source).resolve()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"destination already exists: {output}")
    output = output.resolve()
    runtime = current_runtime()
    if source == output or source in output.parents or runtime in output.parents:
        raise ValueError("destination must be outside the parent preparation and runtime")
    if not (source / "ontology").is_dir():
        raise ValueError("parent ontology directory is missing")
    manifest_hash = digest_file(source / "manifest.json")
    summary_hash = digest_file(source / "summaries.json")
    parent = read_json(source / "manifest.json")
    summary = read_json(source / "summaries.json")
    current_settings = settings_snapshot()
    parse_seconds = validate_inputs(source, parent, summary, current_settings)
    runtime_hash = tree_digest(runtime, ".py")
    reference = source / "reference.json"
    reference_hash = digest_file(reference) if reference.is_file() else None

    # Validate first. An interrupted copy has no final manifest and cannot be run.
    output.mkdir(parents=True, exist_ok=False)
    for filename in FROZEN_FILES:
        shutil.copy2(source / filename, output / filename)
    shutil.copytree(source / "ontology", output / "ontology")
    shutil.copytree(runtime, output / "runtime" / "app",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for filename, key in FROZEN_FILES.items():
        if digest_file(output / filename) != parent[key]:
            raise RuntimeError(f"parent {filename} changed during copy")
    if tree_digest(output / "ontology", ".ttl") != parent["ontology_hash"]:
        raise RuntimeError("parent ontology changed during copy")
    if any(tree_digest(path, ".py") != runtime_hash
           for path in (runtime, output / "runtime" / "app")):
        raise RuntimeError("current runtime changed during snapshot")
    if digest_file(source / "manifest.json") != manifest_hash or (
        digest_file(source / "summaries.json") != summary_hash
    ):
        raise RuntimeError("parent manifest or summaries changed during fork")
    provenance_dir = output / "provenance"
    provenance_dir.mkdir()
    shutil.copy2(source / "manifest.json", provenance_dir / "parent_manifest.json")
    shutil.copy2(source / "summaries.json", provenance_dir / "parent_summaries.json")
    if digest_file(provenance_dir / "parent_manifest.json") != manifest_hash or (
        digest_file(provenance_dir / "parent_summaries.json") != summary_hash
    ):
        raise RuntimeError("parent provenance changed during copy")
    if reference_hash is not None:
        shutil.copy2(reference, output / "reference.json")
        if digest_file(output / "reference.json") != reference_hash:
            raise RuntimeError("scoring reference changed during copy")

    reuse = {
        "reused": True,
        "parent_prepared": str(source),
        "parent_summary_hash": summary_hash,
        "original_generation_seconds": summary["generation_seconds"],
        "new_generation_seconds": 0.0,
        "analysis_id_validation": (
            "explicit_identity_and_reproduced_chapter_hashes" if "analysis_id" in summary
            else "legacy_missing_identity_bound_by_reproduced_IR_and_all_chapter_hashes"
        ),
    }
    # Preserve both byte identity and paid cost; strengthened identity is in manifest.
    shutil.copy2(provenance_dir / "parent_summaries.json", output / "summaries.json")
    if digest_file(output / "summaries.json") != summary_hash:
        raise RuntimeError("summary bytes changed during copy")
    added = {key: value for key, value in current_settings.items()
             if key not in parent["settings"]}
    changed = {
        key: {"parent": parent["settings"][key], "current": value}
        for key, value in current_settings.items()
        if key in parent["settings"] and value != parent["settings"][key]
    }
    packages = {}
    for name in ("gliner", "torch", "openai", "pydantic", "owlready2", "python-docx"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    manifest = {
        **parent,
        "created_at": datetime.now(UTC).isoformat(),
        "preparation_kind": "fork_frozen_inputs_current_runtime",
        "source_path": str(output / "source.docx"),
        "runtime_hash": runtime_hash,
        "parse_seconds": parse_seconds,
        "settings": current_settings,
        "packages": packages,
        "hardware": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        "model_slots": {"not_sampled": "fork does not contact the model service"},
        "summary_hash": digest_file(output / "summaries.json"),
        "summary_reuse": reuse,
        "reference_status": "copied_scoring_only" if reference_hash else "no_reference_copied",
        "reference": {"path": "reference.json" if reference_hash else None,
                      "sha256": reference_hash, "usage": "scoring_only_never_model_input"},
        "parent_provenance": {
            "prepared": str(source), "manifest_hash": manifest_hash,
            "runtime_hash": parent["runtime_hash"],
            "created_at": parent.get("created_at"),
            "parse_seconds": parent.get("parse_seconds"),
            "dependency_hashes": {key: parent[key] for key in (
                "document_hash", "schema_hash", "ir_hash", "ontology_hash", "analysis_id",
            )},
        },
        "settings_provenance": {
            "core_model_identity_verified": list(CORE_MODEL_KEYS),
            "newly_frozen_settings": added,
            "changed_noncore_settings": changed,
            "removed_parent_settings": sorted(set(parent["settings"]) - set(current_settings)),
        },
    }
    write_json(output / "manifest.json", manifest)
    emit("prepared_fork", output=str(output), runtime_hash=runtime_hash,
         summary_reused=True, original_summary_seconds=summary["generation_seconds"],
         newly_frozen_settings=added, changed_noncore_settings=changed)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Existing frozen preparation")
    parser.add_argument("--output", required=True, help="New preparation; must not exist")
    args = parser.parse_args(argv)
    fork_experiment(args.source, args.output)


if __name__ == "__main__":
    main()
