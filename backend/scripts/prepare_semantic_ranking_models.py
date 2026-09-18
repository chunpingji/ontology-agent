#!/usr/bin/env python3
"""Prepare pinned public ranking artifacts without changing existing model directories.

Run from backend with its CPU semantic environment. Downloads use the standard
library and a separate resumable staging area. BGE-m3's pinned official revision
only publishes PyTorch weights: conversion uses weights_only=True, never creates
a model, and verifies every converted tensor byte-for-byte. No inference or
application/database imports occur here. Runtime bundles contain safetensors only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Condition, Lock

MODELS = {
    "bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
    "bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
}
COMMON = {
    "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "sentencepiece.bpe.model",
}
EMBEDDING = {
    "modules.json", "config_sentence_transformers.json", "sentence_bert_config.json",
    "1_Pooling/config.json",
}


def emit(event, **fields):
    print(json.dumps({"at": datetime.now(UTC).isoformat(), "event": event, **fields}),
          flush=True)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def hashes(path):
    sha256 = hashlib.sha256()
    git_blob = hashlib.sha1(b"blob " + str(path.stat().st_size).encode() + b"\0")
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha256.update(chunk)
            git_blob.update(chunk)
    return sha256.hexdigest(), git_blob.hexdigest()


def verify_source(path, metadata):
    if path.is_symlink() or path.stat().st_size != metadata["size"]:
        raise ValueError("source size/symlink mismatch")
    sha256, blob = hashes(path)
    actual = sha256 if metadata.get("lfs") else blob
    expected = metadata.get("lfs", {}).get("sha256", metadata["blobId"])
    if actual != expected:
        raise ValueError("source hash differs from official repository metadata")
    return sha256


class CaptureRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.repository_etag = None
        self.repository_commit = None

    def capture(self, headers):
        value = headers.get("X-Linked-Etag") or headers.get("ETag")
        if value and self.repository_etag is None:
            self.repository_etag = value.strip('"')
        self.repository_commit = headers.get("X-Repo-Commit") or self.repository_commit

    def redirect_request(self, request, response, code, message, headers, newurl):
        self.capture(headers)
        return super().redirect_request(request, response, code, message, headers, newurl)


class DirectOfficialCDN(urllib.request.ProxyHandler):
    """Optional request-local routing; do not mutate proxy settings or Hub API routing."""

    def proxy_open(self, request, proxy, protocol):
        hostname = urllib.parse.urlsplit(request.full_url).hostname or ""
        if hostname.endswith((".cdn.hf.co", ".xethub.hf.co")):
            return None
        return super().proxy_open(request, proxy, protocol)


def http_opener(redirect, direct_cdn=False):
    if direct_cdn:
        return urllib.request.build_opener(DirectOfficialCDN(), redirect)
    return urllib.request.build_opener(redirect)


def download_ranges(partial, url, metadata, revision, name, *, timeout, attempts, connections,
                    direct_cdn=False):
    """Preserve a sequential prefix and resume bounded, independently stored ranges."""
    ranges_dir = partial.with_name(partial.name + ".ranges")
    ranges_dir.mkdir(exist_ok=True)
    plan_file = ranges_dir / "plan.json"
    expected_etag = metadata.get("lfs", {}).get("sha256", metadata["blobId"])
    if plan_file.exists():
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        if (plan["etag"] != expected_etag or plan["revision"] != revision
                or plan["total_size"] != metadata["size"]):
            raise ValueError("existing range plan differs from the frozen source")
    else:
        prefix = partial.stat().st_size if partial.exists() else 0
        plan = {"prefix": prefix, "total_size": metadata["size"], "etag": expected_etag,
                "revision": revision, "chunk_bytes": 64 * 1024 * 1024}
        write_json(plan_file, plan)
    ranges = [(start, min(start + plan["chunk_bytes"], metadata["size"]) - 1)
              for start in range(plan["prefix"], metadata["size"], plan["chunk_bytes"])]
    condition, progress_lock = Condition(), Lock()
    slots = {"active": 0, "limit": connections}
    progress = {"last": time.monotonic(), "ranges": {}}
    deadline = time.monotonic() + 3600

    def fetch_range(bounds):
        start, end = bounds
        part = ranges_dir / f"{start}-{end}.part"
        if part.is_symlink():
            raise ValueError("range part cannot be a symlink")
        expected_length = end - start + 1
        for attempt in range(1, attempts + 1):
            done = part.stat().st_size if part.exists() else 0
            with progress_lock:
                progress["ranges"][start] = done
            if done == expected_length:
                return part
            if done > expected_length:
                raise ValueError("range part exceeds its registered length")
            redirect = CaptureRedirect()
            request = urllib.request.Request(url, headers={
                "Range": f"bytes={start + done}-{end}", "Accept-Encoding": "identity",
                "User-Agent": "ontology-ranking-preparer/1",
            })
            with condition:
                condition.wait_for(lambda: slots["active"] < slots["limit"])
                slots["active"] += 1
            try:
                opener = http_opener(redirect, direct_cdn)
                with opener.open(request, timeout=timeout) as reply:
                    redirect.capture(reply.headers)
                    if (reply.status != 206 or reply.headers.get("Content-Range")
                            != f"bytes {start + done}-{end}/{metadata['size']}"):
                        raise ValueError("server did not honor the exact registered range")
                    if redirect.repository_etag != expected_etag:
                        raise ValueError("range ETag differs from official repository metadata")
                    if redirect.repository_commit not in (None, revision):
                        raise ValueError("range resolved a different commit")
                    with part.open("ab" if done else "wb") as stream:
                        while chunk := reply.read(1024 * 1024):
                            stream.write(chunk)
                            done = stream.tell()
                            if done > expected_length or time.monotonic() > deadline:
                                raise ValueError("range exceeded its size or time bound")
                            with progress_lock:
                                progress["ranges"][start] = done
                                if time.monotonic() - progress["last"] >= 15:
                                    emit("download_progress", model=name,
                                         file=metadata["rfilename"],
                                         bytes=plan["prefix"] + sum(progress["ranges"].values()),
                                         total=metadata["size"], connections=slots["limit"])
                                    progress["last"] = time.monotonic()
                        stream.flush()
                        os.fsync(stream.fileno())
                if part.stat().st_size != expected_length:
                    raise OSError("incomplete range response")
                return part
            except (OSError, urllib.error.URLError) as exc:
                with condition:
                    slots["limit"] = max(1, slots["limit"] // 2)
                emit("range_retry", model=name, file=metadata["rfilename"], attempt=attempt,
                     connections=slots["limit"], error_type=type(exc).__name__)
                if attempt == attempts or time.monotonic() > deadline:
                    raise RuntimeError(f"bounded range download failed: {name}") from None
                time.sleep(min(2 ** attempt, 20))
            finally:
                with condition:
                    slots["active"] -= 1
                    condition.notify_all()
        raise RuntimeError("range retry budget exhausted")

    with ThreadPoolExecutor(max_workers=connections) as pool:
        parts = list(pool.map(fetch_range, ranges))
    current = partial.stat().st_size if partial.exists() else 0
    if not plan["prefix"] <= current <= metadata["size"]:
        raise ValueError("sequential prefix no longer matches the download plan")
    with partial.open("ab") as target:
        for (start, end), part in zip(ranges, parts, strict=True):
            if end < current:
                continue
            with part.open("rb") as source:
                source.seek(max(0, current - start))
                shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
            current = target.tell()
        target.flush()
        os.fsync(target.fileno())
    return {"parallel_connections_initial": connections,
            "parallel_connections_final": slots["limit"],
            "resumed_prefix_bytes": plan["prefix"], "exact_ranges_validated": True,
            "direct_official_cdn": direct_cdn}


def download_file(stage, name, revision, metadata, *, timeout, attempts, connections=8,
                  direct_cdn=False):
    """Resume only verified immutable inputs; partial transfers never enter runtime."""
    filename = metadata["rfilename"]
    target = stage / "sources" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = target.with_name(target.name + ".download.json")
    partial = target.with_name(target.name + ".part")
    if target.exists():
        sha256 = verify_source(target, metadata)
        if not receipt.is_file():
            raise ValueError("verified source lacks its download receipt")
        result = json.loads(receipt.read_text(encoding="utf-8"))
        if result["sha256"] != sha256 or result["revision"] != revision:
            raise ValueError("existing source receipt mismatch")
        return result
    if partial.is_symlink():
        raise ValueError("partial download cannot be a symlink")
    url = f"https://huggingface.co/BAAI/{name}/resolve/{revision}/{filename}?download=true"
    expected_etag = metadata.get("lfs", {}).get("sha256", metadata["blobId"])
    deadline = time.monotonic() + 3600
    received_etag = None
    transfer = None
    if metadata["size"] >= 128 * 1024 * 1024 and connections > 1:
        transfer = download_ranges(
            partial, url, metadata, revision, name, timeout=timeout,
            attempts=attempts, connections=connections, direct_cdn=direct_cdn,
        )
        received_etag = expected_etag
    for attempt in range(1, attempts + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == metadata["size"]:
            break
        if offset > metadata["size"]:
            raise ValueError("partial download exceeds the official file size")
        headers = {"Accept-Encoding": "identity", "User-Agent": "ontology-ranking-preparer/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        redirect = CaptureRedirect()
        opener = http_opener(redirect, direct_cdn)
        started = last_report = time.monotonic()
        try:
            request = urllib.request.Request(url, headers=headers)
            with opener.open(request, timeout=timeout) as reply:
                redirect.capture(reply.headers)
                if redirect.repository_etag != expected_etag:
                    raise ValueError("download ETag differs from the official repository blob")
                if redirect.repository_commit not in (None, revision):
                    raise ValueError("download resolved a different repository commit")
                received_etag = redirect.repository_etag
                if offset and reply.status != 206:
                    raise ValueError("server did not honor the download resume range")
                if offset and not reply.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise ValueError("server returned an unexpected resume range")
                with partial.open("ab" if offset else "wb") as stream:
                    while chunk := reply.read(1024 * 1024):
                        stream.write(chunk)
                        current = stream.tell()
                        if current > metadata["size"] or time.monotonic() > deadline:
                            raise ValueError("download exceeded its size or one-hour time limit")
                        if time.monotonic() - last_report >= 10:
                            emit("download_progress", model=name, file=filename, bytes=current,
                                 total=metadata["size"], attempt=attempt)
                            last_report = time.monotonic()
                    stream.flush()
                    os.fsync(stream.fileno())
            if partial.stat().st_size != metadata["size"]:
                raise OSError("incomplete HTTP response")
            break
        except (OSError, urllib.error.URLError) as exc:
            emit("download_retry", model=name, file=filename, attempt=attempt,
                 error_type=type(exc).__name__, elapsed=round(time.monotonic() - started, 2))
            if attempt == attempts or time.monotonic() > deadline:
                raise RuntimeError(f"bounded download failed: {name}/{filename}") from None
            time.sleep(min(attempt, 5))
    sha256 = verify_source(partial, metadata)
    result = {
        "source_url": url, "revision": revision, "path": filename,
        "size": metadata["size"], "sha256": sha256, "git_blob_id": metadata["blobId"],
        "lfs_sha256": metadata.get("lfs", {}).get("sha256"),
        "repository_etag": received_etag or expected_etag,
        "verified_against": "official_revision_file_metadata_and_repository_etag",
        "parallel_transfer": transfer,
    }
    # A crash after transferring all bytes may leave a full .part. Its size and
    # official content hash are rechecked before promotion on the next invocation.
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        if any(previous[key] != result[key] for key in ("sha256", "revision", "path", "size")):
            raise ValueError("existing transfer receipt differs from the completed source")
    else:
        write_json(receipt, result)
    partial.rename(target)
    emit("source_verified", model=name, file=filename, bytes=metadata["size"], sha256=sha256)
    return result


def convert_embedding(source, target):
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file

    torch.set_num_threads(2)
    tensors = torch.load(source, map_location="cpu", weights_only=True, mmap=True)
    if not isinstance(tensors, dict) or not tensors or any(
        not isinstance(key, str) or not isinstance(value, torch.Tensor)
        for key, value in tensors.items()
    ):
        raise ValueError("official weights are not a plain tensor state dictionary")
    tensors = {key: value.contiguous() for key, value in tensors.items()}
    save_file(tensors, target, metadata={"format": "pt"})
    with safe_open(target, framework="pt", device="cpu") as converted:
        if set(converted.keys()) != set(tensors):
            raise ValueError("converted tensor keys differ from the official source")
        for key, expected in tensors.items():
            actual = converted.get_tensor(key)
            if (actual.shape != expected.shape or actual.dtype != expected.dtype
                    or not torch.equal(actual.reshape(-1).view(torch.uint8),
                                       expected.reshape(-1).view(torch.uint8))):
                raise ValueError("converted tensor differs from the official source")
    return {
        "operation": "torch_weights_only_to_safetensors",
        "input": "pytorch_model.bin", "output": "model.safetensors",
        "tensor_count": len(tensors), "all_tensor_bytes_equal": True,
        "model_instantiated": False, "inference_performed": False,
        "packages": {name: importlib.metadata.version(name) for name in ("torch", "safetensors")},
    }


def prepare(name, args):
    revision = MODELS[name]
    parent = args.output_root / name
    final = parent / revision
    manifest = parent / f"{revision}.sha256"
    provenance = parent / f"{revision}.provenance.json"
    model_card = parent / f"{revision}.MODEL_CARD.md"
    if any(path.exists() for path in (final, manifest, provenance, model_card)):
        raise FileExistsError(f"existing deployment will not be overwritten: {final}")
    stage = args.output_root / ".downloads" / name / revision
    stage.mkdir(parents=True, exist_ok=True)
    source_index = stage / "official-model-info.json"
    api_url = f"https://huggingface.co/api/models/BAAI/{name}/revision/{revision}?blobs=true"
    if source_index.exists():
        info = json.loads(source_index.read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(api_url, timeout=args.http_timeout) as reply:
            info = json.load(reply)
        write_json(source_index, info)
    if info["sha"] != revision or info["id"] != f"BAAI/{name}":
        raise ValueError("official source index resolved a different model revision")
    selected = COMMON | {"README.md"}
    selected |= EMBEDDING | {"pytorch_model.bin"} if name == "bge-m3" else {"model.safetensors"}
    entries = {item["rfilename"]: item for item in info["siblings"]}
    if not selected.issubset(entries):
        raise ValueError("pinned repository lacks a required deployment file")
    # Small configuration/tokenizer files precede the weight transfer.
    receipts = [download_file(stage, name, revision, entries[filename],
                              timeout=args.http_timeout, attempts=args.attempts,
                              connections=args.connections, direct_cdn=args.direct_cdn)
                for filename in sorted(selected, key=lambda key: entries[key]["size"])]
    if args.download_only:
        emit("download_complete", model=name, stage=str(stage), files=len(receipts))
        return
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle-", dir=stage) as temporary:
        bundle = Path(temporary)
        for filename in sorted(selected - {"README.md", "pytorch_model.bin"}):
            target = bundle / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stage / "sources" / filename, target)
            verify_source(target, entries[filename])
        conversion = None
        if name == "bge-m3":
            conversion = convert_embedding(stage / "sources" / "pytorch_model.bin",
                                           bundle / "model.safetensors")
        deployed = {path.relative_to(bundle).as_posix(): {
            "sha256": hashes(path)[0], "size": path.stat().st_size,
        } for path in sorted(bundle.rglob("*")) if path.is_file()}
        final.mkdir(exist_ok=False)
        for child in bundle.iterdir():
            shutil.move(str(child), final / child.name)
    with manifest.open("x", encoding="utf-8") as stream:
        stream.writelines(f"{item['sha256']}  {path}\n" for path, item in deployed.items())
    with model_card.open("xb") as stream:
        stream.write((stage / "sources" / "README.md").read_bytes())
    write_json(provenance, {
        "schema_version": "semantic-ranking-artifact-provenance-v1",
        "prepared_at": datetime.now(UTC).isoformat(), "repository": f"BAAI/{name}",
        "revision": revision, "official_api_url": api_url, "official_source_index": info,
        "downloaded_files": receipts, "runtime_files": deployed, "conversion": conversion,
        "manifest_sha256": hashes(manifest)[0], "model_card_sha256": hashes(model_card)[0],
        "preparation_script_sha256": hashes(Path(__file__))[0],
        "runtime_directory": str(final), "manifest_path": str(manifest),
        "download_cache_in_runtime": False, "inference_performed": False,
    })
    emit("artifact_ready", model=name, runtime_directory=str(final), manifest=str(manifest),
         provenance=str(provenance), runtime_files=len(deployed),
         runtime_bytes=sum(item["size"] for item in deployed.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=[*MODELS, "both"], default="both")
    parser.add_argument("--output-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "models/semantic-ranking")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--http-timeout", type=int, choices=range(1, 121), default=60,
                        metavar="SECONDS")
    parser.add_argument("--attempts", type=int, choices=range(1, 9), default=5)
    parser.add_argument("--connections", type=int, choices=range(1, 9), default=8,
                        help="Maximum range connections per model; failures reduce concurrency")
    parser.add_argument("--direct-cdn", action="store_true",
                        help="Bypass environment proxies for official CDN requests only")
    args = parser.parse_args()
    args.output_root = args.output_root.resolve()
    selected = list(MODELS) if args.model == "both" else [args.model]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda name: prepare(name, args), selected))


if __name__ == "__main__":
    main()
