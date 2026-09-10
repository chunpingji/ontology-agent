#!/usr/bin/env python3
"""Run one bounded real-template diagnostic in an empty isolated PostgreSQL database.

Input JSON contains template/source metadata, the frozen ontology snapshot, and
an allowlisted model configuration. It must not contain held-out expected values.
Preparation creates a new run without model calls; --execute continues that run.
This measures absolute timings and costs, not full-template quality or deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
SETTING_KEYS = {
    "local_llm_enabled", "local_llm_model", "local_llm_model_revision",
    "local_llm_tokenizer_backend", "local_llm_server_model_path", "local_llm_temperature",
    "local_llm_total_timeout_s", "evidence_max_input_tokens", "evidence_max_output_tokens",
    "evidence_max_regions_per_task", "evidence_max_objects_per_task", "evidence_timeout_s",
    "evidence_total_timeout_s", "semantic_ranking_embedding_path",
    "semantic_ranking_embedding_manifest_path", "semantic_ranking_reranker_path",
    "semantic_ranking_reranker_manifest_path", "semantic_ranking_device",
    "semantic_ranking_dtype", "semantic_ranking_cuda_version", "semantic_ranking_pool_size",
    "semantic_ranking_max_tokens_per_pair", "semantic_ranking_max_tokens_per_slot",
    "semantic_ranking_max_tokens_per_run", "semantic_ranking_timeout_seconds",
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def summarize(values):
    ordered = sorted(values)
    return {
        "count": len(ordered), "total_seconds": sum(ordered),
        "median_seconds": statistics.median(ordered) if ordered else None,
        "p95_seconds": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        if ordered else None,
    }


def request_accounting(requests):
    """Keep unmeasured inference distinct from tokenizer work and known totals."""
    result = {}
    for category, stages, token_key in (
        ("tokenizer", {"ranking_count_tokens_batch"}, None),
        ("ranking_inference", {"ranking_embed", "ranking_score_pairs"}, "input_tokens"),
        ("main_inference", {"ontology_guided_discovery", "ontology_guided_verification"},
         "prompt_tokens"),
    ):
        selected = [row for row in requests if row["stage"] in stages]
        unknown = [row["request_id"] for row in selected
                   if token_key and row["metrics"].get(token_key) is None]
        result[category] = {
            "requests": len(selected),
            "statuses": dict(Counter(row["status"] for row in selected)),
            "token_metric": token_key,
            "known_input_tokens_subtotal": sum(
                row["metrics"][token_key] for row in selected
                if token_key and row["metrics"].get(token_key) is not None
            ) if token_key else None,
            "unmeasured_input_request_ids": unknown,
            "input_measurement_complete": not unknown if token_key else None,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--tasks", type=int, default=8)
    parser.add_argument("--pause-after-tasks", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    from sqlalchemy.engine import make_url

    url = make_url(args.database_url)
    if (url.get_backend_name() != "postgresql"
            or url.host not in {"127.0.0.1", "localhost"}
            or not (url.database or "").endswith("_template_performance_test")):
        parser.error("requires dedicated loopback PostgreSQL *_template_performance_test")
    if not 1 <= args.tasks <= 64 or not 0 <= args.pause_after_tasks < args.tasks:
        parser.error("tasks must be 1..64; pause-after-tasks must be 0..tasks-1")
    inputs = json.loads(args.input.read_text())
    if set(inputs["settings"]) - SETTING_KEYS:
        parser.error("input settings contain unsupported keys")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixed = {
        **inputs["settings"], "local_llm_base_url": args.model_base_url,
        "local_llm_enabled": True, "evidence_max_tasks": args.tasks,
        "document_analysis_max_model_calls_per_record": 6,
        "document_analysis_performance_enabled": True,
        "document_analysis_template_interleaving": False,
        "semantic_ranking_enabled": True, "semantic_ranking_mode": "semantic",
        "semantic_ranking_failure_policy": "pause", "semantic_ranking_budget_enabled": True,
        "semantic_ranking_retry_limit": 1, "semantic_ranking_batch_size": 4,
    }
    os.environ.update({key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                       for key, value in fixed.items()})
    os.environ.update({
        "DATABASE_URL": args.database_url,
        "OWL_STORE_PATH": str(output / "ontology.sqlite3"),
        "DOCUMENT_ANALYSIS_STORAGE_DIR": str(output / "run-artifacts"),
        "EVIDENCE_WORLD_DIR": str(output / "evidence-worlds"),
        "LLM_CLOUD_ENABLED": "false", "SEMANTIC_ALIGNMENT_ENABLED": "false",
        "GLINER_EXTRACTION_ENABLED": "false", "LLM_WORD_TREE_SUMMARY_ENABLED": "false",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    })
    # Avoid ambient repository .env configuration. Credentials only come from
    # the explicit isolated database argument and existing model client policy.
    os.chdir(output)
    from sqlalchemy import event, inspect, select

    import app.models  # noqa: F401
    from app.db import Base, SessionLocal, engine
    from app.models.document_analysis import DocumentAnalysisRun
    from app.models.extraction import AstTemplate, ExtractionJob
    from app.models.model_request import LocalModelRequest
    from app.services.document_analysis import application, execution
    from app.services.document_analysis.application import DocumentAnalysisApplication
    from app.services.document_analysis.template_runs import TemplateDocumentRuns
    from app.services.extraction.document_ir import DocumentIR
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
    from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
    from app.services.extraction.ontology_guided.records import RecordIndex

    ontology = OntologySnapshot.model_validate(inputs["ontology"])
    template_hash = evidence_hash(inputs["template"]["schema_json"])
    document_hash = hashlib.sha256(args.document.read_bytes()).hexdigest()
    frozen = {
        "scope": "bounded real-template new-kernel absolute performance diagnostic",
        "template_id": inputs["template"]["id"],
        "template_version": inputs["template"]["version"],
        "template_revision": inputs["template"]["revision_no"],
        "template_hash": template_hash, "document_hash": document_hash,
        "ontology_snapshot_id": ontology.snapshot_id, "ontology_hash": ontology.ontology_hash,
        "input_hash": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "expected_source_counts": inputs.get("source_counts"),
        "configuration": fixed, "main_model_request_upper_bound": args.tasks * 6,
        "pause_after_tasks": args.pause_after_tasks,
        "ontology_input": "exact frozen snapshot injected at creation; no live ontology widening",
        "scheduler_database": url.database,
    }
    manifest_path = output / "manifest.json"
    # Only the ontology acquisition boundary is supplied with a frozen fixture.
    # Template origin, parsing, discovery, verification, scheduler and persistence
    # all use their real application implementations and local model adapters.
    application.ontology_snapshot_from_engine = lambda _engine, _root: ontology
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["frozen"] != frozen:
            raise RuntimeError("prepared run configuration changed")
        run_id = UUID(manifest["run_id"])
    else:
        if inspect(engine).get_table_names():
            raise RuntimeError("requires an empty database; existing tables are protected")
        Base.metadata.create_all(engine)
        source_copy = output / "source.docx"
        shutil.copy2(args.document, source_copy)
        template_values = dict(inputs["template"])
        template_values["id"] = UUID(template_values["id"])
        source_values = dict(inputs["source"])
        source_values.update(id=UUID(source_values["id"]), document_path=str(source_copy),
                             source_config=inputs["source_config"])
        with SessionLocal() as db:
            db.add(ExtractionJob(**source_values))
            db.add(AstTemplate(**template_values))
            db.commit()
            service = DocumentAnalysisApplication(db, ontology_engine=object())
            run, created = asyncio.run(TemplateDocumentRuns(service).create(
                "isolated-template-performance", template_values["id"], source_values["id"],
                "bounded-new-kernel-diagnostic",
            ))
            assert created
            run_id = run.recognition_run_id
            origin = service._artifact_payload(run, "source")[0]["origin"]
            expected_paths = [list(path) for path in dict.fromkeys(
                tuple(path) for path in inputs["source_config"]["extraction_priority_paths"])]
            if (origin["template_hash"] != template_hash
                    or origin["priority_paths"] != expected_paths):
                raise RuntimeError("template origin/priority paths changed")
            manifest = {"frozen": frozen, "run_id": str(run_id), "origin": origin}
            write_json(manifest_path, manifest)
    expected_paths = [list(path) for path in dict.fromkeys(
        tuple(path) for path in inputs["source_config"]["extraction_priority_paths"])]
    if manifest["origin"]["priority_paths"] != expected_paths:
        raise RuntimeError("prepared priority path content or ordering changed")
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (output / "manifest.sha256").write_text(manifest_hash + "\n")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    if not args.execute:
        return
    if (output / "execution-started.json").exists():
        raise RuntimeError("execution already started; preserve its evidence and use a new run")
    write_json(output / "execution-started.json", {"started_unix": time.time()})
    started = time.monotonic()
    timings = defaultdict(list)
    sql_timings = defaultdict(list)
    batches, controls = [], []
    first_relation_seconds = None
    paused_once = False
    source_counts = {}

    @event.listens_for(engine, "before_cursor_execute")
    def before_sql(_conn, _cursor, statement, _parameters, context, _executemany):
        context.diagnostic_start = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def after_sql(_conn, _cursor, statement, _parameters, context, _executemany):
        sql_timings[statement.split(None, 1)[0].upper()].append(
            time.monotonic() - context.diagnostic_start)

    def control(action):
        with SessionLocal() as db:
            run = db.get(DocumentAnalysisRun, run_id)
            service = DocumentAnalysisApplication(db, ontology_engine=object())
            stamp = time.monotonic()
            changed, replay = service.control(
                run, action=action, expected_revision=run.revision,
                request_key=f"diagnostic-{action}", reason="isolated bounded diagnostic",
                role="senior_analyst",
            )
            controls.append({"action": action, "seconds": time.monotonic() - stamp,
                             "elapsed_seconds": time.monotonic() - started,
                             "status": changed.execution_status, "replay": replay})
            write_json(output / "controls.json", controls)

    def instrument(name):
        original = getattr(execution, name)

        def call(*positional, **keyword):
            nonlocal first_relation_seconds, paused_once
            stamp = time.monotonic()
            try:
                result = original(*positional, **keyword)
            finally:
                timings[name].append(time.monotonic() - stamp)
            if name == "_publish_artifact" and keyword.get("kind") == "metadata":
                ir = DocumentIR.model_validate(keyword["payload"]["analysis"])
                menu = compile_local_menu(ontology, SubjectRef(
                    entity_id="diagnostic-root", revision=1,
                    class_iri=inputs["source_config"]["doc_class_iri"], is_document_root=True,
                ))
                count = len(RecordIndex(ir).records)
                source_counts.update({"records": count, "evidence_units": len(ir.evidence_units),
                                      "root_opportunities": count * (
                                          len(menu.properties) + len(menu.relationships))})
                write_json(output / "source-counts.json", source_counts)
                if inputs.get("source_counts") and source_counts != inputs["source_counts"]:
                    raise RuntimeError("parsed full-document opportunity counts changed")
            if name == "_persist_recognition_batch":
                batch = keyword["batch"]
                with SessionLocal() as db:
                    run = db.get(DocumentAnalysisRun, run_id)
                    service = DocumentAnalysisApplication(db, ontology_engine=object())
                    graph = service.graph_response(run, projection="effective_affirmed")
                elapsed = time.monotonic() - started
                if graph["relationships"] and first_relation_seconds is None:
                    first_relation_seconds = elapsed
                batches.append({
                    "batch_id": batch.batch_id, "task": batch.task.model_dump(mode="json"),
                    "outcome": batch.outcome.model_dump(mode="json"),
                    "elapsed_seconds": elapsed, "progress": dict(run.progress),
                    "effective_relationships": len(graph["relationships"]),
                    "effective_properties": len(graph["properties"]),
                })
                write_json(output / "batches.json", batches)
                print(json.dumps({"batch": len(batches), "elapsed_seconds": elapsed,
                                  "outcome": batch.outcome.reason_code,
                                  "model_calls": batch.outcome.model_calls}), flush=True)
                if (args.pause_after_tasks and len(batches) >= args.pause_after_tasks
                        and not paused_once):
                    paused_once = True
                    control("pause")
            return result

        setattr(execution, name, call)

    for name in ("_persist_recognition_batch", "_persist_ranking_state",
                 "_persist_model_call_state", "_publish_artifact"):
        instrument(name)
    failure = None
    try:
        execution.dispatch_run(run_id, bind=engine)
        with SessionLocal() as db:
            run = db.get(DocumentAnalysisRun, run_id)
            pause_snapshot = {"status": run.execution_status, "error": run.error,
                              "progress": dict(run.progress), "fingerprint": run.run_fingerprint}
        if paused_once:
            write_json(output / "pause-state.json", pause_snapshot)
            if pause_snapshot["status"] == "paused" and pause_snapshot["error"] is None:
                control("resume")
                execution.dispatch_run(run_id, bind=engine)
    except Exception as exc:
        failure = type(exc).__name__
        raise
    finally:
        elapsed = time.monotonic() - started
        with SessionLocal() as db:
            run = db.get(DocumentAnalysisRun, run_id)
            service = DocumentAnalysisApplication(db, ontology_engine=object())
            graph = service.graph_response(run, projection="effective_affirmed")
            write_json(output / "graph.json", graph)
            requests = [{column.name: getattr(row, column.name) for column in row.__table__.columns}
                        for row in db.scalars(select(LocalModelRequest).where(
                            LocalModelRequest.run_id == str(run_id)).order_by(
                                LocalModelRequest.sequence))]
            write_json(output / "scheduler.json", requests)
            report = {
                "run_id": str(run_id), "manifest_hash": manifest_hash,
                "status": run.execution_status, "error": run.error,
                "stop_reason": run.stop_reason, "progress": dict(run.progress),
                "run_fingerprint": run.run_fingerprint, "total_seconds": elapsed,
                "first_effective_relation_seconds": first_relation_seconds,
                "template_path_completion_seconds": None,
                "template_path_status": "awaiting_held_out_validation",
                "source_counts": source_counts,
                "main_model_request_upper_bound": args.tasks * 6,
                "requests_by_stage_status": dict(Counter(
                    f"{row['stage']}:{row['status']}" for row in requests)),
                "known_model_metrics_subtotals": dict(Counter({key: sum(
                    row["metrics"].get(key, 0) or 0 for row in requests)
                    for key in sorted({key for row in requests
                                       for key, value in row["metrics"].items()
                                       if isinstance(value, (int, float))})})),
                "request_accounting": request_accounting(requests),
                "persistence": {key: summarize(value) for key, value in timings.items()},
                "sql": {key: summarize(value) for key, value in sql_timings.items()},
                "controls": controls, "failure_type": failure,
                "full_template_acceptance": "incomplete",
                "limitations": ["bounded tasks; full opportunity denominator is retained",
                                "shared model server and host; no relative speedup claim",
                                "pause/resume includes model worker startup and polling costs",
                                "ontology acquisition uses exact exported frozen snapshot"],
            }
            write_json(output / "report.json", report)
            print(json.dumps(report, ensure_ascii=False, default=str), flush=True)
        validation = subprocess.run([
            sys.executable, str(BACKEND / "scripts/validate_template_kernel_migration.py"),
            "--run-id", str(run_id), "--output", str(output / "template-acceptance.json"),
        ], cwd=output, env={**os.environ, "PYTHONPATH": str(BACKEND)},
            capture_output=True, text=True)
        (output / "acceptance-process.log").write_text(validation.stdout + validation.stderr)
        write_json(output / "acceptance-execution.json", {"exit_code": validation.returncode})
        acceptance_path = output / "template-acceptance.json"
        if acceptance_path.exists():
            acceptance = json.loads(acceptance_path.read_text())
            report["template_path_status"] = (
                "held_out_checks_passed_at_end_time_unmeasured" if acceptance["passed"]
                else "not_completed_in_bounded_diagnostic")
            report["held_out_checks"] = acceptance["checks"]
            write_json(output / "report.json", report)


if __name__ == "__main__":
    main()
