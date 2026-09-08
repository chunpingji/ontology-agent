"""Run-scoped file storage for document-analysis source artifacts.

Only this module turns a persisted storage reference into a filesystem path.
Public callers provide a run id and, for evidence navigation, an opaque
``selection_ref``; they never provide a path.  Staging and finalisation are
separate so the application service can remove an orphan if the database
transaction fails.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

from fastapi import UploadFile

_SUPPORTED_SUFFIXES = {".doc", ".docx"}
_DOC_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_DOCX_REQUIRED_MEMBERS = {"[Content_Types].xml", "word/document.xml"}
_SAFE_NAME = re.compile(r"[^\w.()\[\] -]+", re.UNICODE)
_CHUNK_SIZE = 1024 * 1024


class SourceArtifactError(ValueError):
    """Stable validation failure raised before a run is accepted."""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StagedSource:
    run_id: UUID
    filename: str
    suffix: str
    media_type: str
    size_bytes: int
    document_hash: str
    storage_uri: str
    path: Path


def safe_basename(value: str | None) -> str:
    """Return a display-safe basename independent of the server platform."""

    raw = (value or "").replace("\\", "/")
    basename = PurePosixPath(raw).name.strip().replace("\x00", "")
    basename = "".join(char for char in basename if char.isprintable())
    basename = _SAFE_NAME.sub("_", basename).strip(" .")
    if not basename:
        raise SourceArtifactError("INVALID_REQUEST", "文件名不能为空", status_code=400)
    if len(basename) > 240:
        stem = Path(basename).stem[:200]
        basename = f"{stem}{Path(basename).suffix.lower()}"
    return basename


def _validate_docx(path: Path, max_upload_bytes: int) -> None:
    if not zipfile.is_zipfile(path):
        raise SourceArtifactError(
            "UNSUPPORTED_SOURCE_TYPE",
            "文件扩展名为 .docx，但内容不是有效的 Word OOXML 文件",
            status_code=415,
        )
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not _DOCX_REQUIRED_MEMBERS.issubset(names):
                raise SourceArtifactError(
                    "UNSUPPORTED_SOURCE_TYPE",
                    "上传内容缺少 Word 文档必需结构",
                    status_code=415,
                )
            # Parsing a small compressed upload must not expand without bound.
            expanded = sum(item.file_size for item in archive.infolist())
            if expanded > max(max_upload_bytes * 20, 100 * 1024 * 1024):
                raise SourceArtifactError(
                    "SOURCE_TOO_LARGE",
                    "Word 文档解压后的内容超过安全上限",
                    status_code=413,
                )
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceArtifactError("INVALID_WORD", "Word 文档结构损坏", status_code=422) from exc


def _validate_actual_type(path: Path, suffix: str, max_upload_bytes: int) -> str:
    if suffix == ".docx":
        _validate_docx(path, max_upload_bytes)
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with path.open("rb") as stream:
        magic = stream.read(len(_DOC_MAGIC))
    if magic != _DOC_MAGIC:
        raise SourceArtifactError(
            "UNSUPPORTED_SOURCE_TYPE",
            "文件扩展名为 .doc，但内容不是 Word 97-2003 复合文档",
            status_code=415,
        )
    return "application/msword"


async def stage_upload(
    upload: UploadFile,
    *,
    storage_root: Path,
    run_id: UUID,
    max_upload_bytes: int,
) -> StagedSource:
    """Stream, hash and validate one upload into its future run directory."""

    filename = safe_basename(upload.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise SourceArtifactError(
            "UNSUPPORTED_SOURCE_TYPE", "仅支持 .doc 或 .docx 文件", status_code=415
        )
    if max_upload_bytes <= 0:
        raise ValueError("max_upload_bytes must be positive")

    run_dir = storage_root.resolve() / str(run_id)
    source_dir = run_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=False)
    descriptor, staged_name = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=source_dir)
    staged_path = Path(staged_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while True:
                chunk = await upload.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_upload_bytes:
                    raise SourceArtifactError(
                        "SOURCE_TOO_LARGE",
                        f"上传文件超过 {max_upload_bytes} 字节上限",
                        status_code=413,
                    )
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if size == 0:
            raise SourceArtifactError("EMPTY_SOURCE", "上传的 Word 文件为空", status_code=422)
        media_type = _validate_actual_type(staged_path, suffix, max_upload_bytes)
        final_path = source_dir / f"original{suffix}"
        os.replace(staged_path, final_path)
        relative = final_path.relative_to(storage_root.resolve()).as_posix()
        return StagedSource(
            run_id=run_id,
            filename=filename,
            suffix=suffix,
            media_type=media_type,
            size_bytes=size,
            document_hash=digest.hexdigest(),
            storage_uri=relative,
            path=final_path,
        )
    except Exception:
        staged_path.unlink(missing_ok=True)
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    finally:
        await upload.close()


class RunArtifactStorage:
    """Resolve and clean only paths nested under a concrete run directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, run_id: UUID | str, storage_uri: str) -> Path:
        relative = Path(storage_uri)
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceArtifactError("INVALID_REQUEST", "非法 artifact 存储引用", status_code=400)
        expected_root = (self.root / str(run_id)).resolve()
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(expected_root)
        except ValueError as exc:
            raise SourceArtifactError(
                "INVALID_REQUEST", "artifact 不属于当前运行", status_code=400
            ) from exc
        return candidate

    def open(self, run_id: UUID | str, storage_uri: str) -> BinaryIO:
        path = self.resolve(run_id, storage_uri)
        if not path.is_file():
            raise SourceArtifactError("RUN_EXPIRED", "运行原文已清理", status_code=410)
        return path.open("rb")

    def discard_run(self, run_id: UUID | str) -> None:
        """Remove only one explicitly resolved run directory."""

        run_dir = (self.root / str(run_id)).resolve()
        if run_dir.parent != self.root or run_dir == self.root:
            raise ValueError("refusing to delete a broad storage path")
        shutil.rmtree(run_dir, ignore_errors=True)
