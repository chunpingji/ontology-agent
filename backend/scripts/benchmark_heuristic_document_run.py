#!/usr/bin/env python3
"""Freeze and measure an isolated heuristic-first document-graph experiment.

Preparation uses only the standard library and never calls a model. Execution
is accepted only from the frozen runner, using its matching frozen app package.
This is an observer over the shared core, not a second recognition algorithm.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

VERSION = "heuristic-document-benchmark-v1"
CMC_ROOT = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
DD = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
EQ = "https://ontology.pharma-gmp.cn/slpra/equipment/"
REPAIR_FOCUS_PATHS = [
    (DD + "usesEquipment", EQ + "equipmentID"),
    (DD + "usesEquipment", EQ + "modelSpecification"),
    (DD + "hasProductionPlan", DD + "plannedProductionDate"),
    (DD + "hasProductionPlan", DD + "plannedBatchCount"),
]
EXPECTED_SOURCE_HASH = "2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436"
CONFIG_KEYS = frozenset({
    "local_llm_enabled", "local_llm_base_url", "local_llm_model",
    "local_llm_model_revision", "local_llm_tokenizer_path", "local_llm_tokenizer_backend",
    "local_llm_server_model_path", "local_llm_temperature", "local_llm_max_tokens",
    "local_llm_max_concurrency", "local_llm_total_timeout_s",
    "evidence_max_input_tokens", "evidence_max_output_tokens", "evidence_max_regions_per_task",
    "evidence_max_objects_per_task", "evidence_timeout_s", "evidence_total_timeout_s",
    "semantic_ranking_enabled", "semantic_ranking_budget_enabled", "semantic_ranking_mode",
    "semantic_ranking_failure_policy", "semantic_ranking_embedding_path",
    "semantic_ranking_embedding_manifest_path", "semantic_ranking_reranker_path",
    "semantic_ranking_reranker_manifest_path", "semantic_ranking_device",
    "semantic_ranking_dtype", "semantic_ranking_cuda_version", "semantic_ranking_pool_size",
    "semantic_ranking_batch_size", "semantic_ranking_max_tokens_per_pair",
    "semantic_ranking_max_tokens_per_slot", "semantic_ranking_max_tokens_per_run",
    "semantic_ranking_timeout_seconds", "semantic_ranking_retry_limit",
})


def utc_now():
    return datetime.now(UTC).isoformat()


def focus_paths_observed(history):
    """Timing stop only: required properties must belong to the same parent.

    No values or reference locations enter this test; original-source quality
    is reviewed after the run. It never declares whole-document completion.
    """
    by_subject = defaultdict(set)
    for item in history:
        path = tuple(item.get("predicate_path", []))
        if item.get("currently_effective") and path in REPAIR_FOCUS_PATHS:
            by_subject[(path[0], item["subject_ref"]["id"])].add(path[1])
    required = defaultdict(set)
    for relationship, attribute in REPAIR_FOCUS_PATHS:
        required[relationship].add(attribute)
    return all(any(root == relationship and fields <= values
                   for (root, _subject), values in by_subject.items())
               for relationship, fields in required.items())


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    return path.stat().st_size


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tree_hashes(root):
    root = Path(root)
    return {
        str(path.relative_to(root)): digest_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def local_endpoint(value, *, resolve=False):
    parsed = urllib.parse.urlsplit(str(value))
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError("model endpoint must be a plain local HTTP(S) URL without credentials")
    host = parsed.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if host == "localhost":
            addresses = [ipaddress.ip_address("127.0.0.1")]
        elif resolve:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
            ]
        else:
            return  # DNS identity is checked before any execution-side HTTP request.
    if not addresses or any(not (item.is_loopback or item.is_private) for item in addresses):
        raise ValueError("model endpoint resolved outside the local/private network")


def load_model_config(path):
    value = read_json(path)
    if not isinstance(value, dict) or set(value) - CONFIG_KEYS:
        raise ValueError("model-config contains unsupported or secret configuration keys")
    required = {"local_llm_base_url", "local_llm_model", "local_llm_model_revision"}
    if any(not value.get(key) for key in required) or value.get("local_llm_enabled") is not True:
        raise ValueError("an explicitly enabled local model with immutable revision is required")
    local_endpoint(value["local_llm_base_url"])
    for key, item in value.items():
        if key.endswith("_path") and item and not Path(item).is_absolute():
            raise ValueError(f"{key} must be an absolute local path")
    return value


def prepare(args):
    source = Path(args.source_docx).resolve()
    ontology = Path(args.ontology_dir).resolve()
    output = Path(args.output).resolve()
    backend = Path(__file__).resolve().parents[1]
    if output.exists():
        raise FileExistsError("use a new preparation directory")
    if not source.is_file() or source.suffix.lower() != ".docx":
        raise ValueError("source-docx must be an existing DOCX file")
    if source.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("source DOCX exceeds the 50 MiB experiment limit")
    with zipfile.ZipFile(source) as archive:
        if not {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
            raise ValueError("source is not a Word DOCX package")
    source_hash = digest_file(source)
    if source_hash != args.expected_source_hash:
        raise ValueError("source SHA256 differs from the pre-registered HRS-5592 document")
    config = load_model_config(args.model_config)
    config["semantic_ranking_pool_size"] = args.semantic_pool_size
    config["semantic_ranking_batch_size"] = 4
    config["local_llm_max_concurrency"] = 1
    policy = {
        "initial_page_size": args.initial_page_size,
        "expanded_page_size": args.expanded_page_size,
        "exploration_page_size": args.exploration_page_size,
        "max_exploration_pages": args.max_exploration_pages,
    }
    ranking_policy = {
        "fill_candidate_pool": False,
        "protected_quota": 0, "exploration_quota": 0,
        "structure_quota": 4, "dense_quota": 8, "metadata_quota": 4,
    }
    source_code = {str(p.relative_to(backend / "app")): digest_file(p)
                   for p in sorted((backend / "app").rglob("*.py"))}
    ontology_hashes = tree_hashes(ontology)
    if not ontology_hashes:
        raise ValueError("ontology directory is empty")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    shutil.copy2(source, output / "source.docx")
    shutil.copytree(ontology, output / "ontology")
    for name in source_code:
        destination = output / "runtime" / "app" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backend / "app" / name, destination)
    runner = output / "runtime" / Path(__file__).name
    shutil.copy2(__file__, runner)
    for name in ("pyproject.toml", "uv.lock"):
        if (backend / name).is_file():
            shutil.copy2(backend / name, output / name)
    if source_hash != digest_file(output / "source.docx"):
        raise RuntimeError("source changed during freezing")
    if source_code != tree_hashes(output / "runtime" / "app"):
        raise RuntimeError("application code changed during freezing")
    if ontology_hashes != tree_hashes(output / "ontology"):
        raise RuntimeError("ontology changed during freezing")
    write_json(output / "model-config.json", config)
    if getattr(args, "evidence_repair", False):
        shutil.copy2(
            backend.parent / "specs/022-semantic-graph-closure/evidence-repair-validation-plan.md",
            output / "protocol.md",
        )
    files = tree_hashes(output)
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=backend, capture_output=True, text=True, check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=backend,
        capture_output=True, text=True, check=False,
    ).stdout
    manifest = {
        "version": VERSION, "created_at": utc_now(), "status": "prepared",
        "run_id": "heuristic-" + uuid.uuid4().hex,
        "source_filename": args.source_filename or source.name,
        "source_sha256": source_hash, "source_bytes": source.stat().st_size,
        "root_class_iri": CMC_ROOT, "scope_mode": "document_graph", "focus_path": [],
        "reference_is_recognition_input": False, "legacy_runner_used": False,
        "summary_generation": "disabled_structure_only",
        "source_git_head": git, "source_git_dirty": bool(dirty),
        "source_git_status_sha256": hashlib.sha256(dirty.encode()).hexdigest(),
        "files": files, "frozen_files_sha256": hashlib.sha256(encoded(files)).hexdigest(),
        "heuristic_policy_arguments": policy,
        "evidence_repair": bool(getattr(args, "evidence_repair", False)),
        "ranking_policy_overrides": ranking_policy,
        "limits": {
            "max_tasks": args.max_tasks, "max_hops": args.max_hops,
            "max_calls": args.max_calls, "deadline_seconds": args.deadline_seconds,
            "max_model_calls_per_record": args.max_model_calls_per_record,
            "main_recognition_in_flight": 1,
            "stop_after_focus_paths": bool(getattr(args, "stop_after_focus_paths", False)),
        },
        "model_config": config,
        "measurement_scope": "shared_core_with_isolated_sqlite_scheduler_and_local_artifacts",
        "production_postgresql_measured": False, "production_deployed": False,
        "formal_quality_gate": "pending_expert_reference_and_independent_runs",
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps({
        "prepared": str(output), "source_sha256": source_hash,
        "execute_argv": [sys.executable, str(runner), "--execute", str(output)],
        "model_calls": 0,
    }, ensure_ascii=False), flush=True)


class Recorder:
    def __init__(self, output):
        self.output = output
        self.started = time.perf_counter()
        self.recognition_started = None
        self.phase = "setup"
        self.lock = threading.RLock()
        self.persistence = defaultdict(lambda: {"count": 0, "seconds": 0.0, "bytes": 0})
        self.durations = defaultdict(list)
        self.stage_events = Counter()
        self.search_stage_first_seconds = {}
        self.admissions = {}
        self.model_calls = 0
        self.model_response_count = 0
        self.latest_model_state = {}
        self.latest_ranking_state = {}
        self.latest_graph = None
        self.latest_dependencies = {}
        self.milestones = {}
        self.effective_history = {}
        self.task_count = 0
        self.task_info = {}
        self.stop_reason = None
        self.sql = defaultdict(lambda: {"count": 0, "seconds": 0.0, "statements": Counter()})

    def elapsed(self):
        if self.recognition_started is None:
            return None
        return time.perf_counter() - self.recognition_started

    def event(self, kind, payload, *, filename="events.jsonl"):
        stamp = time.perf_counter()
        value = {
            "at": utc_now(), "event": kind, "elapsed_seconds": self.elapsed(),
            "total_elapsed_seconds": stamp - self.started, "payload": payload,
        }
        data = encoded(value) + b"\n"
        with self.lock:
            with (self.output / filename).open("ab") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            entry = self.persistence["jsonl"]
            entry["count"] += 1
            entry["bytes"] += len(data)
            entry["seconds"] += time.perf_counter() - stamp

    def save(self, filename, value, category="snapshot"):
        started = time.perf_counter()
        with self.lock:
            size = write_json(self.output / filename, value)
            entry = self.persistence[category]
            entry["count"] += 1
            entry["bytes"] += size
            entry["seconds"] += time.perf_counter() - started

    @contextmanager
    def timing(self, name, payload=None):
        started = time.perf_counter()
        status = "complete"
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            elapsed = time.perf_counter() - started
            self.durations[name].append(elapsed)
            self.event("stage_timing", {
                **(payload or {}), "stage": name, "seconds": elapsed, "status": status,
            })

    def search(self, kind, payload):
        stage = payload.get("stage")
        if stage:
            self.stage_events[stage] += 1
            self.search_stage_first_seconds.setdefault(stage, self.elapsed())
        if isinstance(payload.get("elapsed_seconds"), (int, float)):
            self.durations[kind].append(payload["elapsed_seconds"])
        if kind == "heuristic_admission":
            for rid in payload.get("record_ids", []):
                self.admissions[(payload["subject_id"], payload["predicate_iri"], rid)] = stage
        self.event(kind, payload)

    def model_state(self, state):
        old = len(self.latest_model_state.get("reservations", []))
        self.latest_model_state = state
        self.model_calls = len(state.get("reservations", []))
        self.save("model-calls-state.json", state, "model_reservation")
        for item in state.get("reservations", [])[old:]:
            self.event("model_reserved", item)

    def ranking_state(self, state):
        self.latest_ranking_state = state
        self.save("ranking-state.json", state, "ranking_state")

    def continue_run(self, boundary, limits):
        # All request-stage debit barriers call before_model; one task may use
        # two independent requests, so counting completed tasks is insufficient.
        if limits.get("stop_after_focus_paths") and focus_paths_observed(
            self.effective_history.values()
        ):
            self.stop_reason = self.stop_reason or "registered_focus_paths_reached"
            return False
        if self.elapsed() is not None and self.elapsed() >= limits["deadline_seconds"]:
            self.stop_reason = self.stop_reason or "experiment_deadline_reached"
            return False
        if self.model_calls >= limits["max_calls"]:
            self.stop_reason = self.stop_reason or "experiment_model_call_budget_exhausted"
            return False
        return True

    def batch(self, batch):
        self.task_count += 1
        self.latest_graph = batch.graph
        self.latest_dependencies = batch.dependency_index
        payload = batch.model_dump(mode="json")
        self.save("checkpoint.json", payload, "checkpoint")
        self.save(f"batches/{self.task_count:04d}.json", {
            "at": utc_now(), "elapsed_seconds": self.elapsed(),
            "task": payload["task"], "outcome": payload["outcome"],
            "graph": payload["graph"], "dependency_index": payload["dependency_index"],
        }, "batch_graph")
        self.event("task_committed", {
            "task": payload["task"], "outcome": payload["outcome"],
            "progress": payload["graph"]["progress"],
        }, filename="tasks.jsonl")
        self.observe_graph(batch.graph, batch.dependency_index)

    def observe_graph(self, graph, dependency_snapshot):
        from app.services.extraction.ontology_guided.dependencies import DependencyIndex
        from app.services.extraction.ontology_guided.projection import project_graph

        effective = project_graph(
            recognition_run_id=graph.recognition_run_id, run_revision=graph.run_revision,
            event_head=graph.event_head, metadata_snapshot_id=graph.metadata_snapshot_id,
            root_ref=graph.root_ref, nodes=graph.nodes, edges=graph.edges,
            properties=graph.properties, coverage=graph.coverage, progress=graph.progress,
            dependency_index=DependencyIndex.from_snapshot(dependency_snapshot),
            projection="effective", artifact_status=graph.artifact_status,
        )
        elapsed = self.elapsed()
        node_map = {node.entity_id: node for node in effective.nodes}
        paths = {effective.root_ref.id: []}
        pending = deque([effective.root_ref.id])
        adjacency = defaultdict(list)
        for edge in effective.edges:
            adjacency[edge.subject_ref.id].append(edge)
        while pending:
            subject_id = pending.popleft()
            for edge in adjacency[subject_id]:
                if edge.object_ref.id not in paths:
                    paths[edge.object_ref.id] = paths[subject_id] + [edge.predicate_iri]
                    pending.append(edge.object_ref.id)
        for item in self.effective_history.values():
            item["currently_effective"] = False
        for kind, values in (("relationship", effective.edges), ("property", effective.properties)):
            for item in values:
                key = f"{item.candidate_id}@{item.revision}"
                if key not in self.effective_history:
                    payload = item.model_dump(mode="json")
                    obj = node_map.get(item.object_ref.id) if kind == "relationship" else None
                    payload.update({
                        "kind": kind, "first_effective_seconds": elapsed,
                        "predicate_path": paths.get(item.subject_ref.id, []) + [item.predicate_iri],
                        "subject_label": node_map[item.subject_ref.id].label,
                        "object_label": obj.label if obj else None,
                        "object_class_iri": obj.class_iri if obj else None,
                        "business_correctness": "pending_independent_source_review",
                    })
                    self.effective_history[key] = payload
                    self.event("first_effective_candidate", payload)
                self.effective_history[key]["currently_effective"] = True
        self.save("graph/effective.json", effective.model_dump(mode="json"), "effective_graph")
        return effective


class ObservedAdapter:
    def __init__(self, delegate, recorder):
        from app.services.extraction.ontology_guided import model_adapter

        self.delegate = delegate
        self.model_identity = delegate.model_identity
        self.recorder = recorder
        original_request = delegate._request
        original_chat = model_adapter.chat_with_schema

        def observed_chat(*args, **kwargs):
            from app.services.llm.model_runtime import runtime

            response = original_chat(*args, **kwargs)
            # Private run artifacts only: preserve the wire value before citation
            # decoding so a failed quote can be diagnosed without guessing.
            recorder.event("model_wire_response", {
                "task_id": runtime.get().get("task_id"),
                "stage": kwargs.get("schema_name"),
                "response_scope": "parsed_model_json_before_citation_decode",
                "response": response,
            }, filename="model-wire-responses.jsonl")
            return response

        model_adapter.chat_with_schema = observed_chat

        def observed_request(*args, **kwargs):
            from app.services.llm.model_runtime import runtime

            stage = kwargs["stage"]
            with recorder.lock:
                recorder.model_response_count += 1
                ordinal = recorder.model_response_count
            payload = {
                "task_id": runtime.get().get("task_id"), "stage": stage, "ordinal": ordinal,
                "response_scope": "decoded_typed_adapter_response_before_proof_gate",
            }
            try:
                with recorder.timing(stage, {"task_id": payload["task_id"]}):
                    response = original_request(*args, **kwargs)
                payload.update(
                    status="returned",
                    response=response.model_dump(mode="json")
                    if hasattr(response, "model_dump") else response,
                )
                return response
            except BaseException as exc:
                payload.update(
                    status="failed", error_code=type(exc).__name__,
                    reason_code=getattr(exc, "reason_code", None),
                    cause_type=getattr(exc, "cause_type", None),
                    validation_errors=getattr(exc, "validation_errors", None),
                )
                raise
            finally:
                recorder.event("model_response", payload, filename="model-responses.jsonl")

        delegate._request = observed_request
        original_count = delegate.token_counter.count

        def observed_count(text):
            with recorder.timing("recognition_input_tokenization"):
                return original_count(text)

        delegate.token_counter.count = observed_count

    def inspect(self, task, context, predicate, menu):
        from app.services.llm.model_runtime import model_scope

        started = time.perf_counter()
        data = task.model_dump(mode="json")
        data["search_stage"] = self.recorder.admissions.get(
            (task.subject.entity_id, task.predicate_iri, task.record_id)
        )
        data["context_hash"] = context.context_hash
        self.recorder.task_info[task.task_id] = data.copy()
        self.recorder.event("task_started", data, filename="calls.jsonl")
        try:
            with model_scope(task_id=task.task_id):
                outcome = self.delegate.inspect(task, context, predicate, menu)
            data.update(status="complete", semantic_outcome=outcome.semantic_outcome,
                        reason_code=outcome.reason_code, model_calls=outcome.model_calls)
            return outcome
        except BaseException as exc:
            data.update(status="failed", error_code=type(exc).__name__,
                        reason_code=getattr(exc, "reason_code", None))
            raise
        finally:
            data["seconds"] = time.perf_counter() - started
            self.recorder.durations["task_inspection"].append(data["seconds"])
            self.recorder.event("task_finished", data, filename="calls.jsonl")


def slots_snapshot(base_url):
    endpoint = base_url.rstrip("/").removesuffix("/v1") + "/slots"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(endpoint, timeout=5) as response:
            slots = json.load(response)
        return {
            "at": utc_now(), "slots": [
                {key: slot.get(key) for key in ("id", "is_processing", "n_ctx", "n_prompt_tokens")}
                for slot in slots
            ],
        }
    except Exception as exc:
        return {"at": utc_now(), "unavailable": type(exc).__name__}


def initialize_settings(output, model_config, limits):
    # Move away from the workspace before importing Settings: its default
    # env_file is relative to cwd. No .env is copied into this fresh directory.
    os.chdir(output)
    os.environ.update({
        "DATABASE_URL": "sqlite:///" + str(output / "scheduler.sqlite3"),
        "LLM_CLOUD_ENABLED": "false", "LOCAL_LLM_ENABLED": "false",
        "LLM_WORD_TREE_SUMMARY_ENABLED": "false", "SEMANTIC_RANKING_ENABLED": "false",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    from app import config

    # Every settings field is supplied explicitly, preventing inherited process
    # environment from enabling unrelated capabilities or changing the run.
    defaults = {name: field.get_default(call_default_factory=True)
                for name, field in config.Settings.model_fields.items()}
    values = {
        **defaults, **model_config,
        "database_url": os.environ["DATABASE_URL"],
        "ontology_dir": output / "ontology", "owl_store_path": output / "ontology.sqlite3",
        "evidence_world_dir": output / "evidence-worlds",
        "document_analysis_storage_dir": output / "document-analysis-unused",
        "report_output_dir": output / "reports-unused",
        "anthropic_api_key": "", "local_llm_api_key": "not-needed",
        "llm_cloud_enabled": False, "llm_word_tree_summary_enabled": False,
        "semantic_alignment_enabled": False, "gliner_extraction_enabled": False,
        "evidence_timeout_retries": 0, "local_llm_max_concurrency": 1,
        "evidence_max_tasks": limits["max_tasks"],
        "document_analysis_max_model_calls_per_record": limits["max_model_calls_per_record"],
    }
    config.settings = config.Settings(_env_file=None, **values)
    return config.settings


def environment_snapshot():
    packages = {}
    for name in ("pydantic", "SQLAlchemy", "openai", "owlready2", "python-docx",
                 "torch", "sentence-transformers", "transformers", "tokenizers"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
         "--format=csv,noheader"], capture_output=True, text=True, check=False,
    ) if shutil.which("nvidia-smi") else None
    return {
        "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
        "cpu": platform.processor(), "cpu_count": os.cpu_count(), "packages": packages,
        "gpu": gpu.stdout.strip() if gpu and gpu.returncode == 0 else None,
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "llm_cache_state": "shared_service_warmth_not_reset",
        "ranking_cache_state": "fresh_run_private_worker_not_loaded_before_H2",
    }


def node_summary(recorder, effective):
    entries = list(recorder.effective_history.values())
    result = {}
    predicates = {
        "product_relation": ["describes"],
        "product_project_name": ["describes", "projectName"],
        "product_molecular_formula": ["describes", "molecularFormula"],
        "product_molecular_weight": ["describes", "molecularWeight"],
        "synthesis_route": ["hasSynthesisRoute"],
        "synthesis_step_path": ["hasSynthesisRoute", "hasStep"],
        "cleaning_relation": ["hasCleaningMethod"],
        "equipment_first_item": ["usesEquipment"],
        "material_first_item": ["hasSynthesisRoute", "hasStep", "usesMaterial"],
    }
    for name in ("first_relationship", "first_property", *predicates):
        candidates = []
        for item in entries:
            parts = [iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                     for iri in item["predicate_path"]]
            if (
                name == "first_relationship" and item["kind"] == "relationship"
                or name == "first_property" and item["kind"] == "property"
                or name in predicates and parts == predicates[name]
            ):
                candidates.append(item)
        first = min(candidates, key=lambda item: item["first_effective_seconds"], default=None)
        result[name] = {
            "first_effective_seconds": first["first_effective_seconds"] if first else None,
            "reason": "shared_proof_gate_passed" if first else "not_reached_within_this_run",
            "currently_effective": any(item["currently_effective"] for item in candidates),
            "candidate_id": first["candidate_id"] if first else None,
            "business_correctness": "pending_independent_source_review" if first else "not_scored",
        }
    result["list_completeness"] = {
        "seconds": None, "reason": "requires_independent_complete_list_review",
        "effective_edges": len(effective.edges) if effective else 0,
    }
    return result


def execute(args):
    output = Path(args.execute).resolve()
    manifest = read_json(output / "manifest.json")
    frozen_runner = output / "runtime" / Path(__file__).name
    if Path(__file__).resolve() != frozen_runner:
        raise ValueError("execute the prepared runtime/benchmark_heuristic_document_run.py")
    if manifest["version"] != VERSION or manifest["status"] != "prepared":
        raise ValueError("unsupported or already executed preparation")
    if (output / "execution-started.json").exists():
        raise FileExistsError("run already started; preserve it and prepare a new identity")
    for name, expected in manifest["files"].items():
        if digest_file(output / name) != expected:
            raise ValueError(f"frozen input changed: {name}")
    if hashlib.sha256(encoded(manifest["files"])).hexdigest() != manifest["frozen_files_sha256"]:
        raise ValueError("frozen code manifest hash mismatch")
    model_config = load_model_config(output / "model-config.json")
    if model_config != manifest["model_config"]:
        raise ValueError("model configuration no longer matches preparation")
    local_endpoint(model_config["local_llm_base_url"], resolve=True)
    sys.path.insert(0, str(output / "runtime"))
    sys.dont_write_bytecode = True
    limits = manifest["limits"]
    settings = initialize_settings(output, model_config, limits)
    import app
    if Path(app.__file__).resolve().parent != output / "runtime" / "app":
        raise RuntimeError("application import escaped frozen runtime")
    write_json(output / "execution-started.json", {"at": utc_now(), "pid": os.getpid()})
    recorder = Recorder(output)
    recorder.save("environment-before.json", environment_snapshot())

    from sqlalchemy import create_engine, event, select

    from app.models.model_request import LocalModelPool, LocalModelRequest
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided.contracts import SubjectRef
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy
    from app.services.extraction.ontology_guided.metadata import prepare_metadata
    from app.services.extraction.ontology_guided.model_adapter import configured_model_adapter
    from app.services.extraction.ontology_guided.ontology_plan import (
        compile_local_menu,
        ontology_snapshot_from_engine,
    )
    from app.services.extraction.ontology_guided.records import RecordIndex
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.llm.model_runtime import model_scope
    from app.services.llm.semantic_ranking import configured_ranking_service
    from app.services.ontology_engine import OntologyEngine

    bind = create_engine(settings.database_url, connect_args={"timeout": 30})

    @event.listens_for(bind, "before_cursor_execute")
    def before_sql(_conn, _cursor, _statement, _parameters, context, _many):
        context._benchmark_started = time.perf_counter()
        context._benchmark_phase = recorder.phase

    @event.listens_for(bind, "after_cursor_execute")
    def after_sql(_conn, _cursor, statement, _parameters, context, _many):
        with recorder.lock:
            entry = recorder.sql[context._benchmark_phase]
            entry["count"] += 1
            entry["seconds"] += time.perf_counter() - context._benchmark_started
            entry["statements"][statement.lstrip().split(None, 1)[0].upper()] += 1

    LocalModelPool.__table__.create(bind)
    LocalModelRequest.__table__.create(bind)
    ontology_engine = None
    ranking_service = None
    execution = None
    effective = None
    failure = None
    recognition_ended = None
    try:
        with recorder.timing("parse_docx_and_ir"):
            analysis = analyze_word_core(
                output / "source.docx", source_filename=manifest["source_filename"],
            )
        recorder.save("ir.json", analysis.ir.model_dump(mode="json"))
        with recorder.timing("ontology_load_and_snapshot"):
            ontology_engine = OntologyEngine(
                ontology_dir=output / "ontology", store_path=output / "ontology.sqlite3",
            )
            ontology_engine.load()
            ontology = ontology_snapshot_from_engine(ontology_engine, CMC_ROOT)
        recorder.save("ontology-snapshot.json", ontology.model_dump(mode="json"))
        with recorder.timing("structure_metadata"):
            metadata = prepare_metadata(
                analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
                summary_version="structure-only", generation_source="structure_only",
            )
        recorder.save("metadata.json", metadata.model_dump(mode="json"))
        with recorder.timing("root_menu_and_record_index"):
            root_menu = compile_local_menu(ontology, SubjectRef(
                entity_id="benchmark-root-menu", revision=1, class_iri=CMC_ROOT,
                is_document_root=True,
            ), engine=ontology_engine)
            records = RecordIndex(analysis.ir)
        recorder.save("root-menu.json", root_menu.model_dump(mode="json"))
        with recorder.timing("model_and_ranking_configuration"):
            adapter = (configured_model_adapter(protocol_version="evidence-repair-v1")
                       if manifest.get("evidence_repair") else configured_model_adapter())
            if adapter is None:
                raise RuntimeError("frozen local recognition model unavailable")
            ranking_service, ranking_identity = configured_ranking_service(
                settings, policy_overrides=manifest["ranking_policy_overrides"],
            )
        policy = (HeuristicSearchPolicy.durable(**manifest["heuristic_policy_arguments"])
                  if manifest.get("evidence_repair")
                  else HeuristicSearchPolicy(**manifest["heuristic_policy_arguments"]))
        fingerprint_inputs = {
            "manifest_hash": evidence_hash(manifest), "source_hash": analysis.ir.document_hash,
            "structure_hash": analysis.ir.structure_hash, "ontology_hash": ontology.ontology_hash,
            "metadata_hash": metadata.dependency_hash, "model_identity": adapter.model_identity,
            "ranking_identity": ranking_identity, "policy": policy.snapshot(), "limits": limits,
        }
        fingerprint = evidence_hash(fingerprint_inputs)
        recorder.save("run-identity.json", {
            **fingerprint_inputs, "run_id": manifest["run_id"], "fingerprint": fingerprint,
            "records": len(records.records), "evidence_units": len(analysis.ir.evidence_units),
            "root_relationships": len(root_menu.relationships),
            "root_properties": len(root_menu.properties),
            "root_opportunities": len(records.records)
            * (len(root_menu.relationships) + len(root_menu.properties)),
            "scope_mode": "document_graph", "reference_is_recognition_input": False,
        })
        recorder.save("slots-before.json", slots_snapshot(settings.local_llm_base_url))
        executor = OntologyGuidedExecutor(
            ontology=ontology, engine=ontology_engine, adapter=ObservedAdapter(adapter, recorder),
            max_tasks=limits["max_tasks"], max_hops=limits["max_hops"],
            max_model_calls_per_record=limits["max_model_calls_per_record"],
            progress_hook=lambda boundary: recorder.continue_run(boundary, limits),
            ranking_service=ranking_service, heuristic_policy=policy, search_hook=recorder.search,
            evidence_repair=manifest.get("evidence_repair", False),
            template_interleaving=manifest.get("evidence_repair", False),
            priority_paths=REPAIR_FOCUS_PATHS if manifest.get("evidence_repair") else [],
        )
        recorder.phase = "recognition"
        recorder.recognition_started = time.perf_counter()
        recorder.event("recognition_started", {"run_id": manifest["run_id"]})
        with model_scope(bind=bind, run_id=manifest["run_id"], task_id="experiment"):
            execution = executor.run(
                recognition_run_id=manifest["run_id"], run_fingerprint=fingerprint,
                ir=analysis.ir, metadata=metadata, root_class_iri=CMC_ROOT,
                root_class_label=ontology.classes[CMC_ROOT].label,
                filename=manifest["source_filename"], batch_hook=recorder.batch,
                ranking_hook=recorder.ranking_state, model_call_hook=recorder.model_state,
            )
        recognition_ended = time.perf_counter()
        recorder.phase = "finalization"
        recorder.latest_graph = execution.graph
        recorder.save("graph/result.json", execution.model_dump(mode="json"))
        effective = recorder.observe_graph(execution.graph, recorder.latest_dependencies)
    except BaseException as exc:
        recognition_ended = time.perf_counter()
        failure = {"type": type(exc).__name__, "reason_code": getattr(exc, "reason_code", None)}
        recorder.event("experiment_exception", failure)
        # Traceback is local to this controlled experiment, not stdout.
        (output / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        recorder.phase = "finalization"
        if ranking_service and ranking_service.model:
            ranking_service.model.close()
        if ontology_engine and ontology_engine._world:
            ontology_engine._world.close()
        with bind.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                select(LocalModelRequest.__table__).order_by(LocalModelRequest.sequence)
            ).mappings()]
        recorder.save("model-requests.json", rows)
        recorder.save("slots-after.json", slots_snapshot(settings.local_llm_base_url))
        recorder.save("environment-after.json", environment_snapshot())
        if effective is None and recorder.latest_graph is not None:
            effective = recorder.observe_graph(recorder.latest_graph, recorder.latest_dependencies)
        if execution is None:
            recorder.save("graph/result.json", {
                "graph": recorder.latest_graph.model_dump(mode="json")
                if recorder.latest_graph is not None else None,
                "exception": failure, "partial_after_exception": True,
            })
        progress = recorder.latest_graph.progress.model_dump(mode="json") \
            if recorder.latest_graph is not None else None
        stages = defaultdict(lambda: {"requests": 0, "status_counts": Counter(),
                                      "known_prompt_tokens": 0, "known_completion_tokens": 0,
                                      "known_input_tokens": 0, "unknown_input_requests": 0,
                                      "unknown_prompt_requests": 0,
                                      "unknown_completion_requests": 0,
                                      "queue_seconds": 0.0, "request_seconds": 0.0})
        requests_by_search_stage = defaultdict(lambda: {"requests": 0, "request_seconds": 0.0})
        for row in rows:
            stage = stages[row["stage"]]
            stage["requests"] += 1
            stage["status_counts"][row["status"]] += 1
            metrics = row["metrics"] or {}
            search_stage = (
                "H2" if row["stage"].startswith("ranking_")
                else recorder.task_info.get(row["task_id"], {}).get("search_stage")
            ) or "unattributed"
            requests_by_search_stage[search_stage]["requests"] += 1
            requests_by_search_stage[search_stage]["request_seconds"] += (
                metrics.get("request_seconds") or 0
            )
            if metrics.get("input_tokens") is None:
                stage["unknown_input_requests"] += 1
            else:
                stage["known_input_tokens"] += metrics["input_tokens"]
            for field in ("prompt", "completion"):
                value = metrics.get(field + "_tokens")
                if value is None:
                    stage["unknown_" + field + "_requests"] += 1
                else:
                    stage["known_" + field + "_tokens"] += value
            for field in ("queue_seconds", "request_seconds"):
                stage[field] += metrics.get(field) or 0
        recognition_seconds = (
            recognition_ended - recorder.recognition_started
            if recorder.recognition_started is not None and recognition_ended is not None else None
        )
        summary = {
            "version": VERSION, "run_id": manifest["run_id"], "completed_at": utc_now(),
            "experiment_status": "failed" if failure else "finished",
            "exception": failure, "recognition_seconds": recognition_seconds,
            "total_including_setup_seconds": time.perf_counter() - recorder.started,
            "deadline_tail_seconds": max(0, recognition_seconds - limits["deadline_seconds"])
            if recognition_seconds is not None else None,
            "requested_stop_reason": recorder.stop_reason, "progress": progress,
            "task_batches": recorder.task_count, "reserved_main_requests": recorder.model_calls,
            "node_milestones": node_summary(recorder, effective),
            "effective_candidate_history": list(recorder.effective_history.values()),
            "search_stages": {stage: {
                "events": recorder.stage_events[stage],
                "first_event_seconds": recorder.search_stage_first_seconds.get(stage),
                "status": "observed" if recorder.stage_events[stage] else "not_triggered",
            } for stage in ("H0", "H1", "H2", "H3")},
            "timings": {name: {"count": len(values), "total_seconds": sum(values),
                                "min_seconds": min(values), "max_seconds": max(values)}
                        for name, values in recorder.durations.items()},
            "timing_overlap": (
                "stage/task/persistence/SQL spans can overlap; do not sum as wall time"
            ),
            "model_request_stages": dict(stages), "sql": dict(recorder.sql),
            "model_requests_by_search_stage": dict(requests_by_search_stage),
            "local_persistence": dict(recorder.persistence),
            "ranking_observations": list(getattr(
                getattr(ranking_service, "model", None), "observations", [],
            )),
            "measurement_scope": manifest["measurement_scope"],
            "postgresql_performance": "not_measured", "production_end_to_end": "not_measured",
            "business_quality": "pending_independent_source_review",
            "formal_quality_gate": manifest["formal_quality_gate"],
            "reference_is_recognition_input": False, "production_deployed": False,
        }
        recorder.save("summary.json", summary)
        recorder.save("artifact-hashes.json", {
            str(path.relative_to(output)): digest_file(path)
            for path in sorted(output.rglob("*")) if path.is_file()
            and not path.is_relative_to(output / "runtime")
            and not path.is_relative_to(output / "ontology")
            and path.name not in {"artifact-hashes.json", "scheduler.sqlite3", "ontology.sqlite3"}
        })
        bind.dispose()
    print(json.dumps({
        "run_id": manifest["run_id"], "summary": str(output / "summary.json"),
        "recognition_seconds": summary["recognition_seconds"],
        "experiment_status": summary["experiment_status"], "progress": summary["progress"],
    }, ensure_ascii=False), flush=True)
    return 1 if failure else 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", metavar="PREPARED_DIRECTORY")
    result.add_argument("--source-docx")
    result.add_argument("--source-filename")
    result.add_argument("--ontology-dir")
    result.add_argument("--output")
    result.add_argument("--model-config")
    result.add_argument("--expected-source-hash", default=EXPECTED_SOURCE_HASH)
    result.add_argument("--max-tasks", type=int, default=48)
    result.add_argument("--max-hops", type=int, default=4)
    result.add_argument("--max-calls", type=int, default=96)
    result.add_argument("--deadline-seconds", type=float, default=1800)
    result.add_argument("--max-model-calls-per-record", type=int, default=6)
    result.add_argument("--initial-page-size", type=int, default=8)
    result.add_argument("--expanded-page-size", type=int, default=16)
    result.add_argument("--exploration-page-size", type=int, default=32)
    result.add_argument("--max-exploration-pages", type=int, default=1)
    result.add_argument("--semantic-pool-size", type=int, default=16)
    result.add_argument("--evidence-repair", action="store_true")
    result.add_argument("--stop-after-focus-paths", action="store_true")
    return result


def main():
    argument_parser = parser()
    args = argument_parser.parse_args()
    if args.prepare:
        if args.stop_after_focus_paths and not args.evidence_repair:
            argument_parser.error("--stop-after-focus-paths requires --evidence-repair")
        for field in ("source_docx", "ontology_dir", "output", "model_config"):
            if not getattr(args, field):
                argument_parser.error("--prepare requires --" + field.replace("_", "-"))
        for field in ("max_tasks", "max_hops", "max_calls", "deadline_seconds",
                      "max_model_calls_per_record", "initial_page_size", "expanded_page_size",
                      "exploration_page_size", "max_exploration_pages", "semantic_pool_size"):
            if getattr(args, field) <= 0:
                argument_parser.error(field.replace("_", "-") + " must be positive")
        prepare(args)
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
