"""Safe local file output for downloaded ``.docx`` reports.

Hardening: server-supplied filenames are reduced to a basename (no path
traversal); writes are atomic (temp + ``os.replace``); an existing target is
never clobbered unless ``force`` is set.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from cli_anything.ontology_agent.core.errors import UsageError


def sanitize_filename(name: str | None) -> str | None:
    """Reduce a (possibly server-supplied) name to a safe basename, or None."""
    if not name:
        return None
    base = os.path.basename(name.replace("\\", "/")).strip().strip(".")
    return base or None


def resolve_output_path(output: str | None, server_filename: str | None, fallback: str) -> Path:
    """Decide where to write.

    * no ``-o`` → ``<cwd>/<server_filename or fallback>``
    * ``-o`` is an existing dir or ends with a separator → treat as directory
    * ``-o`` otherwise → use verbatim as the file path
    """
    name = sanitize_filename(server_filename) or fallback
    if output is None:
        return Path.cwd() / name
    p = Path(output)
    if str(output).endswith((os.sep, "/")) or p.is_dir():
        return p / name
    return p


def write_atomic(content: bytes, path: Path, *, force: bool = False) -> str:
    """Atomically write *content* to *path*; return the SHA-256 hex digest.

    Refuses to overwrite an existing file unless ``force``.
    """
    if path.exists() and not force:
        raise UsageError(f"文件已存在：{path}（加 --force 覆盖）")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return hashlib.sha256(content).hexdigest()
