#!/usr/bin/env python3
"""Validate or apply one manifest-bound legacy Word recognition cleanup.

Dry-run is the default.  Physical deletion is available only through
``--execute`` and requires a frozen manifest hash, a matching governed approval
envelope, and an active named maintenance window.  There is deliberately no
force/bypass option.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, and_, create_engine, delete, or_, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.sql.schema import Table

try:
    from scripts.audit_word_recognition_retirement import (
        MANIFEST_SCHEMA,
        _database_identity,
        _default_database_url,
        _file_hash,
        _inside,
        _jsonable,
        _row_hash,
        _verify_audit_chain,
        build_manifest,
        verify_manifest_hash,
    )
except ModuleNotFoundError:  # direct ``python scripts/cleanup_...py`` execution
    from audit_word_recognition_retirement import (
        MANIFEST_SCHEMA,
        _database_identity,
        _default_database_url,
        _file_hash,
        _inside,
        _jsonable,
        _row_hash,
        _verify_audit_chain,
        build_manifest,
        verify_manifest_hash,
    )


GENESIS_HASH = "0" * 64
REQUIRED_CHECKS = (
    "scope_is_exact",
    "ownership_complete",
    "reference_closure_complete",
    "all_target_files_hashed",
    "immutable_audit_history_preserved",
    "protected_objects_snapshotted",
    "g_c03_passed",
)
REQUIRED_EXECUTION_APPROVALS = (
    "old_writers_disabled",
    "late_writes_fenced",
    "quality_gates_approved",
    "protection_baseline_verified",
)


def _manifest_counts(manifest: dict[str, Any], *, failed: int = 0) -> dict[str, int]:
    items = manifest.get("items") or []
    return {
        "physical_delete": sum(item.get("action") == "physical_delete" for item in items),
        "retain": sum(str(item.get("action") or "").startswith("retain") for item in items),
        "invalidation_required": sum(
            item.get("action") == "retain_and_append_invalidation_outside_this_tool"
            for item in items
        ),
        "failed": failed,
    }


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def load_manifest(path: Path | str) -> dict[str, Any]:
    with Path(path).expanduser().open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def _approval_issues(
    manifest: dict[str, Any],
    expected_hash: str | None,
    maintenance_window: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    current_hash = manifest.get("manifest_sha256")
    if not expected_hash:
        issues.append({"code": "EXECUTION_REQUIRES_EXPECTED_MANIFEST_HASH"})
    elif expected_hash != current_hash:
        issues.append({"code": "EXPECTED_MANIFEST_HASH_MISMATCH"})
    approval = manifest.get("execution_authorization")
    if not isinstance(approval, dict):
        return issues + [{"code": "EXECUTION_AUTHORIZATION_MISSING"}]
    if approval.get("approved_manifest_sha256") != current_hash:
        issues.append({"code": "MANIFEST_NOT_APPROVED_AT_FROZEN_HASH"})
    for field in ("approved_by", "approved_at"):
        if not approval.get(field):
            issues.append({"code": "EXECUTION_APPROVAL_FIELD_MISSING", "field": field})
    for field in REQUIRED_EXECUTION_APPROVALS:
        if approval.get(field) is not True:
            issues.append({"code": "EXECUTION_GATE_NOT_APPROVED", "field": field})
    window = approval.get("maintenance_window")
    if not isinstance(window, dict):
        issues.append({"code": "MAINTENANCE_WINDOW_MISSING"})
        return issues
    if not maintenance_window or window.get("id") != maintenance_window:
        issues.append({"code": "MAINTENANCE_WINDOW_ID_MISMATCH"})
    starts_at = _parse_time(window.get("starts_at"))
    ends_at = _parse_time(window.get("ends_at"))
    if starts_at is None or ends_at is None or starts_at >= ends_at:
        issues.append({"code": "MAINTENANCE_WINDOW_INVALID"})
    elif not starts_at <= now <= ends_at:
        issues.append({"code": "OUTSIDE_MAINTENANCE_WINDOW"})
    return issues


def _basic_gate_issues(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        issues.append({"code": "UNSUPPORTED_MANIFEST_SCHEMA"})
    if manifest.get("read_only_inventory") is not True:
        issues.append({"code": "MANIFEST_NOT_READ_ONLY_INVENTORY"})
    if not verify_manifest_hash(manifest):
        issues.append({"code": "MANIFEST_HASH_MISMATCH"})
    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        issues.append({"code": "MANIFEST_CHECKS_MISSING"})
    else:
        for check in REQUIRED_CHECKS:
            if checks.get(check) is not True:
                issues.append({"code": "MANIFEST_CHECK_FAILED", "check": check})
        if checks.get("active_legacy_leases"):
            issues.append({"code": "ACTIVE_LEGACY_LEASES"})
    categories = manifest.get("categories")
    if not isinstance(categories, dict):
        issues.append({"code": "MANIFEST_CATEGORIES_MISSING"})
    else:
        for category in ("shared_or_unknown", "published_or_confirmed"):
            if categories.get(category):
                issues.append(
                    {
                        "code": "G_C03_BLOCKED",
                        "category": category,
                        "object_ids": categories[category],
                    }
                )
    for issue in manifest.get("issues") or []:
        code = issue.get("code") if isinstance(issue, dict) else "MALFORMED_AUDIT_ISSUE"
        issues.append({"code": "AUDIT_MANIFEST_BLOCKER", "source_code": code})
    for item in manifest.get("items") or []:
        if item.get("action") == "physical_delete" and (
            item.get("classification") != "unpublished_exclusive"
            or item.get("ownership") != "exclusive"
            or item.get("publication_state") != "unpublished"
        ):
            issues.append(
                {
                    "code": "ILLEGAL_PHYSICAL_DELETE_ACTION",
                    "object_id": item.get("object_id"),
                }
            )
        if (
            item.get("locator", {}).get("table") == "audit_log"
            and item.get("action") == "physical_delete"
        ):
            issues.append({"code": "IMMUTABLE_AUDIT_DELETE_FORBIDDEN"})
    return issues


def _find_applied_cleanup(database_url: str, manifest_sha256: str) -> dict[str, Any] | None:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            metadata = MetaData()
            metadata.reflect(bind=connection, only=lambda name, _: name == "audit_log")
            audit = metadata.tables.get("audit_log")
            if audit is None or "details" not in audit.c:
                return None
            rows = list(
                connection.execute(
                    select(audit).where(audit.c.seq.is_not(None)).order_by(audit.c.seq)
                ).mappings()
            )
            if not _verify_audit_chain([dict(row) for row in rows])["ok"]:
                return None
            cleanup_rows = connection.execute(
                select(audit).where(audit.c.action == "word_recognition.retirement.cleanup")
            ).mappings()
            for row in cleanup_rows:
                details = row.get("details")
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                if isinstance(details, dict) and details.get("manifest_sha256") == manifest_sha256:
                    return _jsonable(dict(row))
    finally:
        engine.dispose()
    return None


def validate_manifest(
    manifest: dict[str, Any],
    database_url: str,
    *,
    execute: bool = False,
    expected_manifest_sha256: str | None = None,
    maintenance_window: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate frozen content and re-audit live ownership/reference state."""
    now = now or datetime.now(timezone.utc)
    issues = _basic_gate_issues(manifest)
    if execute:
        issues.extend(_approval_issues(manifest, expected_manifest_sha256, maintenance_window, now))
        if not manifest.get("scope", {}).get("target_job_ids"):
            issues.append({"code": "EMPTY_EXECUTION_SCOPE"})
    if issues:
        return {
            "ok": False,
            "status": "G-C03 blocked",
            "mode": "execute" if execute else "dry-run",
            "counts": _manifest_counts(manifest, failed=len(issues)),
            "issues": issues,
        }

    if (
        manifest.get("database", {}).get("fingerprint")
        != _database_identity(database_url)["fingerprint"]
    ):
        issues.append({"code": "DATABASE_IDENTITY_MISMATCH"})
    environment = manifest.get("environment") or {}
    try:
        live = build_manifest(
            database_url,
            storage_root=Path(environment["storage_root"]),
            project_root=Path(environment["project_root"]),
            allowed_roots=[Path(value) for value in environment["allowed_file_roots"]],
            explicit_target_job_ids=manifest["scope"]["target_job_ids"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        issues.append({"code": "MANIFEST_ENVIRONMENT_INVALID", "detail": str(exc)})
        live = None
    if live is not None:
        if live.get("database", {}).get("schema_sha256") != manifest.get("database", {}).get(
            "schema_sha256"
        ):
            issues.append({"code": "DATABASE_SCHEMA_HASH_MISMATCH"})
        if live.get("inventory_sha256") != manifest.get("inventory_sha256"):
            issues.append(
                {
                    "code": "LIVE_INVENTORY_HASH_MISMATCH",
                    "expected": manifest.get("inventory_sha256"),
                    "actual": live.get("inventory_sha256"),
                }
            )
        for live_issue in live.get("issues") or []:
            issues.append(
                {
                    "code": "LIVE_AUDIT_BLOCKER",
                    "source_code": live_issue.get("code", "UNKNOWN"),
                }
            )
    if issues:
        return {
            "ok": False,
            "status": "G-C03 blocked",
            "mode": "execute" if execute else "dry-run",
            "counts": _manifest_counts(manifest, failed=len(issues)),
            "issues": issues,
        }
    return {
        "ok": True,
        "status": "validated",
        "mode": "execute" if execute else "dry-run",
        "manifest_sha256": manifest["manifest_sha256"],
        "counts": _manifest_counts(manifest),
        "issues": [],
    }


def _delete_table_order(tables: dict[str, Table], names: set[str]) -> list[str]:
    remaining = set(names)
    ordered: list[str] = []
    while remaining:
        parent_names = {
            constraint.referred_table.name
            for child_name in remaining
            for constraint in tables[child_name].foreign_key_constraints
            if constraint.referred_table.name in remaining
            and constraint.referred_table.name != child_name
        }
        ready = sorted(remaining - parent_names)
        if not ready:
            raise ValueError(f"cyclic deletion dependency: {sorted(remaining)}")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def _pk_predicate(table: Table, primary_key: dict[str, Any]) -> Any:
    expected = {column.name for column in table.primary_key.columns}
    if not expected or set(primary_key) != expected:
        raise ValueError(f"incomplete primary key for {table.name}")
    comparisons = []
    for name, value in primary_key.items():
        column = table.c[name]
        try:
            python_type = column.type.python_type
        except (AttributeError, NotImplementedError):
            python_type = None
        if value is not None and not isinstance(value, python_type or object):
            if python_type is UUID:
                value = UUID(str(value))
            elif python_type is Decimal:
                value = Decimal(str(value))
            elif python_type is datetime:
                value = _parse_time(value)
            elif python_type is date:
                value = date.fromisoformat(str(value))
            elif python_type in {int, float, str}:
                value = python_type(value)
        comparisons.append(column == value)
    return and_(*comparisons)


def _audit_canonical(
    seq: int,
    action: str,
    actor: str | None,
    entity_iri: str | None,
    details: dict[str, Any] | None,
) -> str:
    return json.dumps(
        {
            "seq": seq,
            "action": action,
            "actor": actor,
            "entity_iri": entity_iri,
            "details": details,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _append_audit(
    connection: Connection,
    audit: Table,
    *,
    actor: str,
    manifest_sha256: str,
    maintenance_window: str,
    counts: dict[str, int],
) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(20260906, 20)"))
    head = (
        connection.execute(
            select(audit.c.seq, audit.c.entry_hash)
            .where(audit.c.seq.is_not(None))
            .order_by(audit.c.seq.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    seq = int(head["seq"]) + 1 if head else 1
    previous = str(head["entry_hash"]) if head and head["entry_hash"] else GENESIS_HASH
    action = "word_recognition.retirement.cleanup"
    entity_iri = f"urn:word-recognition-retirement:{manifest_sha256}"
    details = {
        "manifest_sha256": manifest_sha256,
        "maintenance_window": maintenance_window,
        "counts": counts,
    }
    entry_hash = hashlib.sha256(
        (previous + _audit_canonical(seq, action, actor, entity_iri, details)).encode("utf-8")
    ).hexdigest()
    values: dict[str, Any] = {
        "action": action,
        "actor": actor,
        "entity_iri": entity_iri,
        "details": details,
        "prev_hash": previous,
        "entry_hash": entry_hash,
        "seq": seq,
    }
    if "created_at" in audit.c:
        values["created_at"] = datetime.now(timezone.utc)
    connection.execute(audit.insert().values(**values))


def _stage_files(
    items: list[dict[str, Any]], allowed_roots: list[Path], manifest_sha256: str
) -> list[tuple[Path, Path]]:
    staged: list[tuple[Path, Path]] = []
    try:
        for item in items:
            original = Path(item["locator"]["absolute_path"]).absolute()
            if not original.exists():
                raise ValueError(f"cleanup file disappeared: {original}")
            if (
                not _inside(original, allowed_roots)
                or original.is_symlink()
                or not original.is_file()
            ):
                raise ValueError(f"unsafe cleanup file: {original}")
            if _file_hash(original) != item["content_sha256"]:
                raise ValueError(f"file hash changed: {original}")
            quarantine = original.parent / (
                f".{original.name}.retirement-{manifest_sha256[:16]}-{len(staged)}"
            )
            if quarantine.exists() or quarantine.is_symlink():
                raise ValueError(f"quarantine collision: {quarantine}")
            os.replace(original, quarantine)
            staged.append((original, quarantine))
    except BaseException:
        for original, quarantine in reversed(staged):
            if quarantine.exists() and not original.exists():
                os.replace(quarantine, original)
        raise
    return staged


def _restore_staged(staged: list[tuple[Path, Path]]) -> None:
    for original, quarantine in reversed(staged):
        if quarantine.exists() and not original.exists():
            os.replace(quarantine, original)


def execute_cleanup(
    manifest: dict[str, Any],
    database_url: str,
    *,
    expected_manifest_sha256: str,
    maintenance_window: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Delete only exact unpublished/exclusive items after every gate passes."""
    stamp = now or datetime.now(timezone.utc)
    preliminary_issues = _basic_gate_issues(manifest)
    preliminary_issues.extend(
        _approval_issues(
            manifest,
            expected_manifest_sha256,
            maintenance_window,
            stamp,
        )
    )
    if not manifest.get("scope", {}).get("target_job_ids"):
        preliminary_issues.append({"code": "EMPTY_EXECUTION_SCOPE"})
    if (
        manifest.get("database", {}).get("fingerprint")
        != _database_identity(database_url)["fingerprint"]
    ):
        preliminary_issues.append({"code": "DATABASE_IDENTITY_MISMATCH"})
    if preliminary_issues:
        return {
            "ok": False,
            "status": "G-C03 blocked",
            "mode": "execute",
            "counts": _manifest_counts(manifest, failed=len(preliminary_issues)),
            "issues": preliminary_issues,
        }
    applied = _find_applied_cleanup(database_url, manifest.get("manifest_sha256", ""))
    if applied is not None:
        return {
            "ok": True,
            "status": "already_applied",
            "mode": "execute",
            "manifest_sha256": manifest.get("manifest_sha256"),
            "counts": applied.get("details", {}).get("counts", {}),
            "issues": [],
        }
    validation = validate_manifest(
        manifest,
        database_url,
        execute=True,
        expected_manifest_sha256=expected_manifest_sha256,
        maintenance_window=maintenance_window,
        now=stamp,
    )
    if not validation["ok"]:
        return validation

    delete_items = [item for item in manifest["items"] if item["action"] == "physical_delete"]
    db_items = [item for item in delete_items if item["kind"] == "database_row"]
    file_items = [item for item in delete_items if item["kind"] == "file"]
    environment = manifest["environment"]
    allowed_roots = [Path(value).resolve() for value in environment["allowed_file_roots"]]
    staged = _stage_files(file_items, allowed_roots, manifest["manifest_sha256"])
    engine = create_engine(database_url, pool_pre_ping=True)
    deleted_rows: dict[str, int] = defaultdict(int)
    retained_or_invalidated = _manifest_counts(manifest)["retain"]
    try:
        with engine.begin() as connection:
            metadata = MetaData()
            metadata.reflect(bind=connection)
            tables = {table.name: table for table in metadata.tables.values()}
            if "audit_log" not in tables:
                raise ValueError("append-only audit_log is required for cleanup")
            items_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in db_items:
                table_name = item["locator"]["table"]
                if table_name not in tables:
                    raise ValueError(f"manifest table disappeared: {table_name}")
                items_by_table[table_name].append(item)
            for table_name in _delete_table_order(tables, set(items_by_table)):
                table = tables[table_name]
                predicates = []
                for item in items_by_table[table_name]:
                    predicate = _pk_predicate(table, item["locator"]["primary_key"])
                    row = (
                        connection.execute(select(table).where(predicate)).mappings().one_or_none()
                    )
                    if row is None or _row_hash(dict(row)) != item["content_sha256"]:
                        raise ValueError(f"frozen row changed: {item['object_id']}")
                    predicates.append(predicate)
                result = connection.execute(delete(table).where(or_(*predicates)))
                if result.rowcount != len(predicates):
                    raise ValueError(
                        f"delete count mismatch for {table_name}: "
                        f"{result.rowcount}/{len(predicates)}"
                    )
                deleted_rows[table_name] += result.rowcount
            counts = {
                "database_rows_deleted": sum(deleted_rows.values()),
                "files_deleted": len(staged),
                "retained_or_invalidated": retained_or_invalidated,
                "failed": 0,
            }
            approval = manifest["execution_authorization"]
            _append_audit(
                connection,
                tables["audit_log"],
                actor=str(approval["approved_by"]),
                manifest_sha256=manifest["manifest_sha256"],
                maintenance_window=maintenance_window,
                counts=counts,
            )
    except BaseException:
        _restore_staged(staged)
        raise
    finally:
        engine.dispose()

    failures: list[str] = []
    for _, quarantine in staged:
        try:
            quarantine.unlink()
        except OSError as exc:
            failures.append(f"{quarantine}: {exc}")
    residuals: list[str] = []
    verification_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with verification_engine.connect() as connection:
            metadata = MetaData()
            metadata.reflect(bind=connection)
            tables = {table.name: table for table in metadata.tables.values()}
            for item in db_items:
                table = tables.get(item["locator"]["table"])
                if table is None:
                    residuals.append(item["object_id"])
                    continue
                if connection.execute(
                    select(table).where(_pk_predicate(table, item["locator"]["primary_key"]))
                ).first():
                    residuals.append(item["object_id"])
    finally:
        verification_engine.dispose()
    for item in file_items:
        if Path(item["locator"]["absolute_path"]).exists():
            residuals.append(item["object_id"])
    failures.extend(f"residual:{object_id}" for object_id in residuals)
    result = {
        "ok": not failures,
        "status": "applied" if not failures else "applied_with_file_cleanup_failures",
        "mode": "execute",
        "manifest_sha256": manifest["manifest_sha256"],
        "counts": {
            "database_rows_deleted": sum(deleted_rows.values()),
            "files_deleted": len(staged) - len(failures),
            "retained_or_invalidated": retained_or_invalidated,
            "failed": len(failures),
        },
        "database_rows": dict(sorted(deleted_rows.items())),
        "residual_count": len(residuals),
        "issues": [{"code": "QUARANTINE_UNLINK_FAILED", "detail": item} for item in failures],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database-url", default=None)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="validate only (default)")
    modes.add_argument(
        "--cutover-preflight",
        action="store_true",
        help="validate execution approvals/window/live hash without deleting anything",
    )
    modes.add_argument("--execute", action="store_true", help="apply an approved manifest")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--maintenance-window")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        database_url = args.database_url or _default_database_url()
        if args.execute:
            result = execute_cleanup(
                manifest,
                database_url,
                expected_manifest_sha256=args.expected_manifest_sha256 or "",
                maintenance_window=args.maintenance_window or "",
            )
        elif args.cutover_preflight:
            result = validate_manifest(
                manifest,
                database_url,
                execute=True,
                expected_manifest_sha256=args.expected_manifest_sha256 or "",
                maintenance_window=args.maintenance_window or "",
            )
            result = {**result, "mode": "cutover_preflight", "destructive": False}
        else:
            result = validate_manifest(manifest, database_url, execute=False)
    except Exception as exc:
        result = {
            "ok": False,
            "status": "G-C03 blocked",
            "mode": (
                "execute"
                if args.execute
                else "cutover_preflight"
                if args.cutover_preflight
                else "dry-run"
            ),
            "destructive": bool(args.execute),
            "issues": [{"code": type(exc).__name__, "detail": str(exc)}],
        }
    print(json.dumps(_jsonable(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    # Direct execution resolves the sibling audit module without turning scripts
    # into an application package.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
