#!/usr/bin/env python3
"""Build a read-only, hash-frozen inventory for retiring legacy Word recognition.

The inventory is intentionally conservative.  A job is in the physical-cleanup
scope only when it carries the exact legacy marker ``source_type=word`` and
``source_config.mode=auto``.  A Word job without an understood ownership marker
is reported as unknown; it is never inferred to be disposable merely from its
file extension or ``source_type``.

This module only reads the database and source/cache files.  The sole write made
by the CLI is the requested JSON manifest.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import (
    MetaData,
    String,
    and_,
    cast,
    create_engine,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.sql.schema import Table

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
MANIFEST_SCHEMA = "word-recognition-retirement-manifest-v1"
STATIC_AUDIT_SCHEMA = "word-recognition-static-audit-v1"
LEGACY_SOURCE_PATTERN = "extraction_jobs:source_type=word;source_config.mode=auto"

# T018: this is an exact review list, not a directory-wide ignore.  Adding a new
# call edge to the retired runner/candidate domain therefore fails closed until
# its ownership guard and legitimate shared purpose are reviewed explicitly.
_LEGACY_CALL_KINDS = {
    "GenericExtractionRunner": "legacy_runner",
    "configured_generic_runner": "legacy_runner",
    "_compute_annotation": "legacy_runner",
    "_enqueue_annotation": "legacy_runner",
    "_annotation_worker": "legacy_runner",
    "_load_annotation_checkpoint": "legacy_checkpoint",
    "_persist_evidence_payload": "legacy_candidate_write",
    "persist_validated": "legacy_candidate_write",
}
_ALLOWED_LEGACY_CALL_EDGES = {
    ("backend/app/api/ast_templates.py", "upload_default_source", "_enqueue_annotation"),
    ("backend/app/api/evidence.py", "extract_evidence", "_enqueue_annotation"),
    ("backend/app/api/evidence.py", "fill_evidence_gaps", "configured_generic_runner"),
    ("backend/app/api/evidence.py", "fill_evidence_gaps", "persist_validated"),
    ("backend/app/api/evidence.py", "create_candidate", "persist_validated"),
    ("backend/app/api/extraction.py", "_compute_annotation", "configured_generic_runner"),
    ("backend/app/api/extraction.py", "get_annotated_document", "_compute_annotation"),
    ("backend/app/api/extraction.py", "_persist_evidence_payload", "persist_validated"),
    ("backend/app/api/extraction.py", "worker", "_annotation_worker"),
    ("backend/app/api/extraction.py", "on_snapshot", "_persist_evidence_payload"),
    ("backend/app/api/extraction.py", "_annotation_worker", "_compute_annotation"),
    ("backend/app/api/extraction.py", "_annotation_worker", "_load_annotation_checkpoint"),
    ("backend/app/api/extraction.py", "_annotation_worker", "_persist_evidence_payload"),
    ("backend/app/api/extraction.py", "_claim_annotation", "_load_annotation_checkpoint"),
    ("backend/app/api/extraction.py", "job_progress", "_load_annotation_checkpoint"),
    ("backend/app/api/extraction.py", "resume_annotation", "_enqueue_annotation"),
    ("backend/app/api/extraction.py", "rerun_annotation", "_enqueue_annotation"),
    (
        "backend/app/services/extraction/local_semantic_model.py",
        "configured_generic_runner",
        "GenericExtractionRunner",
    ),
    (
        "backend/app/services/extraction/relation_extractor.py",
        "extract_relationships",
        "configured_generic_runner",
    ),
}
_REQUIRED_GUARD_CALLS = {
    (
        "backend/app/api/extraction.py",
        "_create_declarative_job",
        "_reject_retired_word_recognition",
    ),
    ("backend/app/api/extraction.py", "create_job", "_reject_retired_word_recognition"),
    ("backend/app/api/extraction.py", "create_auto_job", "_reject_retired_word_recognition"),
    ("backend/app/api/extraction.py", "start_extraction_job", "_reject_retired_word_recognition"),
    ("backend/app/api/extraction.py", "_run_pipeline_bg", "_word_job_allows_annotation"),
    ("backend/app/api/extraction.py", "get_annotated_document", "_require_preview_capability"),
    ("backend/app/api/extraction.py", "_claim_annotation", "_require_annotation_capability"),
    ("backend/app/api/extraction.py", "worker", "_word_job_allows_annotation"),
    ("backend/app/api/extraction.py", "_annotation_worker", "_word_job_allows_annotation"),
    ("backend/app/api/extraction.py", "job_progress", "_require_preview_capability"),
    ("backend/app/api/extraction.py", "pause_annotation", "_require_annotation_capability"),
    ("backend/app/api/extraction.py", "list_candidates", "_require_result_capability"),
    ("backend/app/api/extraction.py", "generate_risk_report", "_require_result_capability"),
    ("backend/app/api/evidence.py", "_job", "_require_result_capability"),
    ("backend/app/api/ast_templates.py", "suggest_slots_endpoint", "_require_preview_capability"),
    ("backend/app/api/pde_conflict.py", "_require_job", "_require_result_capability"),
    ("backend/app/api/report_runs.py", "require_source_bindings", "_require_result_capability"),
    (
        "backend/app/services/reporting/report_run_service.py",
        "start",
        "_require_report_source",
    ),
    ("backend/app/services/fact_commit.py", "recover_pending", "_recoverable_commit_source"),
}
_CANDIDATE_STORE_IMPORT_ALLOWLIST = {
    "backend/app/api/evidence.py",
    "backend/app/api/extraction.py",
    "backend/app/services/extraction/gap_workflow.py",
    "backend/app/services/fact_commit.py",
    "backend/app/services/reasoning/calculation_review.py",
    "backend/app/services/reasoning/rule_service.py",
    "backend/app/services/reporting/coverage_v2.py",
    "backend/app/services/reporting/report_run_service.py",
}
_NEW_RUN_ROOTS = (
    "backend/app/api/document_analysis.py",
    "backend/app/services/document_analysis/",
    "backend/app/services/extraction/ontology_guided/",
)
_NEW_RUN_FORBIDDEN_SYMBOLS = {
    "CandidateStore",
    "EvidenceJobState",
    "ExtractionJob",
    "FactCommitService",
    "GenericExtractionRunner",
    "configured_generic_runner",
    "_annotation_worker",
    "_compute_annotation",
    "_enqueue_annotation",
    "_load_annotation_checkpoint",
}
_DUAL_TRACK_PATTERNS = (
    re.compile(r"\blegacy_runner\b", re.IGNORECASE),
    re.compile(r"\blegacy_mode\b", re.IGNORECASE),
    re.compile(r"\buse_legacy\b", re.IGNORECASE),
    re.compile(r"\brunner_variant\b", re.IGNORECASE),
    re.compile(r"\blegacy_checkpoint\b", re.IGNORECASE),
)

# Tables whose rows are runtime products owned by one legacy recognition job.
# Everything else is treated as a consumer/protected domain, even when it has a
# foreign key to extraction_jobs.
OWNED_DIRECT_TABLES = {
    "annotation_executions",
    "evidence_job_states",
    "evidence_candidates",
    "evidence_commits",
    "evidence_assertions",
    "evidence_snapshots",
    "evidence_coverage",
    "extraction_candidates",
    "slot_dismissals",
    "local_model_requests",
}
OWNED_DESCENDANT_TABLES = {
    "evidence_candidate_revisions",
    "evidence_reviews",
}
OWNED_TABLES = (
    OWNED_DIRECT_TABLES
    | OWNED_DESCENDANT_TABLES
    | {
        "document_analyses",
    }
)

# These records are append-only, human decisions, publication products, or
# shared business outputs.  They are references to preserve, never cleanup rows.
PROTECTED_TABLES = {
    "audit_log",
    "ast_templates",
    "ast_template_training_pairs",
    "calculation_decisions",
    "conditional_assertion_bindings",
    "generated_reports",
    "pde_conflict_decisions",
    "report_artifacts",
    "report_bodies",
    "report_content_reviews",
    "report_content_versions",
    "report_input_snapshots",
    "report_output_results",
    "report_runs",
    "report_signature_events",
    "report_signature_snapshots",
    "report_signatures",
    "report_signing_envelopes",
    "report_signing_sessions",
}
IMMUTABLE_TABLES = {
    "audit_log",
    "calculation_decisions",
    "conditional_assertion_bindings",
    "evidence_assertions",
    "evidence_candidate_revisions",
    "evidence_reviews",
    "evidence_snapshots",
    "report_artifacts",
    "report_bodies",
    "report_content_reviews",
    "report_content_versions",
    "report_input_snapshots",
    "report_output_results",
    "report_signature_events",
    "report_signature_snapshots",
    "report_signatures",
    "report_signing_envelopes",
}
ACTIVE_EXECUTION_STATES = {"queued", "running", "applying", "claimed", "in_progress"}
CONFIRMED_STATES = {"approved", "confirmed"}
PUBLISHED_COMMIT_STATES = {"succeeded", "published", "committed"}
GENESIS_AUDIT_HASH = "0" * 64


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    stamp = value or _utc_now()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return _iso(value) if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, Path):
        return str(value)
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _python_static_facts(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    calls: list[tuple[str, str, int]] = []
    imports: list[tuple[str, int]] = []
    symbols: list[tuple[str, int]] = []
    scopes: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            scopes.append(node.name)
            self.generic_visit(node)
            scopes.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_Call(self, node: ast.Call) -> None:
            name = _call_name(node.func)
            if name:
                calls.append((scopes[-1] if scopes else "<module>", name, node.lineno))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module:
                imports.append((node.module, node.lineno))
            symbols.extend((alias.name, node.lineno) for alias in node.names)
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            imports.extend((alias.name, node.lineno) for alias in node.names)
            symbols.extend((alias.name.rsplit(".", 1)[-1], node.lineno) for alias in node.names)
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            symbols.append((node.id, node.lineno))
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            symbols.append((node.attr, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return {"calls": calls, "imports": imports, "symbols": symbols}


def audit_online_retirement(project_root: Path | str = ROOT) -> dict[str, Any]:
    """Statically prove that online code has no unreviewed legacy/dual-track edge.

    Offline evaluation is deliberately excluded by one exact path prefix.  All
    online legacy calls that remain for Excel or ``template_default`` are
    reported and checked against an exact function-level allowlist.
    """
    root = Path(project_root).expanduser().resolve()
    source_roots = (root / "backend" / "app", root / "frontend" / "src")
    suffixes = {".py", ".js", ".jsx", ".ts", ".tsx"}
    files: list[Path] = []
    violations: list[dict[str, Any]] = []
    observed_edges: list[dict[str, Any]] = []
    call_index: set[tuple[str, str, str]] = set()
    source_hashes: dict[str, str] = {}

    for source_root in source_roots:
        if not source_root.is_dir():
            violations.append(
                {
                    "code": "ONLINE_SOURCE_ROOT_MISSING",
                    "path": source_root.relative_to(root).as_posix(),
                    "line": 0,
                }
            )
            continue
        files.extend(
            path
            for path in source_root.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        )

    for path in sorted(files):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("backend/app/evaluation/"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            violations.append(
                {
                    "code": "ONLINE_SOURCE_UNREADABLE",
                    "path": relative,
                    "line": 0,
                    "detail": str(exc),
                }
            )
            continue
        source_hashes[relative] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        for line_number, line in enumerate(source.splitlines(), 1):
            if "/document-analysis/word" in line:
                violations.append(
                    {
                        "code": "LEGACY_SYNC_PROTOCOL",
                        "path": relative,
                        "line": line_number,
                    }
                )
            if "analyzeWordDocument" in line:
                violations.append(
                    {
                        "code": "LEGACY_SYNC_CLIENT",
                        "path": relative,
                        "line": line_number,
                    }
                )
            for pattern in _DUAL_TRACK_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        {
                            "code": "DUAL_TRACK_SWITCH",
                            "path": relative,
                            "line": line_number,
                            "symbol": pattern.pattern,
                        }
                    )

        if path.suffix != ".py":
            continue
        try:
            facts = _python_static_facts(source)
        except SyntaxError as exc:
            violations.append(
                {
                    "code": "ONLINE_PYTHON_SYNTAX_ERROR",
                    "path": relative,
                    "line": exc.lineno or 0,
                    "detail": exc.msg,
                }
            )
            continue

        for module, line_number in facts["imports"]:
            if (
                module == "app.services.extraction.candidate_store"
                and relative not in _CANDIDATE_STORE_IMPORT_ALLOWLIST
            ):
                violations.append(
                    {
                        "code": "UNREVIEWED_CANDIDATE_STORE_IMPORT",
                        "path": relative,
                        "line": line_number,
                        "symbol": module,
                    }
                )

        for caller, callee, line_number in facts["calls"]:
            edge = (relative, caller, callee)
            call_index.add(edge)
            kind = _LEGACY_CALL_KINDS.get(callee)
            if kind is None:
                continue
            allowed = edge in _ALLOWED_LEGACY_CALL_EDGES
            observed_edges.append(
                {
                    "kind": kind,
                    "path": relative,
                    "line": line_number,
                    "caller": caller,
                    "callee": callee,
                    "disposition": "shared_allowlist" if allowed else "blocked",
                }
            )
            if not allowed:
                violations.append(
                    {
                        "code": "UNREVIEWED_LEGACY_CALL_EDGE",
                        "kind": kind,
                        "path": relative,
                        "line": line_number,
                        "caller": caller,
                        "callee": callee,
                    }
                )

        if relative == _NEW_RUN_ROOTS[0] or any(
            relative.startswith(prefix) for prefix in _NEW_RUN_ROOTS[1:]
        ):
            for symbol, line_number in facts["symbols"]:
                if symbol in _NEW_RUN_FORBIDDEN_SYMBOLS:
                    violations.append(
                        {
                            "code": "NEW_RUN_LEGACY_DEPENDENCY",
                            "path": relative,
                            "line": line_number,
                            "symbol": symbol,
                        }
                    )

    for path, caller, callee in sorted(_REQUIRED_GUARD_CALLS):
        if (path, caller, callee) not in call_index:
            violations.append(
                {
                    "code": "RETIREMENT_GUARD_MISSING",
                    "path": path,
                    "line": 0,
                    "caller": caller,
                    "callee": callee,
                }
            )

    violations.sort(
        key=lambda item: (
            item.get("path", ""),
            item.get("line", 0),
            item.get("code", ""),
            item.get("symbol", ""),
        )
    )
    observed_edges.sort(
        key=lambda item: (item["path"], item["line"], item["caller"], item["callee"])
    )
    return {
        "schema_version": STATIC_AUDIT_SCHEMA,
        "status": "blocked" if violations else "pass",
        "online_only": True,
        "excluded_exact_prefixes": ["backend/app/evaluation/"],
        "scanned_files": len(source_hashes),
        "sources_sha256": _digest(source_hashes),
        "observed_legacy_call_edges": observed_edges,
        "violations": violations,
    }


def manifest_hash(manifest: dict[str, Any]) -> str:
    """Hash the frozen inventory; execution approval is a separate envelope."""
    payload = deepcopy(manifest)
    payload.pop("manifest_sha256", None)
    payload.pop("execution_authorization", None)
    return _digest(payload)


def verify_manifest_hash(manifest: dict[str, Any]) -> bool:
    expected = manifest.get("manifest_sha256")
    return isinstance(expected, str) and len(expected) == 64 and expected == manifest_hash(manifest)


def _row_hash(row: dict[str, Any]) -> str:
    return _digest(row)


def _verify_audit_chain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    chained = sorted(
        (row for row in rows if row.get("seq") is not None),
        key=lambda row: int(row["seq"]),
    )
    previous = GENESIS_AUDIT_HASH
    expected_seq = 1
    for row in chained:
        if int(row["seq"]) != expected_seq:
            return {
                "ok": False,
                "verified_count": expected_seq - 1,
                "broken_at_seq": row["seq"],
                "reason": "non_contiguous_sequence",
            }
        record = {
            "seq": expected_seq,
            "action": row.get("action"),
            "actor": row.get("actor"),
            "entity_iri": row.get("entity_iri"),
            "details": row.get("details"),
        }
        expected_hash = hashlib.sha256(
            (previous + _canonical_bytes(record).decode("utf-8")).encode("utf-8")
        ).hexdigest()
        if row.get("prev_hash") != previous or row.get("entry_hash") != expected_hash:
            return {
                "ok": False,
                "verified_count": expected_seq - 1,
                "broken_at_seq": row["seq"],
                "reason": "hash_mismatch",
            }
        previous = expected_hash
        expected_seq += 1
    return {
        "ok": True,
        "verified_count": len(chained),
        "head_seq": len(chained),
        "head_hash": previous,
    }


def _primary_key(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    identity = {column.name: _jsonable(row[column.name]) for column in table.primary_key.columns}
    return identity or {"_row_sha256": _row_hash(row)}


def _row_key(table: Table, row: dict[str, Any]) -> tuple[str, str]:
    return table.name, json.dumps(_primary_key(table, row), sort_keys=True, separators=(",", ":"))


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _database_identity(database_url: str) -> dict[str, str | None]:
    url = make_url(database_url)
    database = url.database
    if url.get_backend_name() == "sqlite" and database not in {None, "", ":memory:"}:
        database = str(Path(database).expanduser().resolve())
    safe = {
        "dialect": url.get_backend_name(),
        "driver": url.get_driver_name(),
        "host": url.host,
        "port": str(url.port) if url.port is not None else None,
        "database": database,
    }
    return {**safe, "fingerprint": _digest(safe)}


def _schema_snapshot(inspector: Any) -> tuple[dict[str, Any], str]:
    tables: dict[str, Any] = {}
    for table_name in sorted(inspector.get_table_names()):
        tables[table_name] = {
            "columns": [
                {
                    "name": column["name"],
                    "type": str(column["type"]),
                    "nullable": bool(column.get("nullable", True)),
                }
                for column in inspector.get_columns(table_name)
            ],
            "primary_key": inspector.get_pk_constraint(table_name).get("constrained_columns", []),
            "foreign_keys": sorted(
                [
                    {
                        "columns": fk.get("constrained_columns") or [],
                        "referred_table": fk.get("referred_table"),
                        "referred_columns": fk.get("referred_columns") or [],
                    }
                    for fk in inspector.get_foreign_keys(table_name)
                ],
                key=lambda item: _digest(item),
            ),
        }
    return tables, _digest(tables)


def _select_rows(
    connection: Connection, table: Table, predicate: Any | None = None
) -> list[dict[str, Any]]:
    statement = select(table)
    if predicate is not None:
        statement = statement.where(predicate)
    return [dict(row) for row in connection.execute(statement).mappings()]


def _job_predicate(table: Table, job_ids: Iterable[str]) -> Any:
    return cast(table.c.job_id, String).in_(sorted(set(job_ids)))


def _contains_token(value: Any, tokens: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.update(_contains_token(key, tokens))
            found.update(_contains_token(item, tokens))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            found.update(_contains_token(item, tokens))
    elif value is not None and str(value) in tokens:
        found.add(str(value))
    return found


def _resolve_file(path_value: str, storage_root: Path) -> Path:
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = storage_root / candidate
    # Normalize ``..`` while retaining the final path component so a symlink is
    # visible to the ownership gate instead of silently following it.
    return Path(os.path.abspath(candidate))


def _inside(path: Path, roots: list[Path]) -> bool:
    resolved_path = path.resolve(strict=False)
    for root in roots:
        try:
            resolved_path.relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path.resolve(strict=False)),
            "exists": False,
            "files": 0,
            "sha256": None,
        }
    records: list[dict[str, Any]] = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts:
            records.append(
                {
                    "path": item.relative_to(path).as_posix(),
                    "size": item.stat().st_size,
                    "sha256": _file_hash(item),
                }
            )
    return {
        "path": str(path.resolve()),
        "exists": True,
        "files": len(records),
        "sha256": _digest(records),
    }


def _classify_word_job(row: dict[str, Any]) -> tuple[str, str]:
    if str(row.get("source_type") or "").lower() != "word":
        return "out_of_scope", "non_word"
    config = _parse_json_object(row.get("source_config"))
    mode = config.get("mode")
    if mode == "auto" and not any(
        config.get(key) for key in ("class_mapping_id", "template_id", "document_role")
    ):
        return "target", LEGACY_SOURCE_PATTERN
    if mode == "template_default" or config.get("template_id"):
        return "protected", "template_default"
    if config.get("class_mapping_id"):
        return "protected", "declarative_class_mapping"
    return "unknown", f"unproven_word_mode:{mode!r}"


def _fetch_owned_rows(
    connection: Connection,
    tables: dict[str, Table],
    target_job_ids: list[str],
    target_jobs: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[Table, dict[str, Any]]]:
    selected: dict[tuple[str, str], tuple[Table, dict[str, Any]]] = {}

    def add(table: Table, rows: Iterable[dict[str, Any]]) -> bool:
        changed = False
        for row in rows:
            key = _row_key(table, row)
            if key not in selected:
                selected[key] = (table, row)
                changed = True
        return changed

    jobs_table = tables["extraction_jobs"]
    add(jobs_table, target_jobs)
    for name in sorted(OWNED_DIRECT_TABLES):
        table = tables.get(name)
        if table is None or "job_id" not in table.c:
            continue
        add(
            table,
            _select_rows(connection, table, _job_predicate(table, target_job_ids)),
        )

    # Follow only owned child rows.  Protected consumers are recorded later as
    # references and therefore make the source object non-exclusive.
    changed = True
    while changed:
        changed = False
        by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for table, row in selected.values():
            by_table[table.name].append(row)
        for child_name in sorted(OWNED_TABLES - {"document_analyses"}):
            child = tables.get(child_name)
            if child is None:
                continue
            for constraint in child.foreign_key_constraints:
                parent_name = constraint.referred_table.name
                parents = by_table.get(parent_name, [])
                if not parents:
                    continue
                elements = list(constraint.elements)
                conditions = []
                for parent in parents:
                    conditions.append(
                        and_(
                            *[
                                child.c[element.parent.name] == parent[element.column.name]
                                for element in elements
                            ]
                        )
                    )
                if conditions:
                    changed |= add(child, _select_rows(connection, child, or_(*conditions)))

    # document_analyses is parent data and may be shared.  Select it only when
    # every evidence_job_state reference belongs to the target set.
    analyses = tables.get("document_analyses")
    states = tables.get("evidence_job_states")
    if analyses is not None and states is not None and "analysis_id" in states.c:
        selected_states = [
            row
            for table, row in selected.values()
            if table.name == "evidence_job_states" and row.get("analysis_id") is not None
        ]
        for analysis_id in sorted({str(row["analysis_id"]) for row in selected_states}):
            all_refs = _select_rows(
                connection, states, cast(states.c.analysis_id, String) == analysis_id
            )
            owners = {str(row.get("job_id")) for row in all_refs}
            if len(owners) == 1 and owners.issubset(target_job_ids):
                add(
                    analyses,
                    _select_rows(connection, analyses, cast(analyses.c.id, String) == analysis_id),
                )
    return selected


def _selected_job_owners(
    selected: dict[tuple[str, str], tuple[Table, dict[str, Any]]],
    target_job_ids: list[str],
) -> dict[tuple[str, str], str | None]:
    """Propagate one target job identity through the selected FK closure."""
    target_set = set(target_job_ids)
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for key, (table, row) in selected.items():
        if table.name == "extraction_jobs" and str(row.get("id")) in target_set:
            owners[key].add(str(row["id"]))
        if row.get("job_id") is not None and str(row["job_id"]) in target_set:
            owners[key].add(str(row["job_id"]))

    changed = True
    while changed:
        changed = False
        by_table: dict[str, list[tuple[tuple[str, str], dict[str, Any]]]] = defaultdict(list)
        for key, (table, row) in selected.items():
            by_table[table.name].append((key, row))
        for child_key, (child, child_row) in selected.items():
            for constraint in child.foreign_key_constraints:
                elements = list(constraint.elements)
                for parent_key, parent_row in by_table.get(constraint.referred_table.name, []):
                    if not all(
                        child_row[element.parent.name] == parent_row[element.column.name]
                        for element in elements
                    ):
                        continue
                    combined = owners[child_key] | owners[parent_key]
                    if combined - owners[child_key]:
                        owners[child_key].update(combined)
                        changed = True
                    if combined - owners[parent_key]:
                        owners[parent_key].update(combined)
                        changed = True
    return {key: next(iter(values)) if len(values) == 1 else None for key, values in owners.items()}


def _references(
    connection: Connection,
    tables: dict[str, Table],
    selected: dict[tuple[str, str], tuple[Table, dict[str, Any]]],
    target_job_ids: list[str],
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], list[dict[str, Any]]]:
    refs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    selected_keys = set(selected)
    token_to_objects: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for key, (table, row) in selected.items():
        for column in table.primary_key.columns:
            raw = row[column.name]
            token = str(raw)
            # Numeric/autoincrement keys such as ``1`` are too ambiguous for a
            # schema-less JSON scan.  Their real references are covered by FK
            # traversal; logical references use stable string identities.
            if isinstance(raw, (str, UUID)) and len(token) >= 16:
                token_to_objects[token].add(key)
    for target_id in target_job_ids:
        job_key = next(
            key
            for key, (table, row) in selected.items()
            if table.name == "extraction_jobs" and str(row.get("id")) == target_id
        )
        token_to_objects[target_id].add(job_key)

    # Database-enforced inbound references.
    for child in tables.values():
        for constraint in child.foreign_key_constraints:
            elements = list(constraint.elements)
            parent_name = constraint.referred_table.name
            parents = [
                (key, row) for key, (table, row) in selected.items() if table.name == parent_name
            ]
            if not parents:
                continue
            conditions = [
                and_(
                    *[
                        child.c[element.parent.name] == parent[element.column.name]
                        for element in elements
                    ]
                )
                for _, parent in parents
            ]
            for child_row in _select_rows(connection, child, or_(*conditions)):
                child_key = _row_key(child, child_row)
                if child_key in selected_keys:
                    continue
                for parent_key, parent in parents:
                    if all(
                        child_row[element.parent.name] == parent[element.column.name]
                        for element in elements
                    ):
                        refs[parent_key].append(
                            {
                                "kind": "foreign_key",
                                "table": child.name,
                                "primary_key": _primary_key(child, child_row),
                                "columns": [element.parent.name for element in elements],
                                "blocking": child.name != "audit_log",
                                "protected_domain": child.name in PROTECTED_TABLES,
                            }
                        )

    # Search every non-binary scalar/JSON column for logical references not
    # represented by an FK.  Only locator metadata is emitted; business values
    # are never copied into the manifest.
    tokens = set(token_to_objects)
    logical_findings: list[dict[str, Any]] = []
    for table in tables.values():
        searchable = [
            column
            for column in table.columns
            if not str(column.type).upper().startswith(("BLOB", "BYTEA", "LARGEBINARY"))
        ]
        if not searchable:
            continue
        for row in _select_rows(connection, table):
            key = _row_key(table, row)
            if key in selected_keys:
                continue
            matched_by_column: dict[str, set[str]] = {}
            for column in searchable:
                found = _contains_token(row.get(column.name), tokens)
                if found:
                    matched_by_column[column.name] = found
            for column_name, found in matched_by_column.items():
                for token in found:
                    for target_key in token_to_objects[token]:
                        item = {
                            "kind": "logical_reference",
                            "table": table.name,
                            "primary_key": _primary_key(table, row),
                            "columns": [column_name],
                            "blocking": table.name != "audit_log",
                            "protected_domain": table.name in PROTECTED_TABLES,
                        }
                        if item not in refs[target_key]:
                            refs[target_key].append(item)
                        logical_findings.append({**item, "target_table": target_key[0]})
    for values in refs.values():
        values.sort(key=_digest)
    logical_findings.sort(key=_digest)
    return refs, logical_findings


def _publication_state(table_name: str, row: dict[str, Any]) -> str:
    review = str(row.get("review_status") or "").lower()
    if review in CONFIRMED_STATES:
        return "confirmed"
    if (
        table_name == "evidence_reviews"
        and str(row.get("decision") or "").lower() in CONFIRMED_STATES
    ):
        return "confirmed"
    payload = _parse_json_object(row.get("payload"))
    if str(payload.get("review_status") or "").lower() in CONFIRMED_STATES:
        return "confirmed"
    commit_status = str(payload.get("commit_status") or "").lower()
    if commit_status in PUBLISHED_COMMIT_STATES:
        return "published"
    if commit_status in {"queued", "applying", "requested"}:
        return "submitted"
    if row.get("committed_iri"):
        return "committed"
    if table_name == "evidence_commits":
        state = str(row.get("status") or "").lower()
        return "published" if state in PUBLISHED_COMMIT_STATES else "submitted"
    if table_name in {"evidence_assertions", "evidence_snapshots"}:
        return "published"
    return "unpublished"


def _active_lease(table_name: str, row: dict[str, Any], now: datetime) -> bool:
    status = str(row.get("status") or "").lower()
    if status not in ACTIVE_EXECUTION_STATES:
        return False
    expiry = row.get("lease_expires_at") or row.get("deadline_at")
    if expiry is None:
        return True
    if isinstance(expiry, str):
        try:
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            return True
    if not isinstance(expiry, datetime):
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > now


def _protected_database_snapshot(
    tables: dict[str, Table],
    all_rows: dict[str, list[dict[str, Any]]],
    selected_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name in sorted(tables):
        table = tables[name]
        hashes = sorted(
            _row_hash(row) for row in all_rows[name] if _row_key(table, row) not in selected_keys
        )
        snapshot[name] = {"rows": len(hashes), "sha256": _digest(hashes)}
    return {"tables": snapshot, "sha256": _digest(snapshot)}


def _default_authorization() -> dict[str, Any]:
    return {
        "approved_manifest_sha256": None,
        "approved_by": None,
        "approved_at": None,
        "maintenance_window": None,
        "old_writers_disabled": False,
        "late_writes_fenced": False,
        "quality_gates_approved": False,
        "protection_baseline_verified": False,
        "note": (
            "Populate only through the governed release record; this envelope is not hash input."
        ),
    }


def build_manifest(
    database_url: str,
    *,
    storage_root: Path | str = BACKEND_ROOT,
    project_root: Path | str = ROOT,
    allowed_roots: Iterable[Path | str] | None = None,
    explicit_target_job_ids: Iterable[str] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a deterministic, read-only retirement inventory."""
    storage_root = Path(storage_root).expanduser().resolve()
    project_root = Path(project_root).expanduser().resolve()
    roots = [Path(path).expanduser().resolve() for path in (allowed_roots or ())]
    if not roots:
        roots = [
            (storage_root / "data" / "uploads").resolve(),
            (storage_root / "data" / "evidence-worlds").resolve(),
        ]
    stamp = generated_at or _utc_now()
    engine = create_engine(database_url, pool_pre_ping=True)
    issues: list[dict[str, Any]] = []
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                elif connection.dialect.name == "sqlite":
                    connection.execute(text("PRAGMA query_only = ON"))

                inspector = inspect(connection)
                schema, schema_hash = _schema_snapshot(inspector)
                if "extraction_jobs" not in schema:
                    issues.append(
                        {
                            "code": "MISSING_EXTRACTION_JOBS",
                            "detail": "cannot establish cleanup scope",
                        }
                    )
                    jobs: list[dict[str, Any]] = []
                    tables: dict[str, Table] = {}
                else:
                    metadata = MetaData()
                    metadata.reflect(bind=connection)
                    tables = {table.name: table for table in metadata.tables.values()}
                    jobs = _select_rows(connection, tables["extraction_jobs"])

                all_rows = {name: _select_rows(connection, table) for name, table in tables.items()}
                audit_chain = (
                    _verify_audit_chain(all_rows["audit_log"])
                    if "audit_log" in all_rows
                    else {"ok": False, "reason": "audit_log_missing"}
                )
                if not audit_chain["ok"]:
                    issues.append(
                        {
                            "code": "AUDIT_CHAIN_UNVERIFIED",
                            "detail": audit_chain,
                        }
                    )
                classified = [(*_classify_word_job(row), row) for row in jobs]
                detected_targets = {
                    str(row["id"]) for category, _, row in classified if category == "target"
                }
                requested = (
                    {str(item) for item in explicit_target_job_ids}
                    if explicit_target_job_ids is not None
                    else detected_targets
                )
                target_jobs = [row for row in jobs if str(row.get("id")) in requested]
                missing_targets = sorted(requested - {str(row.get("id")) for row in target_jobs})
                for job_id in missing_targets:
                    issues.append({"code": "TARGET_JOB_MISSING", "job_id": job_id})
                for row in target_jobs:
                    category, reason = _classify_word_job(row)
                    if category != "target":
                        issues.append(
                            {
                                "code": "TARGET_PATTERN_MISMATCH",
                                "job_id": str(row["id"]),
                                "detail": reason,
                            }
                        )

                unknown_jobs = sorted(
                    [
                        {"job_id": str(row["id"]), "reason": reason}
                        for category, reason, row in classified
                        if category == "unknown"
                    ],
                    key=lambda item: item["job_id"],
                )
                protected_jobs = sorted(
                    [
                        {"job_id": str(row["id"]), "reason": reason}
                        for category, reason, row in classified
                        if category == "protected"
                    ],
                    key=lambda item: item["job_id"],
                )
                if unknown_jobs:
                    issues.append(
                        {
                            "code": "UNKNOWN_WORD_JOB_OWNERSHIP",
                            "count": len(unknown_jobs),
                            "job_ids": [item["job_id"] for item in unknown_jobs],
                        }
                    )

                target_ids = sorted(str(row["id"]) for row in target_jobs)
                selected = (
                    _fetch_owned_rows(connection, tables, target_ids, target_jobs)
                    if target_ids
                    else {}
                )
                refs, logical_findings = (
                    _references(connection, tables, selected, target_ids) if selected else ({}, [])
                )
                selected_owners = _selected_job_owners(selected, target_ids)

                items: list[dict[str, Any]] = []
                now = _utc_now()
                active_leases: list[dict[str, Any]] = []
                for key, (table, row) in sorted(selected.items(), key=lambda item: item[0]):
                    job_id = selected_owners.get(key)
                    state = _publication_state(table.name, row)
                    blocking_refs = [ref for ref in refs.get(key, []) if ref["blocking"]]
                    ownership = (
                        "unknown" if job_id is None else "shared" if blocking_refs else "exclusive"
                    )
                    item = {
                        "object_id": f"db:{table.name}:{_digest(_primary_key(table, row))[:20]}",
                        "kind": "database_row",
                        "job_id": job_id,
                        "source_pattern": LEGACY_SOURCE_PATTERN,
                        "locator": {
                            "table": table.name,
                            "primary_key": _primary_key(table, row),
                        },
                        "content_sha256": _row_hash(row),
                        "ownership": ownership,
                        "publication_state": state,
                        "immutable_history": table.name in IMMUTABLE_TABLES,
                        "references": refs.get(key, []),
                        "action": "pending_classification",
                        "post_check": {
                            "row_absent": True,
                            "references_absent_or_invalidated": True,
                        },
                    }
                    if _active_lease(table.name, row, now):
                        active_leases.append(
                            {
                                "object_id": item["object_id"],
                                "table": table.name,
                                "job_id": job_id,
                            }
                        )
                    items.append(item)
                    if job_id is None:
                        issues.append(
                            {
                                "code": "UNASSIGNED_SELECTED_OBJECT",
                                "object_id": item["object_id"],
                            }
                        )

                # Exact files: source, three fixed legacy cache names, and commit
                # worlds.  Missing optional caches are not invented as objects.
                path_values: dict[Path, dict[str, Any]] = {}

                def register_path(path: Path, job_id: str, role: str, required: bool) -> None:
                    metadata = path_values.setdefault(
                        path,
                        {"job_ids": set(), "roles": set(), "required": False},
                    )
                    metadata["job_ids"].add(job_id)
                    metadata["roles"].add(role)
                    metadata["required"] = metadata["required"] or required

                uploads_root = (storage_root / "data" / "uploads").resolve()
                for job in target_jobs:
                    job_id = str(job["id"])
                    if job.get("document_path"):
                        path = _resolve_file(str(job["document_path"]), storage_root)
                        register_path(path, job_id, "source_document", True)
                    for suffix, role in (
                        (".annotated.json", "annotation_cache"),
                        (".annotation_checkpoint.json", "checkpoint"),
                        (".annotation_checkpoint.json.jsonl", "checkpoint_journal"),
                    ):
                        path = (uploads_root / f"{job_id}{suffix}").absolute()
                        if path.exists() or path.is_symlink():
                            register_path(path, job_id, role, False)
                for _, (table, row) in selected.items():
                    if table.name == "evidence_commits" and row.get("world_path"):
                        path = _resolve_file(str(row["world_path"]), storage_root)
                        register_path(path, str(row.get("job_id")), "commit_world", True)

                # Exact scalar or nested-JSON path references are ownership
                # evidence.  This catches report source bundles as well as
                # ordinary ``*_path`` columns without copying their content.
                for path, file_meta in sorted(path_values.items(), key=lambda item: str(item[0])):
                    external_refs: list[dict[str, Any]] = []
                    file_job_ids = sorted(file_meta["job_ids"])
                    if len(file_job_ids) != 1:
                        external_refs.append(
                            {
                                "kind": "shared_target_path",
                                "job_ids": file_job_ids,
                                "blocking": True,
                            }
                        )
                        issues.append(
                            {
                                "code": "SHARED_TARGET_FILE",
                                "job_ids": file_job_ids,
                                "path": str(path),
                            }
                        )
                    raw_candidates = {str(path), os.path.relpath(path, storage_root)}
                    for table_name, rows in all_rows.items():
                        table = tables[table_name]
                        for row in rows:
                            key = _row_key(table, row)
                            if key in selected:
                                continue
                            matched_columns = [
                                column.name
                                for column in table.columns
                                if _contains_token(row.get(column.name), raw_candidates)
                            ]
                            if matched_columns:
                                reference = {
                                    "kind": "path_reference",
                                    "table": table.name,
                                    "primary_key": _primary_key(table, row),
                                    "columns": matched_columns,
                                    "blocking": table.name != "audit_log",
                                    "protected_domain": table.name in PROTECTED_TABLES,
                                }
                                if reference not in external_refs:
                                    external_refs.append(reference)
                    exists = path.exists()
                    is_regular = exists and path.is_file() and not path.is_symlink()
                    allowed = _inside(path, roots)
                    if file_meta["required"] and not is_regular:
                        issues.append(
                            {
                                "code": "REQUIRED_FILE_UNHASHED",
                                "job_ids": file_job_ids,
                                "path": str(path),
                            }
                        )
                    if exists and not allowed:
                        issues.append(
                            {
                                "code": "FILE_OUTSIDE_ALLOWED_ROOTS",
                                "job_ids": file_job_ids,
                                "path": str(path),
                            }
                        )
                    if not exists:
                        continue
                    item = {
                        "object_id": f"file:{_digest(str(path))[:24]}",
                        "kind": "file",
                        "job_id": file_job_ids[0],
                        "source_pattern": LEGACY_SOURCE_PATTERN,
                        "locator": {
                            "absolute_path": str(path),
                            "roles": sorted(file_meta["roles"]),
                        },
                        "content_sha256": _file_hash(path) if is_regular else None,
                        "size": path.stat().st_size if is_regular else None,
                        "ownership": "shared"
                        if any(ref["blocking"] for ref in external_refs)
                        else ("exclusive" if is_regular and allowed else "unknown"),
                        "publication_state": "unpublished",
                        "references": sorted(external_refs, key=_digest),
                        "action": "pending_classification",
                        "post_check": {"file_absent": True, "path_not_recreated": True},
                    }
                    items.append(item)

                # One protected/confirmed member protects the complete job
                # closure.  Partial physical deletion would strand references or
                # erase the history behind a published fact.
                by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for item in items:
                    by_job[item["job_id"]].append(item)
                job_results: list[dict[str, Any]] = []
                for job_id in target_ids:
                    members = by_job[job_id]
                    published = sorted(
                        {
                            item["publication_state"]
                            for item in members
                            if item["publication_state"] != "unpublished"
                        }
                    )
                    ownerships = {item["ownership"] for item in members}
                    if published:
                        classification = "published_or_confirmed"
                        reason = ",".join(published)
                        action = "retain_and_append_invalidation_outside_this_tool"
                    elif ownerships & {"shared", "unknown"}:
                        classification = "shared_or_unknown"
                        reason = "shared_reference_or_unproven_file_ownership"
                        action = "retain_and_resolve_ownership"
                    else:
                        classification = "unpublished_exclusive"
                        reason = "exclusive_unpublished_closure"
                        action = "physical_delete"
                    for item in members:
                        item["classification"] = classification
                        item["action"] = action
                    job_results.append(
                        {
                            "job_id": job_id,
                            "classification": classification,
                            "reason": reason,
                            "object_ids": sorted(item["object_id"] for item in members),
                        }
                    )

                categories = {
                    name: sorted(
                        item["object_id"] for item in items if item.get("classification") == name
                    )
                    for name in (
                        "unpublished_exclusive",
                        "shared_or_unknown",
                        "published_or_confirmed",
                    )
                }
                blocked_jobs = [
                    row for row in job_results if row["classification"] != "unpublished_exclusive"
                ]
                if blocked_jobs:
                    issues.append(
                        {
                            "code": "G_C03_PROTECTED_TARGETS",
                            "job_ids": [row["job_id"] for row in blocked_jobs],
                        }
                    )
                if active_leases:
                    issues.append({"code": "ACTIVE_LEGACY_LEASES", "leases": active_leases})

                selected_keys = set(selected)
                protected_database = _protected_database_snapshot(tables, all_rows, selected_keys)
                audit_present = "audit_log" in tables
                checks = {
                    "scope_is_exact": not unknown_jobs and not missing_targets,
                    "ownership_complete": not unknown_jobs
                    and all(item["ownership"] != "unknown" for item in items),
                    "reference_closure_complete": not unknown_jobs and "extraction_jobs" in tables,
                    "all_target_files_hashed": not any(
                        issue["code"] in {"REQUIRED_FILE_UNHASHED", "FILE_OUTSIDE_ALLOWED_ROOTS"}
                        for issue in issues
                    ),
                    "active_legacy_leases": active_leases,
                    "immutable_audit_history_preserved": audit_present
                    and audit_chain["ok"]
                    and not any(item["locator"].get("table") == "audit_log" for item in items),
                    "protected_objects_snapshotted": audit_present,
                    "g_c03_passed": not blocked_jobs,
                }
                protected_files = {
                    "ontology": _tree_fingerprint(project_root / "ontology"),
                    "offline_evaluations": _tree_fingerprint(project_root / "docs" / "evaluations"),
                    "evaluation_code": _tree_fingerprint(
                        project_root / "backend" / "app" / "evaluation"
                    ),
                }
                inventory = {
                    "scope": {
                        "source_pattern": LEGACY_SOURCE_PATTERN,
                        "target_job_ids": target_ids,
                        "protected_word_jobs": protected_jobs,
                        "unknown_word_jobs": unknown_jobs,
                    },
                    "items": sorted(items, key=lambda item: item["object_id"]),
                    "job_classifications": job_results,
                    "categories": categories,
                    "checks": checks,
                    "issues": sorted(issues, key=_digest),
                    "logical_reference_findings": logical_findings,
                    "protected_database": protected_database,
                    "audit_chain": audit_chain,
                    "protected_files": protected_files,
                }
                inventory_hash = _digest(inventory)
                transaction.rollback()
            except BaseException:
                transaction.rollback()
                raise
    finally:
        engine.dispose()

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at": _iso(stamp),
        "read_only_inventory": True,
        "database": {
            **_database_identity(database_url),
            "schema_sha256": schema_hash,
        },
        "environment": {
            "storage_root": str(storage_root),
            "project_root": str(project_root),
            "allowed_file_roots": [str(root) for root in roots],
        },
        **inventory,
        "inventory_sha256": inventory_hash,
        "execution_authorization": _default_authorization(),
    }
    manifest["manifest_sha256"] = manifest_hash(manifest)
    return manifest


def _default_database_url() -> str:
    configured = os.environ.get("DATABASE_URL")
    if configured:
        return configured
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.config import settings

    return settings.database_url


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="scan online source edges without opening a database or writing a manifest",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--storage-root", type=Path, default=BACKEND_ROOT)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--allowed-root", type=Path, action="append", default=[])
    parser.add_argument("--target-job-id", action="append", default=None)
    args = parser.parse_args(argv)

    static_audit = audit_online_retirement(args.project_root)
    if args.static_only:
        print(json.dumps(static_audit, ensure_ascii=False, sort_keys=True))
        return 0 if static_audit["status"] == "pass" else 1
    if static_audit["status"] != "pass":
        print(json.dumps(static_audit, ensure_ascii=False, sort_keys=True))
        return 1
    if args.output is None:
        parser.error("--output is required unless --static-only is used")

    manifest = build_manifest(
        args.database_url or _default_database_url(),
        storage_root=args.storage_root,
        project_root=args.project_root,
        allowed_roots=args.allowed_root or None,
        explicit_target_job_ids=args.target_job_id,
    )
    _write_json_atomic(args.output, manifest)
    summary = {
        "status": "review_required" if manifest["issues"] else "ready_for_dry_run",
        "read_only": True,
        "target_jobs": len(manifest["scope"]["target_job_ids"]),
        "items": len(manifest["items"]),
        "manifest_sha256": manifest["manifest_sha256"],
        "static_sources_sha256": static_audit["sources_sha256"],
        "static_legacy_call_edges": len(static_audit["observed_legacy_call_edges"]),
        "issues": [issue["code"] for issue in manifest["issues"]],
        "output": str(args.output.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    # Inventory findings are not an audit failure: cleanup performs the blocking
    # gate.  This keeps read-only inventory available even for unsafe datasets.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
