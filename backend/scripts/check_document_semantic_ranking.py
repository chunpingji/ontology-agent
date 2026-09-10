#!/usr/bin/env python3
"""Check CPU/CUDA 12 ranking on a frozen DOCX and its ontology root menu.

This exercises ranking, full-record preservation and committed-epoch replay.
It does not run the recognition LLM, establish facts, or measure graph precision.
All outputs and scheduler requests belong to a new, isolated directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.smoke_semantic_ranking import (  # noqa: E402
    _absolute_path,
    _bounded_int,
    _device_arguments,
    _hardware,
    _memory_summary,
    _require,
    _scheduler_report,
    _write_json,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    _device_arguments(result)
    result.add_argument("--prepared", type=_absolute_path, required=True)
    result.add_argument("--output-dir", type=_absolute_path, required=True)
    result.add_argument("--predicate-iri", help="Must belong to the frozen root's direct menu.")
    for kind in ("embedding", "reranker"):
        result.add_argument(f"--{kind}-path", type=_absolute_path, required=True)
        result.add_argument(f"--{kind}-manifest-path", type=_absolute_path, required=True)
    result.add_argument("--pool-size", type=_bounded_int(1, 1024), default=64)
    result.add_argument("--batch-size", type=_bounded_int(1, 256), default=4)
    result.add_argument("--max-tokens-per-pair", type=_bounded_int(64, 32768), default=2048)
    result.add_argument("--timeout-seconds", type=_bounded_int(1, 3600), default=600)
    result.add_argument("--max-tokens-per-slot", type=_bounded_int(1, 100_000_000),
                        default=4_194_304)
    result.add_argument("--max-tokens-per-run", type=_bounded_int(1, 100_000_000),
                        default=8_388_608)
    result.add_argument("--epochs", type=_bounded_int(1, 64), default=2,
                        help="Cold pool followed by warm pools, while eligible records remain.")
    return result


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(args, output: Path, report: dict) -> dict:
    from app.services.extraction.document_ir import DocumentIR
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided.contracts import (
        OntologySnapshot,
        SubjectRef,
        VersionedRef,
    )
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from app.services.extraction.ontology_guided.metadata import prepare_metadata
    from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
    from app.services.extraction.ontology_guided.records import RecordIndex
    from app.services.extraction.ontology_guided.retrieval import (
        plan_slot,
        validate_record_universe,
    )

    prepared = args.prepared
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    for filename, key in (("source.docx", "document_hash"), ("ir.json", "ir_hash"),
                          ("ontology_snapshot.json", "ontology_snapshot_file_hash")):
        _require(_digest(prepared / filename) == manifest[key], f"prepared_{key}_changed")
    ir = DocumentIR.model_validate_json((prepared / "ir.json").read_text(encoding="utf-8"))
    ontology = OntologySnapshot.model_validate_json(
        (prepared / "ontology_snapshot.json").read_text(encoding="utf-8"))
    _require(ir.analysis_id == manifest["analysis_id"], "prepared_analysis_identity_changed")
    _require(ir.document_hash == manifest["document_hash"], "prepared_document_identity_changed")
    _require(ontology.snapshot_id == manifest["ontology_snapshot_id"]
             and ontology.ontology_hash == manifest["ontology_semantic_hash"],
             "prepared_ontology_identity_changed")
    root_class = manifest["class_iri"]
    _require(root_class in ontology.classes, "prepared_root_not_in_ontology")
    # Reconstruct only the immutable heading hierarchy; no reparse or model summary.
    tree_nodes = {item["node_id"]: {**item, "children": []} for item in ir.nodes}
    roots = []
    for item in ir.nodes:
        parent = item.get("parent_id")
        if parent is None:
            roots.append(tree_nodes[item["node_id"]])
        else:
            tree_nodes[parent]["children"].append(tree_nodes[item["node_id"]])
    _require(len(roots) == 1, "prepared_structure_requires_one_root")
    metadata = prepare_metadata(ir, section_tree=roots[0], summary_version="frozen-structure-v1",
                                generation_source="structure_only")
    node = OntologyGuidedExecutor.root_node(
        recognition_run_id=report["run_id"], document_hash=ir.document_hash,
        root_class_iri=root_class, root_class_label=ontology.classes[root_class].label,
        filename=manifest["source_filename"],
    )
    subject = SubjectRef(entity_id=node.entity_id, revision=node.revision,
                         class_iri=root_class, is_document_root=True)
    menu = compile_local_menu(ontology, subject)
    predicates = [*menu.relationships, *menu.properties]
    if args.predicate_iri:
        predicates = [item for item in predicates if item.iri == args.predicate_iri]
    _require(bool(predicates), "requested_predicate_missing_from_root_direct_menu")
    priorities = {"hasProduct": 0, "describes": 1}
    predicate = min(predicates, key=lambda item: (
        priorities.get(item.iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1], 2), item.iri))
    index = RecordIndex(ir)
    plan = plan_slot(subject, predicate, index, metadata, ontology_hash=ontology.ontology_hash)
    validate_record_universe(plan, index)
    _require(bool(plan.records), "prepared_document_has_no_records")
    report["input"] = {
        "prepared": str(prepared), "manifest_sha256": _digest(prepared / "manifest.json"),
        "document_hash": ir.document_hash, "analysis_id": ir.analysis_id,
        "ontology_snapshot_id": ontology.snapshot_id, "ontology_hash": ontology.ontology_hash,
        "root_class_iri": root_class, "predicate_iri": predicate.iri,
        "predicate_label": predicate.label, "predicate_kind": predicate.kind,
        "records": len(index.records), "evidence_units": len(ir.evidence_units),
        "prepared_runtime_hash": manifest.get("runtime_hash"),
        "execution_runtime": "current_workspace_with_frozen_input_artifacts",
    }
    runtime_root = BACKEND_ROOT / "app"
    runtime_entries = [(str(path.relative_to(runtime_root)), _digest(path))
                       for path in sorted(runtime_root.rglob("*.py"))]
    report["input"]["execution_runtime_hash"] = hashlib.sha256(
        json.dumps(runtime_entries).encode()).hexdigest()
    _write_json(output / "root_menu.json", menu.model_dump(mode="json"))
    _write_json(output / "metadata.json", metadata.model_dump(mode="json"))
    _write_json(output / "retrieval_plan_initial.json", plan.model_dump(mode="json"))
    return {
        "plan": plan, "index": index, "metadata": metadata, "subject_node": node,
        "predicate": predicate, "run_fingerprint": evidence_hash([
            report["run_id"], report["input"], report["configuration"], report["policy"],
            report["model_identity"],
        ]),
        "root_ref": VersionedRef(id=node.entity_id, revision=node.revision),
        "root_class_iri": root_class, "permission_scope": report["run_id"],
    }


def _inventory(service, arguments, maximum: int) -> tuple[list[dict], set[str]]:
    """Use already measured counts; inventory must never invoke a model."""
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided.retrieval_views import build_retrieval_views

    state = service.snapshot()
    scope = evidence_hash(arguments["permission_scope"])

    def count(text):
        key = evidence_hash([scope, service.model_identity, text, "tokens"])
        _require(key in state["token_cache"], "record_token_measurement_missing")
        return state["token_cache"][key]

    views = build_retrieval_views(arguments["index"], arguments["metadata"],
                                  count_tokens=count, max_record_tokens=maximum)
    query_maximum = max(count(query["model_text"]) for query in service.epochs[0].queries)
    eligible = {rid for rid, view in views.items()
                if view.status == "complete" and view.token_count + query_maximum <= maximum}
    inventory = [{**view.model_dump(mode="json"), "pair_token_reservation": (
        view.token_count + query_maximum), "ranking_eligible": rid in eligible,
        "exclusion_reason": None if rid in eligible else "complete_pair_exceeds_token_limit"}
        for rid, view in views.items()]
    return inventory, eligible


def _execute(args, output: Path, report: dict, model_factory=None) -> None:
    from sqlalchemy import create_engine, inspect

    from app.models.model_request import LocalModelPool, LocalModelRequest
    from app.services.extraction.ontology_guided.retrieval import validate_record_universe
    from app.services.extraction.ontology_guided.semantic_reranker import (
        RankingPolicy,
        RankingService,
        apply_epoch,
    )
    from app.services.llm.model_runtime import model_scope
    from app.services.llm.semantic_ranking import LocalSemanticRanking

    config = {f"{kind}_{suffix}": str(getattr(args, f"{kind}_{suffix}"))
              for kind in ("embedding", "reranker") for suffix in ("path", "manifest_path")}
    config.update(mode="rerank", batch_size=args.batch_size,
                  max_tokens_per_pair=args.max_tokens_per_pair,
                  timeout_seconds=args.timeout_seconds, device=args.device,
                  dtype=args.dtype, cuda_version=args.cuda_version)
    policy = RankingPolicy(
        mode="semantic", failure_policy="pause", pool_size=args.pool_size,
        batch_size=args.batch_size, max_tokens_per_pair=args.max_tokens_per_pair,
        ranking_timeout=args.timeout_seconds, technical_retry_limit=0,
        max_ranking_tokens_per_slot=args.max_tokens_per_slot,
        max_ranking_tokens_per_run=args.max_tokens_per_run,
    )
    report.update(configuration=config, policy=policy.model_dump(mode="json"), epochs=[])
    bind = create_engine(f"sqlite:///{output / 'scheduler.sqlite3'}")
    LocalModelPool.__table__.create(bind)
    LocalModelRequest.__table__.create(bind)
    report["scheduler_tables"] = inspect(bind).get_table_names()
    factory = model_factory or LocalSemanticRanking
    model = restored_model = service = None
    try:
        started = time.monotonic()
        model = factory(config)
        report["factory_seconds"] = time.monotonic() - started
        report["model_identity"] = model.identity
        arguments = _case(args, output, report)
        original_ledger = arguments["plan"].model_dump(mode="json")["ledger"]
        service = RankingService(policy, model)
        service.before_model_hook = lambda state: _write_json(
            output / "ranking_reserved_state.json", state)
        eligible = set(arguments["plan"].frozen_record_ids)
        ranked = set()
        with model_scope(bind=bind, run_id=report["run_id"], task_id="frozen-document-ranking"):
            for number in range(1, args.epochs + 1):
                if number > 1 and not eligible - ranked:
                    break
                started = time.monotonic()
                before_requests = len(_scheduler_report(bind, report["run_id"]))
                epoch = service.prepare_next_epoch(**arguments)
                _require(epoch is not None, "expected_ranking_pool_missing")
                _write_json(output / f"epoch_{number}_prepared.json", epoch.model_dump(mode="json"))
                _require(epoch.actual_ranking_mode == "semantic" and not epoch.degraded,
                         f"semantic_pool_failed:{epoch.reason}")
                _require(len(epoch.observations) == 2 * len(epoch.record_ids)
                         and {item["retrieval_intent"] for item in epoch.observations}
                         == {"discover", "counterevidence"}
                         and all(item["raw_rerank_score"] is not None
                                 for item in epoch.observations), "dual_intent_pool_incomplete")
                _require(all(not view["omitted_refs"] for view in epoch.retrieval_views),
                         "pool_contains_truncated_source")
                committed = service.commit_epoch(epoch)
                _require(committed.status == "committed", "pool_not_committed")
                inventory, eligible = _inventory(service, arguments, args.max_tokens_per_pair)
                _require(len(committed.record_ids) == min(args.pool_size, len(eligible - ranked))
                         and set(committed.record_ids) <= eligible - ranked,
                         "eligible_pool_missing_or_duplicate_records")
                ranked.update(committed.record_ids)
                arguments["plan"] = apply_epoch(arguments["plan"], committed)
                validate_record_universe(arguments["plan"], arguments["index"])
                _require(arguments["plan"].model_dump(mode="json")["ledger"] == original_ledger,
                         "ranking_changed_semantic_coverage")
                _write_json(output / f"epoch_{number}_committed.json",
                            committed.model_dump(mode="json"))
                _write_json(output / "record_inventory.json", inventory)
                report["epochs"].append({
                    "epoch_id": committed.epoch_id, "status": committed.status,
                    "temperature": "cold_worker_and_models" if number == 1
                    else "warm_models_and_record_cache", "records": len(committed.record_ids),
                    "actual_ranking_mode": committed.actual_ranking_mode, "degraded": False,
                    "costs": committed.costs, "elapsed_seconds": time.monotonic() - started,
                    "scheduler_requests": len(_scheduler_report(bind, report["run_id"]))
                    - before_requests,
                })
            state = service.snapshot()
            _write_json(output / "ranking_state.json", state)
            _write_json(output / "retrieval_plan_final.json",
                        arguments["plan"].model_dump(mode="json"))
            report["coverage"] = {
                "total_records": len(original_ledger), "ranking_eligible": len(eligible),
                "long_records_excluded_from_ranking": len(original_ledger) - len(eligible),
                "ranked_records": len(ranked), "eligible_not_ranked": len(eligible - ranked),
                "ledger_unattempted": len(original_ledger), "recognition_records_examined": 0,
                "full_universe_preserved": True, "ranking_did_not_change_coverage": True,
            }
            model.close()
            restored_model = factory(config)
            before_requests = len(_scheduler_report(bind, report["run_id"]))
            started = time.monotonic()
            restored = RankingService(policy, restored_model, state=json.loads(
                (output / "ranking_state.json").read_text(encoding="utf-8")))
            for epoch in restored.epochs:
                restored.validate_epoch(epoch, **arguments)
                _require(restored.commit_epoch(epoch) == epoch, "restored_epoch_changed")
            _require(restored.snapshot() == state, "restored_state_changed")
            extra = len(_scheduler_report(bind, report["run_id"])) - before_requests
            _require(extra == 0 and not restored_model.observations, "restore_recalled_model")
            report["restore"] = {"extra_scheduler_requests": extra, "state_unchanged": True,
                                 "validated_epochs": len(restored.epochs),
                                 "elapsed_seconds": time.monotonic() - started,
                                 "operation": "validate_and_idempotently_replay_committed_epochs"}
            _write_json(output / "ranking_restored_state.json", restored.snapshot())
    finally:
        for item in (model, restored_model):
            if item is not None:
                item.close()
        if service is not None:
            _write_json(output / "ranking_final_state.json", service.snapshot())
        requests = _scheduler_report(bind, report["run_id"])
        _write_json(output / "scheduler_requests.json", requests)
        observations = model.observations if model is not None else []
        report["gpu_memory"] = _memory_summary(observations)
        _write_json(output / "model_observations.json", observations)
        report["observed_costs"] = {
            "scheduler_requests": len(requests),
            "completed_requests": sum(row["status"] == "completed" for row in requests),
            "measured_input_tokens": sum(row["metrics"].get("input_tokens") or 0
                                         for row in requests),
            "unknown_request_count": sum(row["metrics"].get("input_tokens") is None
                                         for row in requests),
            "queue_seconds": sum(row["metrics"].get("queue_seconds") or 0 for row in requests),
            "request_seconds": sum(row["metrics"].get("request_seconds") or 0 for row in requests),
            "reserved_input_tokens": service.costs["tokens"] if service else 0,
        }
        bind.dispose()
    _require(requests and all(row["status"] == "completed" for row in requests),
             "scheduler_has_failed_or_incomplete_requests")
    _require(report["observed_costs"]["measured_input_tokens"] > 0,
             "actual_input_token_cost_missing")


def run_check(args, *, model_factory=None) -> dict:
    output = args.output_dir.resolve()
    args.prepared = args.prepared.resolve()
    _require(output != args.prepared and args.prepared not in output.parents,
             "output_must_not_mutate_prepared_snapshot")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {
        "schema_version": "document-semantic-ranking-check-v2", "run_id": uuid4().hex,
        "started_at": datetime.now(UTC).isoformat(), "status": "running", "hardware": _hardware(),
        "scope": {"input_origin": "frozen_user_document", "device": args.device,
                  "dtype": args.dtype,
                  "main_llm_called": False, "business_facts_written": False,
                  "gold_quality_evaluation": False,
                  "scheduler_database": str(output / "scheduler.sqlite3")},
        "command": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        "harness_sha256": _digest(Path(__file__)),
    }
    overrides = {"DATABASE_URL": f"sqlite:///{output / 'scheduler.sqlite3'}",
                 "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                 "TOKENIZERS_PARALLELISM": "false"}
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        _execute(args, output, report, model_factory=model_factory)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["artifact_sha256"] = {
            path.name: _digest(path) for path in sorted(output.iterdir())
            if path.is_file() and path.name != "report.json"
        }
        _write_json(output / "report.json", report)
    return report


def main(argv=None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.output_dir.exists():
        argument_parser.error("--output-dir must be a new directory")
    report = run_check(args)
    print(json.dumps({"status": report["status"], "report": str(args.output_dir / "report.json"),
                      "failure": report.get("failure")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
