"""Pinned, offline CPU/CUDA 12 ranking with cancellable process isolation.

No weights are loaded by the factory. A private worker is created on first use,
and terminated before a cancelled/timed-out scheduler slot is released. Scores
are independent single-label logits, never probabilities or batch-local ranks.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
import multiprocessing
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock, RLock
from typing import Any
from uuid import uuid4

from app.services.llm.model_runtime import check_cancelled, model_scope
from app.services.llm.model_scheduler import HEARTBEAT_SECONDS, RequestTicket


class RankingModelUnavailable(RuntimeError):
    """Stable technical failure, with no provider text or credentials."""


CUDA_VERSION = "12.6"


def _execution_environment(device):
    keys = ["OMP_NUM_THREADS", "MKL_NUM_THREADS"]
    if device.startswith("cuda:"):
        keys += ["CUDA_VISIBLE_DEVICES", "CUBLAS_WORKSPACE_CONFIG", "NVIDIA_TF32_OVERRIDE",
                 "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE", "PYTORCH_CUDA_ALLOC_CONF",
                 "PYTORCH_ALLOC_CONF"]
    return {key: os.environ.get(key) for key in keys}


def _device_config(config):
    result = {"device": "cpu", "dtype": "float32", "cuda_version": CUDA_VERSION, **config}
    device, dtype = result["device"], result["dtype"]
    if not isinstance(device, str) or not re.fullmatch(r"cpu|cuda:(0|[1-9][0-9]*)", device):
        raise RankingModelUnavailable("ranking_invalid_device")
    if dtype not in ("float32", "float16") or (device == "cpu" and dtype != "float32"):
        raise RankingModelUnavailable("ranking_invalid_dtype")
    if result["cuda_version"] != CUDA_VERSION:
        raise RankingModelUnavailable("ranking_invalid_cuda_version")
    return result


def _execution_error(exc):
    # Inspect diagnostics only to map them to a closed, non-sensitive vocabulary.
    torch = sys.modules.get("torch")
    oom = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", ())
    if isinstance(exc, oom):
        return "ranking_cuda_out_of_memory"
    return "ranking_model_execution_failed"


def _cuda_identity(config):
    """Called only in disposable spawned processes; never initializes parent CUDA."""
    import torch

    if os.environ.get("TORCH_ALLOW_TF32_CUBLAS_OVERRIDE") == "1":
        raise RankingModelUnavailable("ranking_cuda_precision_override")
    if torch.version.cuda != config["cuda_version"]:
        raise RankingModelUnavailable("ranking_cuda_version_mismatch")
    if not torch.cuda.is_available():
        raise RankingModelUnavailable("ranking_cuda_unavailable")
    index = int(config["device"].split(":")[1])
    if index >= torch.cuda.device_count():
        raise RankingModelUnavailable("ranking_cuda_device_missing")
    torch.cuda.set_device(index)
    properties = torch.cuda.get_device_properties(index)
    capability = (properties.major, properties.minor)
    architectures = torch.cuda.get_arch_list()
    compiled = [int(match[1]) for arch in architectures
                if (match := re.fullmatch(r"sm_([0-9]+)", arch))]
    ptx = [int(match[1]) for arch in architectures
           if (match := re.fullmatch(r"compute_([0-9]+)", arch))]
    target = capability[0] * 10 + capability[1]
    if not (any(arch // 10 == target // 10 and arch <= target for arch in compiled)
            or any(arch <= target for arch in ptx)):
        raise RankingModelUnavailable("ranking_cuda_architecture_unsupported")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    try:
        tensor = torch.ones(
            (16, 16), device=config["device"], dtype=getattr(torch, config["dtype"]))
        if (tensor @ tensor).sum().item() != 4096:
            raise RankingModelUnavailable("ranking_cuda_kernel_failed")
        torch.cuda.synchronize(index)
        del tensor
        torch.cuda.empty_cache()
    except RankingModelUnavailable:
        raise
    except Exception as exc:
        reason = _execution_error(exc)
        if reason != "ranking_cuda_out_of_memory":
            reason = "ranking_cuda_kernel_failed"
        raise RankingModelUnavailable(reason) from exc
    driver_path = Path("/proc/driver/nvidia/version")
    driver = None
    if driver_path.is_file():
        match = re.search(r"Kernel Module\s+(\d+\.\d+(?:\.\d+)?)", driver_path.read_text())
        driver = match[1] if match else None
    return {
        "cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
        "driver": driver, "device_name": properties.name,
        "device_uuid": str(properties.uuid), "capability": list(capability),
        "total_memory": properties.total_memory, "compiled_architectures": architectures,
        "attention": "eager", "tf32": False, "cudnn_benchmark": False,
        "fp16_reduced_precision_reduction": False,
        "visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "environment": _execution_environment(config["device"]),
        "runtime_packages": {
            package: LocalSemanticRanking._package_version(package) for package in (
                "nvidia-cuda-runtime-cu12", "nvidia-cublas-cu12", "nvidia-cudnn-cu12",
                "nvidia-cuda-nvrtc-cu12",
            )
        },
    }


def _device_probe_worker(connection, config):
    try:
        connection.send({"result": _cuda_identity(config)})
    except RankingModelUnavailable as exc:
        connection.send({"error": str(exc)})
    except Exception as exc:
        connection.send({"error": _execution_error(exc)})
    finally:
        connection.close()


def _probe_device(config, *, target=_device_probe_worker):
    context = multiprocessing.get_context("spawn")
    connection, child = context.Pipe()
    process = context.Process(target=target, args=(child, config), daemon=True)
    started = time.monotonic()
    try:
        check_cancelled()
        process.start()
        child.close()
        while not connection.poll(0.05):
            check_cancelled()
            if time.monotonic() - started >= min(config["timeout_seconds"], 60):
                raise RankingModelUnavailable("ranking_cuda_probe_timeout")
            if not process.is_alive():
                raise RankingModelUnavailable("ranking_cuda_probe_failed")
        check_cancelled()
        response = connection.recv()
        if "error" in response:
            raise RankingModelUnavailable(response["error"])
        return response["result"]
    except (EOFError, OSError) as exc:
        raise RankingModelUnavailable("ranking_cuda_probe_failed") from exc
    finally:
        connection.close()
        child.close()
        if process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)


def _cuda_metrics(config):
    import torch

    device = config["device"]
    free, total = torch.cuda.mem_get_info(device)
    return {
        "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
        "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(device),
        "cuda_allocated_bytes": torch.cuda.memory_allocated(device),
        "cuda_reserved_bytes": torch.cuda.memory_reserved(device),
        "cuda_free_bytes": free, "cuda_total_bytes": total,
    }


def _sha256(path: Path, *, progress_hook: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            if progress_hook is not None:
                progress_hook()
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(
    directory: str, manifest_path: str, *, progress_hook: Callable[[], None] | None = None,
) -> dict[str, str]:
    """Require an exhaustive sha256sum manifest relative to the model root.

    Symlinks and remote identifiers are deliberately unsupported. Every file,
    including tokenizer/config files, must be covered (the manifest itself is
    excluded when stored inside the directory).
    """
    if progress_hook is not None:
        progress_hook()
    root, manifest = Path(directory), Path(manifest_path)
    if not directory or not root.is_dir() or root.is_symlink() or not manifest.is_file():
        raise RankingModelUnavailable("ranking_artifact_missing")
    root, manifest = root.resolve(), manifest.resolve()
    expected: dict[str, str] = {}
    try:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, filename = line.split(maxsplit=1)
            filename = filename.removeprefix("*")
            relative = Path(filename)
            if (
                len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or relative.is_absolute() or ".." in relative.parts or filename in expected
            ):
                raise ValueError("invalid manifest")
            expected[filename] = digest
        files = {}
        for path in root.rglob("*"):
            if progress_hook is not None:
                progress_hook()
            if path.is_symlink():
                raise ValueError("symlink in model artifact")
            if path.is_file() and path.resolve() != manifest:
                files[path.relative_to(root).as_posix()] = path
        if not expected or set(files) != set(expected):
            raise ValueError("incomplete manifest")
        if not any(name.endswith(".safetensors") for name in files):
            raise ValueError("safe model weights missing")
        if "tokenizer.json" not in files or "config.json" not in files:
            raise ValueError("model tokenizer/config missing")
        for filename, path in files.items():
            if _sha256(path, progress_hook=progress_hook) != expected[filename]:
                raise ValueError("artifact hash mismatch")
    except (OSError, UnicodeError, ValueError) as exc:
        raise RankingModelUnavailable("ranking_artifact_invalid") from exc
    return {
        "manifest_sha256": _sha256(manifest, progress_hook=progress_hook),
        "tokenizer_sha256": expected["tokenizer.json"],
    }


def _worker(connection, config):
    # These variables apply only inside the isolated model worker.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    models, tokenizers = {}, {}
    cuda_ready = progress_ready = False
    try:
        while True:
            operation, inputs = connection.recv()
            try:
                if not progress_ready:
                    from tqdm import tqdm

                    # No progress state is shared across our private workers.
                    # tqdm's default multiprocessing lock otherwise leaves a
                    # named semaphore until the parent exits after forced cancel.
                    tqdm.set_lock(RLock())
                    progress_ready = True
                if _execution_environment(config["device"]) != config["frozen_environment"]:
                    raise RankingModelUnavailable("ranking_environment_changed")
                config["_request_metrics"] = {"worker_pid": os.getpid()}
                if config["device"].startswith("cuda:"):
                    if not cuda_ready:
                        if _cuda_identity(config) != config["frozen_numeric_environment"]:
                            raise RankingModelUnavailable("ranking_cuda_environment_changed")
                        cuda_ready = True
                    import torch

                    torch.cuda.reset_peak_memory_stats(config["device"])
                result, tokens = _worker_operation(operation, inputs, config, models, tokenizers)
                if cuda_ready:
                    config["_request_metrics"].update(_cuda_metrics(config))
                connection.send({"result": result, "input_tokens": tokens,
                                 "worker_metrics": config["_request_metrics"]})
            except RankingModelUnavailable as exc:
                connection.send({"error": str(exc)})
            except Exception as exc:
                # Never send source text, third-party diagnostics, or local paths.
                connection.send({"error": _execution_error(exc)})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


def _verify_worker_artifact(kind, config):
    expected = config.get("frozen_artifact_identities", {}).get(kind)
    if not expected or verify_artifact(
        config[f"{kind}_path"], config[f"{kind}_manifest_path"],
    ) != expected:
        raise RankingModelUnavailable("ranking_artifact_changed")


def _worker_operation(operation, inputs, config, models, tokenizers):
    from transformers import AutoTokenizer

    kinds = ("embedding", "reranker") if config["mode"] == "rerank" else ("embedding",)
    for kind in kinds:
        if kind not in tokenizers:
            _verify_worker_artifact(kind, config)
            tokenizers[kind] = AutoTokenizer.from_pretrained(
                config[f"{kind}_path"], local_files_only=True, trust_remote_code=False,
            )
    if operation == "count_tokens":
        return max(
            len(tokenizer(inputs, truncation=False, add_special_tokens=True)["input_ids"])
            for tokenizer in tokenizers.values()
        ), 0
    if operation == "count_tokens_batch":
        counts = [
            [len(ids) for ids in tokenizer(
                inputs, padding=False, truncation=False, add_special_tokens=True,
            )["input_ids"]]
            for tokenizer in tokenizers.values()
        ]
        if any(len(lengths) != len(inputs) for lengths in counts):
            raise RankingModelUnavailable("ranking_model_returned_incomplete_batch")
        return [max(lengths) for lengths in zip(*counts, strict=True)], 0
    kind = "embedding" if operation == "embed" else "reranker"
    if kind not in tokenizers:
        raise ValueError("reranker not enabled")
    tokenizer = tokenizers[kind]
    lengths = [
        len(tokenizer(
            *(item if operation == "score_pairs" else (item,)),
            truncation=False, add_special_tokens=True,
        )["input_ids"])
        for item in inputs
    ]
    limits = [config["max_tokens_per_pair"]]
    vocabulary_limit = getattr(tokenizer, "model_max_length", None)
    if isinstance(vocabulary_limit, int) and vocabulary_limit > 0:
        limits.append(vocabulary_limit)
    if any(length > min(limits) for length in lengths):
        raise RankingModelUnavailable("ranking_input_too_long")
    if kind not in models:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        loader = SentenceTransformer if kind == "embedding" else CrossEncoder
        # Tokenization can precede weight loading by many operations. The worker
        # must compare against the parent's frozen identity at this later boundary.
        _verify_worker_artifact(kind, config)
        started = time.monotonic()
        model_kwargs = {"torch_dtype": config.get("dtype", "float32")}
        if config.get("device", "cpu").startswith("cuda:"):
            # P100 has no FlashAttention support. Pin eager attention so a library
            # upgrade cannot silently select a different numeric implementation.
            model_kwargs["attn_implementation"] = "eager"
        models[kind] = loader(
            config[f"{kind}_path"], local_files_only=True, trust_remote_code=False,
            device=config.get("device", "cpu"), model_kwargs=model_kwargs,
        )
        config.setdefault("_request_metrics", {})["model_load_seconds"] = time.monotonic() - started
        if config.get("device", "cpu").startswith("cuda:"):
            base = getattr(models[kind], "model", models[kind])
            placements = {(str(p.device), str(p.dtype)) for p in base.parameters()}
            if placements != {(config["device"], f"torch.{config['dtype']}")}:
                raise RankingModelUnavailable("ranking_model_placement_mismatch")
    model = models[kind]
    for name in ("max_seq_length", "max_length"):
        limit = getattr(model, name, None)
        if isinstance(limit, int) and limit > 0:
            limits.append(limit)
    if any(length > min(limits) for length in lengths):
        raise RankingModelUnavailable("ranking_input_too_long")
    config.setdefault("_request_metrics", {}).update(
        actual_device=config.get("device", "cpu"), actual_dtype=config.get("dtype", "float32"),
        max_input_tokens=max(lengths, default=0), input_token_counts=lengths,
    )
    started = time.monotonic()
    if kind == "embedding":
        result = model.encode(
            inputs, batch_size=config["batch_size"], normalize_embeddings=True,
            show_progress_bar=False, convert_to_numpy=True, prompt="",
        )
    else:
        import torch

        result = model.predict(
            inputs, batch_size=config["batch_size"], show_progress_bar=False,
            activation_fn=torch.nn.Identity(), apply_softmax=False, convert_to_numpy=True,
            prompt="",
        )
    config.setdefault("_request_metrics", {})["inference_seconds"] = time.monotonic() - started
    return result.tolist(), sum(lengths)


class LocalSemanticRanking:
    """Synchronous domain port; the document worker already runs outside ASGI."""

    def __init__(self, config: dict[str, Any], *, ticket_factory=RequestTicket,
                 worker_target=_worker):
        self.config = config = _device_config(config)
        self._frozen_config = dict(config)
        self._frozen_environment = _execution_environment(config["device"])
        self._ticket_factory, self._worker_target = ticket_factory, worker_target
        self._process = self._connection = None
        self._deadline: float | None = None
        self._lock = Lock()
        self.observations: list[dict[str, Any]] = []
        identities = {}
        for kind in ("embedding", "reranker"):
            if kind == "reranker" and config["mode"] != "rerank":
                continue
            identities[kind] = verify_artifact(
                config[f"{kind}_path"], config[f"{kind}_manifest_path"],
            )
        numeric_environment = (
            _probe_device(config) if config["device"].startswith("cuda:")
            else {"attention": "default"}
        )
        self.identity = {
            "adapter": "offline-semantic-ranking-v2", "artifacts": identities,
            "device": config["device"], "dtype": config["dtype"],
            "cuda_version": config["cuda_version"] if config["device"] != "cpu" else None,
            "numeric_environment": numeric_environment,
            "normalization": "l2", "score_semantics": "raw_single_logit",
            "input_policy": "complete-no-truncation-v1",
            "execution": {key: config[key] for key in (
                "mode", "batch_size", "max_tokens_per_pair", "timeout_seconds",
            )},
            "packages": {
                package: self._package_version(package)
                for package in ("sentence-transformers", "transformers", "torch", "tokenizers",
                                "numpy", "safetensors")
            },
        }
        self.identity["execution"]["cpu_threads"] = {
            key: os.environ.get(key) for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS")
        }

    @staticmethod
    def _package_version(package):
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    def close(self, *, force: bool = False):
        # EOF lets an idle worker run Python's process finalizers (including
        # tokenizer/progress locks). Cancellation and deadlines still bypass
        # this grace period and terminate immediately.
        if not force and self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None and self._process.pid is not None:
            if not force:
                self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
        if self._connection is not None:
            self._connection.close()
        self._process = self._connection = None

    def set_deadline(self, deadline: float | None):
        """Narrow one epoch's operational deadline without changing model identity."""
        self._deadline = deadline

    def _request(self, operation, inputs):
        if self.config != self._frozen_config:
            raise RankingModelUnavailable("ranking_configuration_changed")
        if _execution_environment(self.config["device"]) != self._frozen_environment:
            self.close(force=True)
            raise RankingModelUnavailable("ranking_environment_changed")
        started, timeout = time.monotonic(), self.config["timeout_seconds"]
        if self._deadline is not None:
            timeout = min(timeout, self._deadline - started)
        if timeout <= 0:
            raise RankingModelUnavailable("ranking_timeout")
        check_cancelled()
        with self._lock, model_scope(stage=f"ranking_{operation}"):
            ticket = self._ticket_factory(
                "http://local-semantic-ranking:80", uuid4().hex, 0, timeout, capacity=1,
            )
            response, status = {}, "failed"
            heartbeat = None

            def guard():
                nonlocal heartbeat
                check_cancelled()
                stamp = time.monotonic()
                if stamp - started >= timeout:
                    raise RankingModelUnavailable("ranking_timeout")
                if heartbeat is not None and stamp - heartbeat >= HEARTBEAT_SECONDS:
                    ticket.heartbeat()
                    heartbeat = time.monotonic()

            try:
                while not ticket.admit():
                    guard()
                    time.sleep(0.05)
                ticket.start()
                heartbeat = time.monotonic()
                if self._process is None:
                    # Recheck at load time so changed bytes cannot use a frozen identity.
                    # Complete hashing can exceed the request's admission lease;
                    # renew/check controls between chunks before spawning a worker.
                    for kind, identity in self.identity["artifacts"].items():
                        if verify_artifact(self.config[f"{kind}_path"],
                                           self.config[f"{kind}_manifest_path"],
                                           progress_hook=guard) != identity:
                            raise RankingModelUnavailable("ranking_artifact_changed")
                    guard()
                    context = multiprocessing.get_context("spawn")
                    self._connection, child = context.Pipe()
                    self._process = context.Process(
                        target=self._worker_target, args=(child, {
                            **self.config, "frozen_artifact_identities": self.identity["artifacts"],
                            "frozen_numeric_environment": self.identity["numeric_environment"],
                            "frozen_environment": self._frozen_environment,
                        }), daemon=True,
                    )
                    try:
                        self._process.start()
                    finally:
                        child.close()
                guard()
                ticket.heartbeat()
                self._connection.send((operation, inputs))
                heartbeat = time.monotonic()
                while not self._connection.poll(0.05):
                    guard()
                guard()
                response = self._connection.recv()
                if time.monotonic() - started >= timeout:
                    raise RankingModelUnavailable("ranking_timeout")
                if "error" in response:
                    # The worker has finished this request and is waiting for
                    # input; a reported rejection can clean up normally.
                    self.close()
                    raise RankingModelUnavailable(response["error"])
                ticket.heartbeat()
                status = "completed"
                return response["result"]
            except BaseException:
                self.close(force=True)
                raise
            finally:
                observation = {
                    "operation": operation, "status": status,
                    "input_count": 1 if operation == "count_tokens" else len(inputs),
                    "input_tokens": response.get("input_tokens"),
                    "elapsed_seconds": time.monotonic() - started,
                    "score_semantics": "raw_single_logit" if operation == "score_pairs" else None,
                    "worker_metrics": response.get("worker_metrics", {}),
                }
                ticket.finish(status, **{k: v for k, v in observation.items() if k != "status"})
                observation.update({key: value for key, value in ticket.metrics.items()
                                    if key in ("queue_seconds", "request_seconds")})
                self.observations.append(observation)

    def count_tokens(self, text: str) -> int:
        result = self._request("count_tokens", text)
        if not isinstance(result, int) or isinstance(result, bool) or result < 0:
            raise RankingModelUnavailable("ranking_invalid_token_count")
        return result

    def count_tokens_batch(self, texts: list[str]) -> list[int]:
        # One scheduler admission/IPC per bounded batch of unique full inputs.
        unique = list(dict.fromkeys(texts))
        counts = {}
        for offset in range(0, len(unique), self.config["batch_size"]):
            batch = unique[offset:offset + self.config["batch_size"]]
            values = self._batch("count_tokens_batch", batch)
            if len(values) != len(batch) or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in values
            ):
                raise RankingModelUnavailable("ranking_invalid_token_count")
            counts.update(zip(batch, values, strict=True))
        return [counts[text] for text in texts]

    def _batch(self, operation, inputs):
        if len(inputs) > self.config["batch_size"]:
            raise RankingModelUnavailable("ranking_batch_limit")
        return self._request(operation, inputs) if inputs else []

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._batch("embed", texts)
        if len(vectors) != len(texts) or any(
            not vector or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                              or not math.isfinite(v) for v in vector)
            for vector in vectors
        ) or len({len(v) for v in vectors}) > 1:
            raise RankingModelUnavailable("ranking_invalid_vectors")
        return vectors

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = self._batch("score_pairs", pairs)
        if len(scores) != len(pairs) or any(
            not isinstance(score, (int, float)) or isinstance(score, bool)
            or not math.isfinite(score) for score in scores
        ):
            raise RankingModelUnavailable("ranking_invalid_scores")
        return scores


def configured_semantic_ranking(config: dict[str, Any] | None) -> LocalSemanticRanking | None:
    if not config or not config.get("enabled", True) or config.get("mode") == "deterministic":
        return None
    config = {"mode": "rerank", **config}
    if config["mode"] == "semantic":
        config["mode"] = "rerank"
    if config["mode"] not in ("dense", "rerank"):
        raise RankingModelUnavailable("ranking_invalid_mode")
    for key, maximum in (("batch_size", 256), ("max_tokens_per_pair", 32768),
                         ("timeout_seconds", 3600)):
        value = config.get(key)
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or not 0 < value <= maximum):
            raise RankingModelUnavailable("ranking_unbounded_configuration")
        if key != "timeout_seconds" and not isinstance(value, int):
            raise RankingModelUnavailable("ranking_invalid_configuration")
    try:
        return LocalSemanticRanking(config)
    except KeyError as exc:
        raise RankingModelUnavailable("ranking_artifact_missing") from exc


def configured_ranking_service(settings, *, config_overrides=None, policy_overrides=None):
    from app.services.extraction.ontology_guided.semantic_reranker import (
        RankingPolicy,
        RankingService,
    )

    config = {
        "enabled": settings.semantic_ranking_enabled,
        "mode": settings.semantic_ranking_mode,
        "device": settings.semantic_ranking_device,
        "dtype": settings.semantic_ranking_dtype,
        "cuda_version": settings.semantic_ranking_cuda_version,
        "embedding_path": settings.semantic_ranking_embedding_path,
        "embedding_manifest_path": settings.semantic_ranking_embedding_manifest_path,
        "reranker_path": settings.semantic_ranking_reranker_path,
        "reranker_manifest_path": settings.semantic_ranking_reranker_manifest_path,
        "batch_size": settings.semantic_ranking_batch_size,
        "max_tokens_per_pair": settings.semantic_ranking_max_tokens_per_pair,
        "timeout_seconds": settings.semantic_ranking_timeout_seconds,
    }
    config.update(config_overrides or {})
    unavailable = None
    try:
        model = configured_semantic_ranking(config)
    except RankingModelUnavailable as exc:
        model = None
        unavailable = str(exc)
    policy_config = dict(
        policy_version="semantic-ranking-v2",
        mode=(
            "semantic" if config["enabled"] and config["mode"] != "deterministic"
            else "deterministic"
        ),
        failure_policy=settings.semantic_ranking_failure_policy,
        pool_size=settings.semantic_ranking_pool_size,
        batch_size=settings.semantic_ranking_batch_size,
        max_tokens_per_pair=settings.semantic_ranking_max_tokens_per_pair,
        max_ranking_tokens_per_slot=settings.semantic_ranking_max_tokens_per_slot,
        max_ranking_tokens_per_run=settings.semantic_ranking_max_tokens_per_run,
        ranking_timeout=settings.semantic_ranking_timeout_seconds,
        technical_retry_limit=settings.semantic_ranking_retry_limit,
    )
    policy_config.update(policy_overrides or {})
    policy = RankingPolicy(**policy_config)
    return RankingService(policy=policy, model=model), {
        "configuration": config,
        "policy": policy.model_dump(mode="json"),
        "model_identity": getattr(model, "identity", None),
        "unavailable_reason": unavailable,
    }
