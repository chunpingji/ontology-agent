"""Durable task deltas with periodic atomic snapshots and torn-tail recovery."""

import json
import os
import tempfile
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from time import monotonic

from app.services.extraction.performance import timed

MAPS = {"completed", "failures", "joint_completed", "task_outcomes"}


def journal_path(path):
    return Path(str(path) + ".jsonl")


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@timed("atomic_json_write")
def write_snapshot(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
            os.replace(temporary, path)
            # Persist the directory entry as well as the file's contents.
            _sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
    # An old journal is harmless if a crash occurs before unlink: base hashes
    # prevent its deltas being applied to the newly installed snapshot.
    journal_path(path).unlink(missing_ok=True)
    return sha256(encoded).hexdigest()


def read_checkpoint(path):
    if not path.is_file():
        return None
    raw = path.read_bytes()
    value = json.loads(raw)
    base = sha256(raw).hexdigest()
    log = journal_path(path)
    if not log.is_file():
        return value
    lines = log.read_bytes().splitlines(keepends=True)
    sequence = 0
    for line in lines:
        if not line.endswith(b"\n"):
            break  # only a torn final append may be ignored
        record = json.loads(line)
        if record["base"] != base:
            continue
        if record["sequence"] != sequence + 1:
            raise ValueError("checkpoint journal sequence mismatch")
        sequence += 1
        value.update(record["metadata"])
        for name, changes in record["maps"].items():
            target = value.setdefault(name, {})
            for key in changes["removed"]:
                target.pop(key, None)
            target.update(changes["set"])
        ids = value.get("candidate_ids", [])
        removed = set(record["candidate_ids"]["removed"])
        value["candidate_ids"] = [key for key in ids if key not in removed]
        value["candidate_ids"].extend(record["candidate_ids"]["added"])
    return value


class CheckpointJournal:
    def __init__(self, path, *, every=32, seconds=30):
        self.path, self.every, self.seconds = path, every, seconds
        self.previous = None
        self.base = None
        self.sequence = 0
        self.started = monotonic()

    @timed("checkpoint_write")
    def save(self, value, *, force=False):
        if (
            self.previous is None
            or force
            or self.sequence >= self.every
            or monotonic() - self.started >= self.seconds
        ):
            self.base = write_snapshot(self.path, value)
            self.previous = deepcopy(value)
            self.sequence = 0
            self.started = monotonic()
            return
        changes = {}
        for name in MAPS:
            before, after = self.previous.get(name, {}), value.get(name, {})
            changes[name] = {
                "removed": sorted(before.keys() - after.keys()),
                "set": {
                    key: val
                    for key, val in after.items()
                    if key not in before or val != before[key]
                },
            }
        before_ids = set(self.previous.get("candidate_ids", []))
        after_ids = set(value.get("candidate_ids", []))
        metadata = {key: val for key, val in value.items() if key not in MAPS | {"candidate_ids"}}
        record = {
            "base": self.base,
            "sequence": self.sequence + 1,
            "metadata": metadata,
            "maps": changes,
            "candidate_ids": {
                "added": [key for key in value.get("candidate_ids", []) if key not in before_ids],
                "removed": sorted(before_ids - after_ids),
            },
        }
        encoded = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
        path = journal_path(self.path)
        created = not path.exists()
        with path.open("ab") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        if created:
            _sync_directory(path.parent)
        self.sequence += 1
        self.previous.update(deepcopy(metadata))
        for name, delta in changes.items():
            target = self.previous.setdefault(name, {})
            for key in delta["removed"]:
                target.pop(key, None)
            target.update(deepcopy(delta["set"]))
        self.previous["candidate_ids"] = list(value.get("candidate_ids", []))
