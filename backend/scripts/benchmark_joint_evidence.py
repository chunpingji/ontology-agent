#!/usr/bin/env python3
"""Frozen, bounded source-context intervention over the unchanged recognition adapter.

This is a diagnostic task driver, not the production executor or an automatic
retrieval/identity algorithm. It never imports prior model outputs or references.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

from benchmark_heuristic_document_run import (
    CMC_ROOT,
    EXPECTED_SOURCE_HASH,
    digest_file,
    encoded,
    environment_snapshot,
    initialize_settings,
    load_model_config,
    local_endpoint,
    read_json,
    slots_snapshot,
    tree_hashes,
    utc_now,
    write_json,
)

VERSION = "joint-evidence-diagnostic-v1"
DD = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
EQ = "https://ontology.pharma-gmp.cn/slpra/equipment/"


def paragraph(number):
    return {"paragraph": number}


def table_row(table, row):
    return {"table": table, "row": row}


def cases():
    registered = [
        {
            "id": "product_name", "predicate": DD + "describes", "target": paragraph(33),
            "support": [paragraph(i) for i in (25, 26, 27)],
            "children": [
                {"predicate": "https://ontology.pharma-gmp.cn/slpra/drug/projectName",
                 "target": paragraph(33)},
                {"predicate": "https://ontology.pharma-gmp.cn/slpra/drug/appearance",
                 "target": paragraph(36)},
            ],
        },
        {
            "id": "production_plan", "predicate": DD + "hasProductionPlan",
            "target": paragraph(26), "support": [paragraph(25), paragraph(27)],
            "children": [
                {"predicate": DD + "plannedProductionDate", "target": paragraph(26)},
                {"predicate": DD + "plannedBatchCount", "target": paragraph(26)},
                {"predicate": DD + "plannedProductionDate", "target": paragraph(19),
                 "id": "wrong_document_date"},
            ],
        },
        {
            "id": "equipment", "predicate": DD + "usesEquipment",
            "target": table_row(9, 1), "support": [table_row(0, 12), table_row(0, 13)],
            "children": [
                {"predicate": EQ + "equipmentID", "target": table_row(9, 1)},
                {"predicate": EQ + "modelSpecification", "target": table_row(9, 1)},
            ],
        },
        {
            "id": "route", "predicate": DD + "hasSynthesisRoute", "target": paragraph(53),
            "support": [paragraph(i) for i in (57, 61, 64, 68, 123, 124, 125, 126, 127)],
            "children": [{"predicate": DD + "processDescription", "target": paragraph(124)}],
        },
        {
            "id": "cytotoxic_confusion", "predicate": DD + "describes",
            "target": paragraph(25), "support": [paragraph(i) for i in range(33, 50)],
            "children": [],
        },
    ]
    # Reserve the negative classification control before gated child tasks can
    # consume the budget. Any tail omissions remain explicit in the report.
    return [registered[i] for i in (0, 4, 1, 2, 3)]


def prepare(args):
    backend = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    source = Path(args.source).resolve()
    if output.exists():
        raise FileExistsError("preserve existing runs; choose a new directory")
    if digest_file(source) != EXPECTED_SOURCE_HASH:
        raise ValueError("HRS-5592 source hash mismatch")
    config = load_model_config(args.model_config)
    config.update(semantic_ranking_enabled=False, local_llm_max_concurrency=1)
    output.mkdir(mode=0o700)
    shutil.copy2(source, output / "source.docx")
    shutil.copytree(backend.parent / "ontology/slpra", output / "ontology")
    source_hashes = {}
    for path in sorted((backend / "app").rglob("*.py")):
        destination = output / "runtime/app" / path.relative_to(backend / "app")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_hashes[str(destination.relative_to(output))] = digest_file(path)
        shutil.copy2(path, destination)
    for name in (Path(__file__).name, "benchmark_heuristic_document_run.py"):
        shutil.copy2(backend / "scripts" / name, output / "runtime" / name)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(backend / name, output / name)
    write_json(output / "model-config.json", config)
    registered_cases = cases()
    if getattr(args, "evidence_repair", False):
        registered_cases = [
            c for c in registered_cases if c["id"] in {"production_plan", "equipment"}
        ]
    write_json(output / "cases.json", registered_cases)
    shutil.copy2(
        backend.parent / "specs/022-semantic-graph-closure" / (
            "evidence-repair-validation-plan.md" if getattr(args, "evidence_repair", False)
            else "joint-evidence-validation-plan.md"
        ),
        output / "protocol.md",
    )
    files = tree_hashes(output)
    if any(files[name] != expected for name, expected in source_hashes.items()):
        raise RuntimeError("source changed during preparation")
    write_json(output / "manifest.json", {
        "version": VERSION, "created_at": utc_now(), "run_id": "joint-" + uuid.uuid4().hex,
        "evidence_repair": bool(getattr(args, "evidence_repair", False)),
        "source_sha256": EXPECTED_SOURCE_HASH, "files": files,
        "frozen_files_sha256": hashlib.sha256(encoded(files)).hexdigest(),
        "limits": {"max_tasks": 32, "max_calls": 48, "deadline_seconds": 1200,
                   "max_model_calls_per_record": (
                       8 if getattr(args, "evidence_repair", False) else 4)},
        "reference_is_recognition_input": False, "manual_source_context_intervention": True,
        "production_executor_used": False, "production_adapter_and_proof_gate_used": True,
        "conflict_revalidation_measured": False, "projection_only": True,
        "automatic_retrieval_measured": False, "production_deployed": False,
        "root_class_iri": CMC_ROOT,
    })
    print(json.dumps({"prepared": str(output), "model_calls": 0}, ensure_ascii=False))


def selected_units(index, selector):
    if "paragraph" in selector:
        units = [unit for unit in index.ir.evidence_units
                 if unit.table_path is None and unit.paragraph_index == selector["paragraph"]]
    else:
        records = [record for record in index.records
                   if tuple(record.table_path or ()) == (f"table:{selector['table']}",)
                   and record.row_index == selector["row"]]
        if len(records) != 1:
            raise ValueError(f"ambiguous table row: {selector}")
        units = list(records[0].source_units)
    units = [unit for unit in units if unit.text]
    if not units:
        raise ValueError(f"empty original-source selector: {selector}")
    return units


def selected_record(index, selector):
    if "table" in selector:
        records = [record for record in index.records
                   if tuple(record.table_path or ()) == (f"table:{selector['table']}",)
                   and record.row_index == selector["row"]]
        if len(records) != 1:
            raise ValueError(f"ambiguous target table row: {selector}")
        return records[0]
    ids = {unit.evidence_id for unit in selected_units(index, selector)}
    records = [record for record in index.records
               if any(unit.evidence_id in ids for unit in record.source_units)]
    if len(records) != 1:
        raise ValueError(f"target must resolve to exactly one record: {selector}")
    return records[0]


class BudgetStop(RuntimeError):
    pass


class Experiment:
    def __init__(self, output, manifest):
        self.output, self.manifest = output, manifest
        self.started = time.perf_counter()
        self.recognition_started = None
        self.calls, self.tasks = 0, []
        self.seen_inputs = set()
        self.active = None
        self.reservations = []
        self.lineage_calls = {}

    def event(self, kind, data):
        row = {"at": utc_now(), "event": kind,
               "elapsed_seconds": time.perf_counter() - self.started, **data}
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return row

    def reserve(self, stage, ordinal):
        limits = self.manifest["limits"]
        if self.calls >= limits["max_calls"]:
            raise BudgetStop("request_budget_exhausted")
        if self.lineage_calls.get(self.active_lineage, 0) >= limits[
            "max_model_calls_per_record"
        ]:
            raise BudgetStop("lineage_budget_exhausted")
        if time.perf_counter() - self.recognition_started >= limits["deadline_seconds"]:
            raise BudgetStop("deadline_exhausted")
        self.calls += 1
        self.lineage_calls[self.active_lineage] = self.lineage_calls.get(self.active_lineage, 0) + 1
        row = self.event("before_model", {
            "call": self.calls, "task": self.active, "stage": stage, "ordinal": ordinal,
            "lineage": self.active_lineage,
        })
        self.reservations.append(row)
        write_json(self.output / "reservations.json", self.reservations)

    def inspect(self, *, case_id, arm, subject, node, predicate, record, support, parent_edge=None):
        from app.services.extraction.evidence_identity import evidence_hash
        from app.services.extraction.ontology_guided.context import assemble_context
        from app.services.extraction.ontology_guided.contracts import (
            DocumentContext,
            VerificationTarget,
            VersionedRef,
        )
        from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
        from app.services.extraction.ontology_guided.scheduler import RecognitionTask
        from app.services.llm.model_runtime import model_scope

        if len(self.tasks) >= self.manifest["limits"]["max_tasks"]:
            raise BudgetStop("task_budget_exhausted")
        if self.calls >= self.manifest["limits"]["max_calls"]:
            raise BudgetStop("request_budget_exhausted")
        if time.perf_counter() - self.recognition_started >= self.manifest["limits"][
            "deadline_seconds"
        ]:
            raise BudgetStop("deadline_exhausted")
        started = time.perf_counter()
        menu = compile_local_menu(self.ontology, subject, engine=self.engine)
        spec = next((item for item in [*menu.relationships, *menu.properties]
                     if item.iri == predicate), None)
        if spec is None:
            result = {"case": case_id, "arm": arm, "predicate": predicate,
                      "status": "predicate_not_in_actual_subject_menu", "seconds": 0}
            self.event("task_skipped", result)
            return result, None
        task = RecognitionTask.create(
            subject=subject, predicate_iri=predicate, predicate_kind=spec.kind,
            record_id=record.record_id, phase=1, hop=0 if subject.is_document_root else 1,
            dependency_hash=evidence_hash([self.fingerprint, support]),
            retry_kind="source_context_intervention" if arm == "joint" else None,
        )
        target = VerificationTarget.create(
            run_fingerprint=self.fingerprint,
            claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1), task_id=task.task_id,
            check_kind="predicate_entailment", subject_ref=subject, predicate_iri=predicate,
            predicate_spec_ref=VersionedRef(id=predicate, revision=1),
            document_context=DocumentContext(
                document_hash=self.index.ir.document_hash, document_class_iri=CMC_ROOT,
                root_ref=VersionedRef(id=self.root.entity_id, revision=1),
            ),
            ontology_hash=self.ontology.ontology_hash,
            source_scope_hash=self.index.ir.structure_hash, context_hash=self.fingerprint,
        )
        required = list(parent_edge.evidence_refs) if parent_edge else []
        for reference in support:
            if reference not in required:
                required.append(reference)
        context = assemble_context(
            target, record.record_id, self.index, subject_label=node.label,
            predicate=spec, ontology=self.ontology,
            subject_evidence_refs=node.evidence_refs if not subject.is_document_root else [],
            proof_dependencies=[VersionedRef(id=f"subject:{subject.entity_id}",
                                             revision=subject.revision)] if parent_edge else [],
            required_context_refs=required,
            token_counter=self.adapter.token_counter,
            max_input_tokens=self.settings.evidence_max_input_tokens,
            repair_enabled=self.manifest.get("evidence_repair", False),
        )
        input_key = (case_id, subject.entity_id, predicate, context.context_hash)
        if input_key in self.seen_inputs:
            raise ValueError("duplicate diagnostic input must not be retried")
        self.seen_inputs.add(input_key)
        context.remaining_model_calls = min(
            2, self.manifest["limits"]["max_calls"] - self.calls,
            self.manifest["limits"]["max_model_calls_per_record"]
            - self.lineage_calls.get(task.claim_lineage_id, 0),
        )
        context.bind_model_call_hook(self.reserve)
        self.active = f"{case_id}-{arm}"
        self.active_lineage = task.claim_lineage_id
        task_dir = self.output / "tasks" / f"{len(self.tasks)+1:02d}-{self.active}"
        write_json(task_dir / "input.json", {
            "task": task.model_dump(mode="json"), "context": context.model_dump(mode="json"),
            "predicate": spec.model_dump(mode="json"), "menu": menu.model_dump(mode="json"),
        })
        if self.manifest.get("evidence_repair"):
            context.bind_protocol_hook(
                lambda state: write_json(task_dir / "protocol-state.json", state)
            )
        result = {
            "case": case_id, "arm": arm, "predicate": predicate, "task_id": task.task_id,
            "lineage": task.claim_lineage_id, "context_hash": context.context_hash,
            "target_id": context.target.target_id, "record_id": record.record_id,
            "subject_id": subject.entity_id, "subject_class": subject.class_iri,
            "fragments": len(context.fragments), "context_tokens": context.token_count,
            "assembly_seconds": time.perf_counter() - started,
            "started_seconds": started - self.recognition_started,
            "input_file": str((task_dir / "input.json").relative_to(self.output)),
        }
        self.tasks.append(result)
        self.event("task_started", result)
        self.active_dir = task_dir
        before_calls = self.calls
        outcome = None
        try:
            with model_scope(bind=self.bind, run_id=self.manifest["run_id"], task_id=task.task_id):
                outcome = self.adapter.inspect(task, context, spec, menu)
                if self.manifest.get("evidence_repair"):
                    from app.services.extraction.ontology_guided.evidence_work import (
                        EvidenceWorkQueue,
                    )

                    work = EvidenceWorkQueue(self.index)
                    recheck = work.observe(task, outcome, context, spec, context.protocol_state)
                    # Keep the preregistered source intervention fixed. Only a
                    # bounded change of the frozen assertion is exercised here;
                    # automatic source supplementation belongs to the executor arm.
                    if recheck is not None and recheck.retry_kind == "rediscovery:1":
                        write_json(
                            task_dir / "initial-outcome.json", outcome.model_dump(mode="json")
                        )
                        result["reproposal"] = recheck.model_dump(mode="json")
                        context.remaining_model_calls = min(
                            2, self.manifest["limits"]["max_calls"] - self.calls,
                            self.manifest["limits"]["max_model_calls_per_record"]
                            - self.lineage_calls.get(task.claim_lineage_id, 0),
                        )
                        outcome = self.adapter.inspect(recheck, context, spec, menu)
            result.update(status=outcome.semantic_outcome, complete=outcome.complete,
                          reason_code=outcome.reason_code, reason=outcome.reason)
            write_json(task_dir / "outcome.json", outcome.model_dump(mode="json"))
        except BudgetStop:
            result.update(status="not_checked", complete=False, reason_code="budget_exhausted")
            raise
        except Exception as exc:
            result.update(status="technical_failure", complete=False,
                          reason_code=getattr(exc, "reason_code", type(exc).__name__),
                          validation_errors=getattr(exc, "validation_errors", None))
            (task_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            result.update(seconds=time.perf_counter() - started,
                          model_calls=self.calls-before_calls,
                          ended_seconds=time.perf_counter() - self.recognition_started)
            self.event("task_finished", result)
            write_json(self.output / "task-results.json", self.tasks)
            print(json.dumps({key: result[key] for key in
                              ("case", "arm", "status", "seconds", "model_calls")},
                             ensure_ascii=False), flush=True)
        return result, outcome


def execute(args):
    output = Path(args.execute).resolve()
    manifest = read_json(output / "manifest.json")
    if Path(__file__).resolve() != output / "runtime" / Path(__file__).name:
        raise ValueError("execute only the frozen runner")
    if manifest["version"] != VERSION or (output / "execution-started.json").exists():
        raise ValueError("unsupported or already started run")
    for name, expected in manifest["files"].items():
        if digest_file(output / name) != expected:
            raise ValueError(f"frozen input changed: {name}")
    sys.path.insert(0, str(output / "runtime"))
    sys.dont_write_bytecode = True
    config = load_model_config(output / "model-config.json")
    local_endpoint(config["local_llm_base_url"], resolve=True)
    settings = initialize_settings(output, config, manifest["limits"])
    import app
    if Path(app.__file__).resolve().parent != output / "runtime/app":
        raise RuntimeError("application escaped frozen runtime")
    write_json(output / "execution-started.json", {"at": utc_now(), "pid": os.getpid()})

    from sqlalchemy import create_engine, select

    from app.models.model_request import LocalModelPool, LocalModelRequest
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided import model_adapter
    from app.services.extraction.ontology_guided.contracts import (
        RunProgress,
        SubjectRef,
        VersionedRef,
    )
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from app.services.extraction.ontology_guided.ontology_plan import ontology_snapshot_from_engine
    from app.services.extraction.ontology_guided.projection import project_graph
    from app.services.extraction.ontology_guided.records import RecordIndex
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.ontology_engine import OntologyEngine

    experiment = Experiment(output, manifest)
    experiment.settings = settings
    experiment.bind = create_engine(settings.database_url, connect_args={"timeout": 30})
    LocalModelPool.__table__.create(experiment.bind)
    LocalModelRequest.__table__.create(experiment.bind)
    experiment.engine = None
    stop_reason = "registered_tasks_finished"
    failure = None
    graphs = {}
    try:
        write_json(output / "environment-before.json", environment_snapshot())
        write_json(output / "slots-before.json", slots_snapshot(settings.local_llm_base_url))
        started = time.perf_counter()
        analysis = analyze_word_core(output / "source.docx")
        experiment.index = RecordIndex(analysis.ir)
        write_json(output / "ir.json", analysis.ir.model_dump(mode="json"))
        experiment.event("source_prepared", {"seconds": time.perf_counter()-started})
        started = time.perf_counter()
        experiment.engine = OntologyEngine(ontology_dir=output / "ontology",
                                           store_path=output / "ontology.sqlite3")
        experiment.engine.load()
        experiment.ontology = ontology_snapshot_from_engine(experiment.engine, CMC_ROOT)
        write_json(output / "ontology-snapshot.json", experiment.ontology.model_dump(mode="json"))
        experiment.event("ontology_prepared", {"seconds": time.perf_counter()-started})
        experiment.fingerprint = evidence_hash(manifest)
        experiment.root = OntologyGuidedExecutor.root_node(
            recognition_run_id=manifest["run_id"], document_hash=analysis.ir.document_hash,
            root_class_iri=CMC_ROOT, root_class_label="CMC报告", filename="HRS-5592 source.docx",
        )
        root_subject = SubjectRef(entity_id=experiment.root.entity_id, revision=1,
                                  class_iri=CMC_ROOT, is_document_root=True)
        experiment.adapter = (model_adapter.configured_model_adapter(
                                  protocol_version="evidence-repair-v1")
                              if manifest.get("evidence_repair")
                              else model_adapter.configured_model_adapter())
        if experiment.adapter is None:
            raise RuntimeError("local model not available")
        transport = model_adapter.chat_with_schema

        def observed_transport(client, **kwargs):
            started = time.perf_counter()
            path = experiment.active_dir / f"request-{experiment.calls:03d}.json"
            write_json(path, {key: kwargs[key] for key in ("system", "user", "schema")})
            raw = None
            error = None
            try:
                raw = transport(client, **kwargs)
                return raw
            except Exception as exc:
                error = type(exc).__name__
                raise
            finally:
                seconds = time.perf_counter()-started
                write_json(path.with_name(path.stem + "-response.json"), {
                    "raw": raw, "error": error, "seconds": seconds,
                })
                experiment.event("model_returned", {
                    "call": experiment.calls, "task": experiment.active,
                    "stage": kwargs["schema_name"], "seconds": seconds, "error": error,
                })

        model_adapter.chat_with_schema = observed_transport
        all_cases = read_json(output / "cases.json")
        # Fail before generating if a preregistered source position is invalid.
        for case in all_cases:
            selected_record(experiment.index, case["target"])
            for selector in case["support"]:
                selected_units(experiment.index, selector)
            for child in case["children"]:
                selected_record(experiment.index, child["target"])
        experiment.recognition_started = time.perf_counter()

        def projection(nodes, edges, properties):
            return project_graph(
                recognition_run_id=manifest["run_id"], run_revision=1, event_head=0,
                metadata_snapshot_id=None, root_ref=VersionedRef(id=experiment.root.entity_id,
                                                                revision=1),
                nodes=nodes, edges=edges, properties=properties, coverage=[],
                progress=RunProgress(),
                projection="effective", artifact_status="partial",
            )

        for position, case in enumerate(all_cases):
            record = selected_record(experiment.index, case["target"])
            support = [analysis.ir.anchor(unit.evidence_id)
                       for selector in case["support"]
                       for unit in selected_units(experiment.index, selector)]
            arms = ("current", "joint") if position % 2 == 0 else ("joint", "current")
            # First finish both parent arms, then observe their gated descendants.
            parents = {}
            for arm in arms:
                result, outcome = experiment.inspect(
                    case_id=case["id"], arm=arm, subject=root_subject, node=experiment.root,
                    predicate=case["predicate"], record=record,
                    support=support if arm == "joint" else [],
                )
                if outcome is None:
                    parents[arm] = None
                    continue
                started = time.perf_counter()
                effective = projection([experiment.root, *outcome.nodes], outcome.edges, [])
                result["projection_seconds"] = time.perf_counter()-started
                result["effective_edges"] = len(effective.edges)
                graph_key = f"{case['id']}-{arm}"
                graphs[graph_key] = effective.model_dump(mode="json")
                write_json(output / "graphs" / f"{graph_key}.json", graphs[graph_key])
                selected = sorted(effective.edges, key=lambda edge: (
                    min((a.span_start or 0 for a in edge.object_evidence_refs), default=0),
                    edge.object_ref.id,
                ))
                parents[arm] = (outcome, selected[0]) if selected else None
            for arm in arms:
                parent = parents[arm]
                if parent is None:
                    experiment.event("children_blocked", {"case": case["id"], "arm": arm,
                                                           "reason": "parent_not_effective"})
                    continue
                outcome, edge = parent
                node = next(node for node in outcome.nodes if node.entity_id == edge.object_ref.id)
                subject = SubjectRef(entity_id=node.entity_id, revision=node.revision,
                                     class_iri=node.class_iri)
                child_properties = []
                for number, child in enumerate(case["children"]):
                    result, child_outcome = experiment.inspect(
                        case_id=f"{case['id']}-child-{number+1}-{child.get('id', 'value')}",
                        arm=arm, subject=subject, node=node, predicate=child["predicate"],
                        record=selected_record(experiment.index, child["target"]),
                        support=support if arm == "joint" else [], parent_edge=edge,
                    )
                    if child_outcome:
                        child_properties.extend(child_outcome.properties)
                        effective = projection([experiment.root, *outcome.nodes],
                                               outcome.edges, child_properties)
                        result["effective_properties_cumulative"] = len(effective.properties)
                        graph_key = f"{case['id']}-{arm}"
                        graphs[graph_key] = effective.model_dump(mode="json")
                        write_json(output / "graphs" / f"{graph_key}.json", graphs[graph_key])
    except BudgetStop as exc:
        stop_reason = str(exc)
    except Exception as exc:
        failure = {"type": type(exc).__name__, "reason": str(exc)}
        stop_reason = "experiment_failed"
        (output / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        ended = time.perf_counter()
        with experiment.bind.connect() as connection:
            rows = [dict(row) for row in connection.execute(select(LocalModelRequest.__table__))
                    .mappings()]
        write_json(output / "model-requests.json", rows)
        write_json(output / "task-results.json", experiment.tasks)
        write_json(output / "slots-after.json", slots_snapshot(settings.local_llm_base_url))
        write_json(output / "summary.json", {
            "run_id": manifest["run_id"], "stop_reason": stop_reason, "failure": failure,
            "model_reservations": experiment.calls, "scheduler_requests": len(rows),
            "tasks": len(experiment.tasks), "total_seconds": ended-experiment.started,
            "recognition_seconds": ended-experiment.recognition_started
            if experiment.recognition_started else None,
            "ontology_hash": experiment.ontology.ontology_hash
            if hasattr(experiment, "ontology") else None,
            "full_document_completion": "not_measured",
            "formal_quality_gate": "pending_expert_reference",
            "production_deployed": False,
        })
        if experiment.engine and experiment.engine._world:
            experiment.engine._world.close()
        experiment.bind.dispose()
        write_json(output / "artifact-hashes.json", tree_hashes(output))
    if failure:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--execute")
    parser.add_argument("--source")
    parser.add_argument("--model-config")
    parser.add_argument("--output")
    parser.add_argument("--evidence-repair", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare(args)
    else:
        execute(args)


if __name__ == "__main__":
    main()
