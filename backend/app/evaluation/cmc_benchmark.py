"""Reproducible, read-only-source CMC graph experiments against the local model.

Run ``python -m app.evaluation.cmc_benchmark --help``. Experimental artifacts stay
inside explicitly selected directories. Shared model admission writes operational
scheduler records, but experiments never update production jobs or fact tables.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

CMC_CLASS = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
DEFAULT_REFERENCE = "upload-23c872fb-3ab1-41de-a705-dd4b162dfa09"
FROZEN_SETTINGS_KEYS = (
    "local_llm_enabled", "local_llm_model", "local_llm_model_revision",
    "local_llm_tokenizer_backend", "local_llm_server_model_path", "local_llm_temperature",
    "local_llm_max_concurrency", "local_llm_total_timeout_s", "evidence_total_timeout_s",
    "evidence_max_input_tokens", "evidence_max_output_tokens", "evidence_max_tasks",
    "evidence_max_regions_per_task", "evidence_max_objects_per_task", "evidence_timeout_s",
    "evidence_timeout_retries", "gliner_threshold", "word_tree_summary_prompt_version",
    "word_tree_summary_max_output_chars", "word_tree_summary_max_input_chars_per_node",
    "word_tree_summary_max_batch_chars", "word_tree_summary_max_nodes_per_batch",
)
RUN_IN_PROGRESS = "run.in_progress.json"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    """Atomically persist generated experimental artifacts, not source edits."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(root, suffix):
    entries = [
        (str(path.relative_to(root)), digest_file(path))
        for path in sorted(Path(root).rglob(f"*{suffix}"))
    ]
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def emit(event, **fields):
    print(
        json.dumps(
            {"event": event, "at": datetime.now(UTC).isoformat(), **fields},
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )


def settings_snapshot():
    from app.config import settings

    values = {key: getattr(settings, key) for key in FROZEN_SETTINGS_KEYS}
    values["gliner_model_path"] = str(Path(settings.gliner_model_path).resolve())
    values["local_llm_tokenizer_path"] = (
        str(Path(settings.local_llm_tokenizer_path).resolve())
        if settings.local_llm_tokenizer_path
        else ""
    )
    return values


def slot_snapshot():
    import httpx

    from app.config import settings

    try:
        endpoint = settings.local_llm_base_url.rstrip("/").removesuffix("/v1") + "/slots"
        response = httpx.get(endpoint, timeout=5, trust_env=False)
        response.raise_for_status()
        slots = response.json()
        return [
            {key: slot.get(key) for key in ("id", "is_processing", "n_ctx", "n_prompt_tokens")}
            for slot in slots
        ]
    except Exception as exc:
        return {"unavailable": type(exc).__name__}


def prepare(args):
    from sqlalchemy import text

    import app
    from app.config import settings
    from app.db import engine as db_engine
    from app.services.extraction.extraction_tasks import semantic_schema_from_engine
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.ontology_engine import OntologyEngine

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with db_engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        rows = list(
            conn.execute(
                text(
                    "SELECT id,source_filename,document_path,source_config,status "
                    "FROM extraction_jobs "
                    "WHERE source_config->>'doc_ref' = :iri ORDER BY created_at DESC"
                ),
                {"iri": "http://slpra.org/facts#" + args.document_ref},
            )
        )
        if not rows:
            raise ValueError("document reference has no extraction job")
        job = dict(rows[0]._mapping)
    config = job.get("source_config") or {}
    if config.get("doc_class_iri") != CMC_CLASS:
        raise ValueError("resolved document is not explicitly typed CMCReport")
    source = Path(job["document_path"]).resolve()
    if not source.is_file():
        raise FileNotFoundError("resolved source document is unavailable")
    shutil.copy2(source, output / "source.docx")
    shutil.copytree(settings.ontology_dir, output / "ontology")
    runtime_source = Path(app.__file__).parent
    original_runtime_hash = tree_digest(runtime_source, ".py")
    shutil.copytree(
        runtime_source,
        output / "runtime" / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if original_runtime_hash != tree_digest(output / "runtime" / "app", ".py"):
        raise RuntimeError("source code changed during snapshot; prepare a fresh snapshot")
    started = time.perf_counter()
    analysis = analyze_word_core(output / "source.docx", source_filename=job["source_filename"])
    parse_seconds = time.perf_counter() - started
    with tempfile.TemporaryDirectory(prefix="cmc-schema-", dir=output) as temporary:
        ontology = OntologyEngine(
            ontology_dir=output / "ontology", store_path=Path(temporary) / "isolated.sqlite3"
        )
        ontology.load()
        try:
            schema = semantic_schema_from_engine(ontology)
        finally:
            ontology._world.close()
    write_json(output / "ir.json", analysis.ir.model_dump(mode="json"))
    write_json(output / "schema.json", schema)
    packages = {}
    for name in ("gliner", "torch", "openai", "pydantic", "owlready2", "python-docx"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "document_ref": args.document_ref,
        "job_id": str(job["id"]),
        "source_filename": job["source_filename"],
        "source_path": str(source),
        "document_hash": digest_file(source),
        "class_iri": CMC_CLASS,
        "priority_paths": config.get("extraction_priority_paths", []),
        "production_status_at_prepare": job["status"],
        "evidence_units": len(analysis.ir.evidence_units),
        "nodes": len(analysis.ir.nodes),
        "source_characters": sum(len(unit.text) for unit in analysis.ir.evidence_units),
        "parse_seconds": parse_seconds,
        "runtime_hash": tree_digest(output / "runtime" / "app", ".py"),
        "ontology_hash": tree_digest(output / "ontology", ".ttl"),
        "schema_hash": digest_file(output / "schema.json"),
        "ir_hash": digest_file(output / "ir.json"),
        "analysis_id": analysis.ir.analysis_id,
        "settings": settings_snapshot(),
        "packages": packages,
        "hardware": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        "model_slots": slot_snapshot(),
        "reference_status": "no_human_gold_supplied",
    }
    write_json(output / "manifest.json", manifest)
    emit(
        "prepared",
        output=str(output),
        **{
            key: manifest[key]
            for key in ("document_hash", "evidence_units", "source_characters", "runtime_hash")
        },
    )


def restore_settings(manifest):
    from app.config import settings

    for key, value in manifest["settings"].items():
        setattr(settings, key, value)
    missing = sorted(set(FROZEN_SETTINGS_KEYS) - manifest["settings"].keys())
    if missing:
        emit(
            "legacy_manifest_settings_missing", keys=missing,
            interpretation="These settings use current process values, not frozen values.",
        )
    return missing


def validate_prepared(prepared, manifest):
    """Check shared dependencies before either summary or extraction invokes a model."""
    required = ("runtime_hash", "document_hash", "schema_hash", "ir_hash", "analysis_id")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(f"prepared manifest missing dependency identities: {', '.join(missing)}")
    runtime = Path(sys.modules["app"].__file__).parent
    if tree_digest(runtime, ".py") != manifest["runtime_hash"]:
        raise ValueError("runtime differs from frozen preparation; use its runtime PYTHONPATH")
    for filename, key, label in (
        ("source.docx", "document_hash", "source"),
        ("schema.json", "schema_hash", "ontology schema"),
        ("ir.json", "ir_hash", "evidence IR"),
    ):
        if digest_file(prepared / filename) != manifest[key]:
            raise ValueError(f"prepared {label} was modified")


def summary_metadata(structure):
    from dataclasses import asdict

    result = {}
    stack = [structure.section_tree]
    while stack:
        node = stack.pop()
        result[node.node_id] = asdict(node.layer_metadata)
        stack.extend(node.children)
    return result


def summary_artifacts(structure):
    """Retain both page and chapter work, while keeping planner metadata chapter-only."""
    from dataclasses import asdict

    chapters = summary_metadata(structure)
    pages = {}
    stack = [structure.section_tree]
    while stack:
        node = stack.pop()
        for page in node.pages:
            pages[page.node_id] = asdict(page.page_metadata)
        stack.extend(node.children)
    chapter_counts = Counter(item.get("summary_source") for item in chapters.values())
    page_counts = Counter(item.get("summary_source") for item in pages.values())
    return {
        "metadata": chapters, "page_metadata": pages,
        "chapter_source_counts": dict(chapter_counts), "page_source_counts": dict(page_counts),
        "source_counts": dict(chapter_counts + page_counts),
        "source_counts_scope": "all_chapters_and_pages",
    }


def summarize(args):
    from app.config import settings
    from app.services.extraction import word_tree_summarizer
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.llm.local_client import get_local_llm

    prepared = Path(args.prepared).resolve()
    manifest = read_json(prepared / "manifest.json")
    missing_settings = restore_settings(manifest)
    if (prepared / "summaries.json").exists():
        raise FileExistsError("summaries already frozen; use a new preparation for a fresh sample")
    validate_prepared(prepared, manifest)
    settings.llm_word_tree_summary_enabled = True
    settings.word_tree_summary_timeout_s = args.timeout
    analysis = analyze_word_core(
        prepared / "source.docx", source_filename=manifest["source_filename"]
    )
    if analysis.ir.analysis_id != manifest["analysis_id"]:
        raise ValueError("runtime parser no longer reproduces the prepared evidence IR")
    before = slot_snapshot()
    started = time.perf_counter()
    client = get_local_llm()
    batches = []
    original_batch = word_tree_summarizer._apply_batch

    def record_partial():
        value = {
            "document_hash": manifest["document_hash"],
            "analysis_id": manifest["analysis_id"],
            **summary_artifacts(analysis.structure),
            "generation_seconds": time.perf_counter() - started,
            "batches": batches,
            "unfrozen_setting_keys": missing_settings,
        }
        write_json(prepared / "summaries.partial.json", value)
        return value

    def measured_batch(batch_client, targets):
        tick = time.perf_counter()
        try:
            return original_batch(batch_client, targets)
        finally:
            event = {
                "batch": len(batches) + 1,
                "nodes": len(targets),
                "wall_seconds": time.perf_counter() - tick,
                "node_ids": [target["node_id"] for target in targets],
            }
            batches.append(event)
            record_partial()
            emit("summary_batch_finished", **event)

    # Process-local observation only; production server and model policy are untouched.
    word_tree_summarizer._apply_batch = measured_batch
    try:
        word_tree_summarizer.summarize_word_tree(
            analysis.structure,
            client,
            progress_fn=lambda stage: emit("summary_progress", stage=stage),
        )
    finally:
        word_tree_summarizer._apply_batch = original_batch
        record_partial()
        close = getattr(client, "close", None)
        if callable(close):
            close()
    result = {
        "document_hash": manifest["document_hash"],
        "analysis_id": manifest["analysis_id"],
        **summary_artifacts(analysis.structure),
        "generation_seconds": time.perf_counter() - started,
        "model_identity": manifest["settings"]["local_llm_model_revision"],
        "summary_prompt_version": settings.word_tree_summary_prompt_version,
        "batches": batches,
        "unfrozen_setting_keys": missing_settings,
        "effective_settings": settings_snapshot(),
        "summary_timeout_s": args.timeout,
        "slots_before": before,
        "slots_after": slot_snapshot(),
    }
    write_json(prepared / "summaries.json", result)
    emit(
        "summaries_complete",
        generation_seconds=result["generation_seconds"],
        source_counts=result["source_counts"],
    )


def run(args):
    """Refuse interrupted segments instead of silently omitting their unrecorded time."""
    if getattr(args, "focus_path", None) and args.mode != "quality_guided_summary":
        raise ValueError("focus_path_requires_quality_guided_summary")
    prepared = Path(args.prepared).resolve()
    manifest = read_json(prepared / "manifest.json")
    missing_settings = restore_settings(manifest)
    validate_prepared(prepared, manifest)
    output = Path(args.output).resolve()
    marker = output / RUN_IN_PROGRESS
    if marker.exists():
        raise ValueError(
            "run has an unfinished segment or an active process; do not resume it. "
            "Preserve its artifacts and use a new output directory so elapsed time is not lost."
        )
    if args.resume and not all((output / name).is_file()
                               for name in ("checkpoint.json", "result.json")):
        raise ValueError(
            "resume requires a successfully finalized checkpoint and result; "
            "use a new output directory after an interrupted initial segment"
        )
    output.mkdir(parents=True, exist_ok=args.resume)
    write_json(marker, {
        "started_at": datetime.now(UTC).isoformat(), "pid": os.getpid(),
        "resume": args.resume, "document_hash": manifest["document_hash"],
        "interpretation": "Removed only after the complete segment result is persisted.",
    })
    _run_segment(args, prepared, manifest, output, missing_settings)
    marker.unlink()


def _run_segment(args, prepared, manifest, output, missing_settings):
    from app.services.extraction.extraction_tasks import GenericExtractionRunner
    from app.services.extraction.local_semantic_model import configured_generic_runner
    from app.services.extraction.word_analysis import analyze_word_core

    started = time.perf_counter()
    analysis = analyze_word_core(
        prepared / "source.docx", source_filename=manifest["source_filename"]
    )
    parse_seconds = time.perf_counter() - started
    if analysis.ir.analysis_id != manifest["analysis_id"]:
        raise ValueError("runtime parser no longer reproduces the prepared evidence IR")
    # The engine facade supplies the already frozen semantic schema. No OWL store is opened.
    schema = read_json(prepared / "schema.json")

    class FrozenEngine:
        def semantic_schema_snapshot(self):
            return schema

    base = configured_generic_runner(FrozenEngine())
    if base.model_call is None or base.tokenizer is None:
        raise RuntimeError("configured local model/tokenizer is unavailable")
    base.priority_paths = [tuple(path) for path in manifest["priority_paths"]]
    base.budget = base.budget.model_copy(
        update={
            "timeout_s": args.timeout,
            "timeout_retries": args.timeout_retries,
        }
    )
    metadata = None
    summary_seconds = 0.0
    if args.mode in {"structure_summary", "structure_summary_gliner", "root_guided_summary",
                     "quality_guided_summary"}:
        summary = read_json(prepared / "summaries.json")
        if summary["document_hash"] != manifest["document_hash"]:
            raise ValueError("summary belongs to a different document")
        if summary.get("analysis_id", manifest["analysis_id"]) != manifest["analysis_id"]:
            raise ValueError("summary belongs to a different evidence analysis")
        metadata = summary["metadata"]
        summary_seconds = summary["generation_seconds"]
    calls = []
    original_call = base.model_call

    def measured_model(*values):
        tick = time.perf_counter()
        event = {"call": len(calls) + 1, "started_seconds": tick - started}
        if len(calls) % 10 == 0:
            event["slots_before"] = slot_snapshot()
        try:
            return original_call(*values)
        except Exception as exc:
            event["error"] = type(exc).__name__
            raise
        finally:
            event["wall_seconds"] = time.perf_counter() - tick
            calls.append(event)
            with (output / "calls.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            emit("model_call", mode=args.mode, **event)

    measured_model.measures_model = True
    base.model_call = measured_model
    planner_started = time.perf_counter()
    if args.mode == "baseline":
        runner: GenericExtractionRunner = base
    elif args.mode == "quality_guided_summary":
        from app.evaluation.quality_guided_variant import build_quality_guided_variant

        runner = build_quality_guided_variant(
            base, analysis.ir, analysis.structure, metadata=metadata or {},
            focus_path=getattr(args, "focus_path", None) or (),
        )
        write_json(output / "plan.json", runner.plan)
    elif args.mode in {"root_guided", "root_guided_summary"}:
        from app.evaluation.root_guided_variant import build_root_guided_variant

        runner = build_root_guided_variant(
            base, analysis.ir, analysis.structure,
            metadata=metadata if metadata is not None else {},
            max_sections_per_predicate=getattr(args, "max_sections_per_predicate", 3),
        )
        write_json(output / "plan.json", runner.plan)
    else:
        from app.evaluation.hierarchy_variant import build_variant

        gliner = None
        if args.mode == "structure_summary_gliner":
            from app.services.extraction.gliner_extractor import get_gliner_extractor

            gliner = get_gliner_extractor()
            if gliner is None:
                raise RuntimeError("GLiNER requested but unavailable; cannot label this an NER run")
            # The variant constructor owns the first availability/load call and
            # its timer. Preloading here would turn its load metric into a cache hit.
        runner = build_variant(
            base, analysis.ir, analysis.structure, mode=args.mode, metadata=metadata, gliner=gliner
        )
        write_json(output / "plan.json", runner.plan)
    planner_seconds = time.perf_counter() - planner_started
    checkpoint = read_json(output / "checkpoint.json") if args.resume else None
    previous = read_json(output / "result.json") if args.resume else None
    if previous and (
        previous["mode"] != args.mode or previous["document_hash"] != manifest["document_hash"]
    ):
        raise ValueError("resume mode or source does not match")
    if checkpoint and checkpoint.get("input_id") != runner.input_id(analysis.ir, CMC_CLASS):
        raise ValueError("checkpoint dependencies differ; refusing to silently restart a resume")
    checkpoint_count = 0
    first = {"validated_result": None, "validated_relationship": None}

    def save_checkpoint(value):
        nonlocal checkpoint_count
        checkpoint_count += 1
        if checkpoint_count % 5 == 0:
            write_json(output / "checkpoint.json", value)

    def snapshot(value):
        elapsed = time.perf_counter() - started
        valid = [
            candidate
            for candidate in value.candidates
            if candidate.validation_status == "passed"
            and not candidate.identity.get("document_root")
        ]
        if valid and first["validated_result"] is None:
            first["validated_result"] = elapsed
        if any(candidate.kind == "relationship" for candidate in valid):
            if first["validated_relationship"] is None:
                first["validated_relationship"] = elapsed
        write_json(
            output / "latest_candidates.json",
            value.model_dump(mode="json", exclude={"tasks", "checkpoint"}),
        )
        # Observation only: reference scoring happens after all model runs finish.
        with (output / "snapshots.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "elapsed_seconds": elapsed,
                "completion": value.completion,
                "candidates": [candidate.model_dump(mode="json") for candidate in value.candidates],
            }, ensure_ascii=False) + "\n")
        emit("snapshot", mode=args.mode, elapsed=elapsed, candidates=len(valid), first=first)

    def trace(value):
        with (output / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                **value, "model_call_number": len(calls),
                "observed_seconds": time.perf_counter() - started,
            }, ensure_ascii=False, default=str) + "\n")

    runner.trace_fn = trace
    before = slot_snapshot()
    deadline = started + args.deadline_seconds if args.deadline_seconds else None
    emit("run_started", mode=args.mode, pause_after=args.pause_after, output=str(output))
    try:
        result = runner.run(
            analysis.ir,
            effective_class=CMC_CLASS,
            checkpoint=checkpoint,
            pause_after=args.pause_after,
            should_pause=lambda: (
                deadline is not None and time.perf_counter() >= deadline
                or bool(getattr(args, "stop_file", None)) and Path(args.stop_file).exists()
            ),
            checkpoint_fn=save_checkpoint,
            snapshot_fn=snapshot,
        )
        # Include final reconciliation (e.g. shared-value conflicts), not only
        # incremental graph publications, in the offline observation timeline.
        snapshot(result)
        elapsed = time.perf_counter() - started
        payload = result.model_dump(mode="json")
        write_json(output / "checkpoint.json", payload.pop("checkpoint", {}))
        write_json(output / "run.json", payload)
        measured = {
            "schema_version": 1,
            "mode": args.mode,
            "document_hash": manifest["document_hash"],
            "runtime_hash": manifest["runtime_hash"],
            "ontology_hash": manifest["ontology_hash"],
            "model_settings": manifest["settings"],
            "effective_settings": settings_snapshot(),
            "unfrozen_setting_keys": missing_settings,
            "budget": runner.budget.model_dump(mode="json"),
            "execution_limits": {
                "pause_after": args.pause_after,
                "deadline_seconds": args.deadline_seconds,
                "stop_file": getattr(args, "stop_file", None),
                "focus_path": list(getattr(args, "focus_path", None) or ()),
                "deadline_policy": "soft_record_boundary" if args.mode == "quality_guided_summary"
                else "soft_task_boundary",
                "timeout_s": args.timeout,
                "timeout_retries": args.timeout_retries,
            },
            "input_hashes": {
                "ir": manifest.get("ir_hash"), "schema": manifest.get("schema_hash"),
                "summaries": digest_file(prepared / "summaries.json")
                if metadata is not None else None,
            },
            "completion": result.completion,
            "diagnostics": result.diagnostics,
            "elapsed_seconds_this_segment": elapsed,
            "elapsed_seconds_total": elapsed + (previous or {}).get("elapsed_seconds_total", 0),
            "parse_seconds_this_segment": parse_seconds,
            "planner_seconds_this_segment": planner_seconds,
            "summary_generation_seconds_separate": summary_seconds,
            "cold_metadata_accounted_seconds": (
                elapsed + (previous or {}).get("elapsed_seconds_total", 0) + summary_seconds
            ),
            "summary_cache": "precomputed" if metadata is not None else "not_used",
            "first_seconds_this_segment": first,
            "model_calls_this_segment": len(calls),
            "model_wall_seconds_this_segment": sum(event["wall_seconds"] for event in calls),
            "candidate_counts": dict(Counter(candidate.kind for candidate in result.candidates)),
            "validated_counts": dict(
                Counter(
                    candidate.kind
                    for candidate in result.candidates
                    if candidate.validation_status == "passed"
                )
            ),
            "task_count": len(result.tasks),
            "performance": result.performance,
            "variant_statistics": getattr(runner, "variant_statistics", {}),
            "slots_before": before,
            "slots_after": slot_snapshot(),
            "speed_claim": "not_evaluated_quality_objective"
            if args.mode == "quality_guided_summary" else "complete_run"
            if result.completion == "complete"
            else "bounded_partial_only",
        }
        write_json(output / "result.json", measured)
        if hasattr(runner, "plan"):
            write_json(output / "plan.json", runner.plan)
        emit(
            "run_finished",
            **{
                key: measured[key]
                for key in (
                    "mode",
                    "completion",
                    "elapsed_seconds_this_segment",
                    "candidate_counts",
                    "validated_counts",
                )
            },
        )
    finally:
        close = getattr(base.tokenizer, "close", None)
        if close:
            close()


def score(args):
    from app.evaluation.graph_metrics import evaluate_graph

    prepared = Path(args.prepared).resolve()
    reference = read_json(args.reference) if args.reference else None
    metrics = evaluate_graph(
        read_json(Path(args.run) / "run.json"), reference, ir=read_json(prepared / "ir.json")
    )
    write_json(Path(args.run) / "metrics.json", metrics)
    emit("scored", output=str(Path(args.run) / "metrics.json"))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    pre = commands.add_parser(
        "prepare", help="Resolve target read-only and freeze source/code/schema"
    )
    pre.add_argument("--document-ref", default=DEFAULT_REFERENCE)
    pre.add_argument("--output", required=True)
    pre.set_defaults(function=prepare)
    summary = commands.add_parser(
        "summarize", help="Generate and time actual local LLM summaries once"
    )
    summary.add_argument("--prepared", required=True)
    summary.add_argument("--timeout", type=float, default=120)
    summary.set_defaults(function=summarize)
    execution = commands.add_parser("run", help="Run an isolated actual model experiment")
    execution.add_argument("--prepared", required=True)
    execution.add_argument("--output", required=True)
    execution.add_argument(
        "--mode",
        choices=("baseline", "structure", "structure_summary", "structure_summary_gliner",
                 "root_guided", "root_guided_summary", "quality_guided_summary"),
        required=True,
    )
    execution.add_argument(
        "--pause-after",
        type=int,
        default=None,
        help="Tasks per segment; omit for complete processing, resume preserves budget",
    )
    execution.add_argument("--deadline-seconds", type=float, default=None)
    execution.add_argument("--stop-file", default=None,
                           help="Pause at a task/record boundary when this explicit file exists")
    execution.add_argument(
        "--focus-path", nargs="+", default=None,
        help="Ordered formal relation IRIs for a staged quality-path experiment; "
             "all source records remain fallback candidates for these relations",
    )
    execution.add_argument("--max-sections-per-predicate", type=int, default=3)
    execution.add_argument("--timeout", type=float, default=120)
    execution.add_argument("--timeout-retries", type=int, default=0)
    execution.add_argument("--resume", action="store_true")
    execution.set_defaults(function=run)
    scoring = commands.add_parser(
        "score", help="Score only the explicitly annotated reference scope"
    )
    scoring.add_argument("--prepared", required=True)
    scoring.add_argument("--run", required=True)
    scoring.add_argument("--reference")
    scoring.set_defaults(function=score)
    return root


def main():
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
