"""Deployment preparation preserves official bytes and interrupted transfers."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def preparer():
    path = Path(__file__).resolve().parents[2] / "scripts/prepare_semantic_ranking_models.py"
    spec = importlib.util.spec_from_file_location("semantic_artifact_preparer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata(data):
    return {
        "rfilename": "model.safetensors", "size": len(data), "blobId": "unused-lfs-pointer",
        "lfs": {"sha256": hashlib.sha256(data).hexdigest()},
    }


def test_modified_source_cannot_pass_official_hash_verification(tmp_path, preparer):
    path = tmp_path / "model.safetensors"
    original = b"official model bytes"
    path.write_bytes(original)
    assert preparer.verify_source(path, metadata(original)) == hashlib.sha256(original).hexdigest()
    path.write_bytes(b"changed! model bytes")
    with pytest.raises(ValueError, match="official repository metadata"):
        preparer.verify_source(path, metadata(original))


def test_range_resume_keeps_existing_prefix_and_partial_chunk(tmp_path, preparer, monkeypatch):
    data = b"0123456789abcdef"
    source = metadata(data)
    partial = tmp_path / "weights.part"
    partial.write_bytes(data[:2])
    ranges_dir = tmp_path / "weights.part.ranges"
    ranges_dir.mkdir()
    (ranges_dir / "plan.json").write_text(json.dumps({
        "prefix": 2, "total_size": len(data), "etag": source["lfs"]["sha256"],
        "revision": "a" * 40, "chunk_bytes": 4,
    }))
    (ranges_dir / "2-5.part").write_bytes(data[2:3])
    requests = []

    class Opener:
        def open(self, request, timeout):
            interval = request.get_header("Range").removeprefix("bytes=")
            start, end = map(int, interval.split("-"))
            requests.append((start, end))
            reply = io.BytesIO(data[start:end + 1])
            reply.status = 206
            reply.headers = {"ETag": source["lfs"]["sha256"],
                             "Content-Range": f"bytes {start}-{end}/{len(data)}"}
            return reply

    monkeypatch.setattr(preparer.urllib.request, "build_opener", lambda redirect: Opener())
    transfer = preparer.download_ranges(partial, "https://example.invalid", source, "a" * 40,
                                        "fixture", timeout=1, attempts=1, connections=2)
    assert partial.read_bytes() == data
    assert sorted(requests) == [(3, 5), (6, 9), (10, 13), (14, 15)]
    assert transfer["resumed_prefix_bytes"] == 2
    assert preparer.verify_source(partial, source) == source["lfs"]["sha256"]


def test_ignored_http_range_cannot_corrupt_an_existing_prefix(tmp_path, preparer, monkeypatch):
    data = b"0123456789"
    partial = tmp_path / "weights.part"
    partial.write_bytes(data[:2])

    class Opener:
        def open(self, request, timeout):
            reply = io.BytesIO(data)
            reply.status = 200
            reply.headers = {"ETag": metadata(data)["lfs"]["sha256"]}
            return reply

    monkeypatch.setattr(preparer.urllib.request, "build_opener", lambda redirect: Opener())
    with pytest.raises(ValueError, match="exact registered range"):
        preparer.download_ranges(partial, "https://example.invalid", metadata(data), "a" * 40,
                                  "fixture", timeout=1, attempts=1, connections=2)
    assert partial.read_bytes() == data[:2]


def test_existing_deployment_is_refused_before_any_network_request(tmp_path, preparer, monkeypatch):
    final = tmp_path / "bge-m3" / preparer.MODELS["bge-m3"]
    final.mkdir(parents=True)
    (final / "keep.txt").write_text("unchanged")

    def forbidden(*args, **kwargs):
        raise AssertionError("existing deployment must not trigger network work")

    monkeypatch.setattr(preparer.urllib.request, "urlopen", forbidden)
    with pytest.raises(FileExistsError, match="will not be overwritten"):
        preparer.prepare("bge-m3", SimpleNamespace(output_root=tmp_path))
    assert (final / "keep.txt").read_text() == "unchanged"


def test_cpu_conversion_keeps_tensor_bytes_without_instantiating_a_model(tmp_path, preparer):
    torch = pytest.importorskip("torch")
    safetensors = pytest.importorskip("safetensors")
    source, target = tmp_path / "official.bin", tmp_path / "model.safetensors"
    tensors = {"weight": torch.arange(12, dtype=torch.float32).reshape(3, 4).T,
               "counter": torch.tensor(4, dtype=torch.int64)}
    torch.save(tensors, source)
    result = preparer.convert_embedding(source, target)
    assert result["all_tensor_bytes_equal"] is True
    assert result["model_instantiated"] is result["inference_performed"] is False
    with safetensors.safe_open(target, framework="pt", device="cpu") as converted:
        assert converted.metadata() == {"format": "pt"}
        assert torch.equal(converted.get_tensor("weight"), tensors["weight"])
