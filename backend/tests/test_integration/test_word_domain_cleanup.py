"""Legacy Word retirement inventory and G-C03 cleanup gates (021 AC-T29)."""

# ruff: noqa: E402

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    update,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.audit_word_recognition_retirement import (
    audit_online_retirement,
    build_manifest,
    verify_manifest_hash,
)
from scripts.audit_word_recognition_retirement import (
    main as audit_main,
)
from scripts.cleanup_word_recognition import (
    _manifest_counts,
    execute_cleanup,
    validate_manifest,
)
from scripts.cleanup_word_recognition import (
    main as cleanup_main,
)


def test_cleanup_counts_every_retained_or_invalidation_action():
    counts = _manifest_counts(
        {
            "items": [
                {"action": "physical_delete"},
                {"action": "retain_and_resolve_ownership"},
                {"action": "retain_and_append_invalidation_outside_this_tool"},
            ]
        }
    )

    assert counts == {
        "physical_delete": 1,
        "retain": 2,
        "invalidation_required": 1,
        "failed": 0,
    }


def _schema(database_url: str) -> tuple[object, dict[str, Table]]:
    engine = create_engine(database_url)
    metadata = MetaData()
    tables = {
        "jobs": Table(
            "extraction_jobs",
            metadata,
            Column("id", String(36), primary_key=True),
            Column("source_type", String(20), nullable=False),
            Column("source_filename", String(100)),
            Column("source_config", JSON),
            Column("document_path", String(500)),
            Column("status", String(20)),
        ),
        "executions": Table(
            "annotation_executions",
            metadata,
            Column("job_id", String(36), ForeignKey("extraction_jobs.id"), primary_key=True),
            Column("run_id", String(32)),
            Column("status", String(20)),
            Column("pause_requested", Boolean, default=False),
            Column("lease_expires_at", DateTime(timezone=True)),
        ),
        "candidates": Table(
            "evidence_candidates",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("job_id", String(36), ForeignKey("extraction_jobs.id")),
            Column("review_status", String(20)),
            Column("payload", JSON),
        ),
        "revisions": Table(
            "evidence_candidate_revisions",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("candidate_id", String(64), ForeignKey("evidence_candidates.id")),
            Column("revision", Integer),
            Column("payload", JSON),
        ),
        "templates": Table(
            "ast_templates",
            metadata,
            Column("id", String(36), primary_key=True),
            Column("default_source_job_id", String(36), ForeignKey("extraction_jobs.id")),
            Column("default_source_path", String(500)),
            Column("status", String(20)),
        ),
        "report_runs": Table(
            "report_runs",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("source_bundle", JSON, nullable=False),
            Column("review_status", String(20)),
        ),
        "audit": Table(
            "audit_log",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("action", String(100), nullable=False),
            Column("entity_iri", String(500)),
            Column("actor", String(100)),
            Column("details", JSON),
            Column("created_at", DateTime(timezone=True)),
            Column("prev_hash", String(64)),
            Column("entry_hash", String(64)),
            Column("seq", Integer, unique=True),
        ),
    }
    metadata.create_all(engine)
    return engine, tables


def _environment(tmp_path: Path) -> tuple[str, Path, Path, object, dict[str, Table]]:
    database = tmp_path / "retirement.sqlite"
    database_url = f"sqlite:///{database}"
    project_root = tmp_path / "project"
    storage_root = project_root / "backend"
    (storage_root / "data" / "uploads").mkdir(parents=True)
    (project_root / "ontology").mkdir(parents=True)
    (project_root / "docs" / "evaluations").mkdir(parents=True)
    (project_root / "backend" / "app" / "evaluation").mkdir(parents=True)
    engine, tables = _schema(database_url)
    return database_url, project_root, storage_root, engine, tables


def _insert_target(
    engine: object,
    tables: dict[str, Table],
    storage_root: Path,
    *,
    job_id: str = "00000000-0000-0000-0000-000000000021",
) -> Path:
    source = storage_root / "data" / "uploads" / f"{job_id}.docx"
    source.write_bytes(b"legacy-word-source")
    with engine.begin() as connection:
        connection.execute(
            tables["jobs"].insert(),
            {
                "id": job_id,
                "source_type": "word",
                "source_filename": "legacy.docx",
                "source_config": {"mode": "auto"},
                "document_path": f"data/uploads/{job_id}.docx",
                "status": "complete",
            },
        )
    return source


def _manifest(database_url: str, project_root: Path, storage_root: Path) -> dict:
    return build_manifest(
        database_url,
        project_root=project_root,
        storage_root=storage_root,
    )


def _authorize(manifest: dict, now: datetime) -> str:
    identity = manifest["manifest_sha256"]
    manifest["execution_authorization"] = {
        "approved_manifest_sha256": identity,
        "approved_by": "release-manager",
        "approved_at": now.isoformat(),
        "maintenance_window": {
            "id": "mw-021-test",
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(minutes=5)).isoformat(),
        },
        "old_writers_disabled": True,
        "late_writes_fenced": True,
        "quality_gates_approved": True,
        "protection_baseline_verified": True,
    }
    assert verify_manifest_hash(manifest)
    return identity


def _seed_audit(connection, table: Table) -> str:
    details = {"baseline": True}
    canonical = json.dumps(
        {
            "seq": 1,
            "action": "baseline",
            "actor": "system",
            "entity_iri": None,
            "details": details,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    entry_hash = hashlib.sha256((("0" * 64) + canonical).encode()).hexdigest()
    connection.execute(
        table.insert(),
        {
            "action": "baseline",
            "actor": "system",
            "entity_iri": None,
            "details": details,
            "created_at": datetime.now(timezone.utc),
            "prev_hash": "0" * 64,
            "entry_hash": entry_hash,
            "seq": 1,
        },
    )
    return entry_hash


def test_audit_is_read_only_and_targets_only_exact_legacy_mode(tmp_path):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    source = _insert_target(engine, tables, storage_root)
    with engine.begin() as connection:
        connection.execute(
            tables["jobs"].insert(),
            [
                {
                    "id": "00000000-0000-0000-0000-000000000022",
                    "source_type": "word",
                    "source_config": {"mode": "template_default", "template_id": "tpl"},
                    "document_path": "data/uploads/template.docx",
                    "status": "complete",
                },
                {
                    "id": "00000000-0000-0000-0000-000000000023",
                    "source_type": "excel",
                    "source_config": {"mode": "auto"},
                    "document_path": None,
                    "status": "complete",
                },
            ],
        )

    manifest = _manifest(database_url, project_root, storage_root)

    assert manifest["scope"]["target_job_ids"] == ["00000000-0000-0000-0000-000000000021"]
    assert manifest["scope"]["protected_word_jobs"] == [
        {
            "job_id": "00000000-0000-0000-0000-000000000022",
            "reason": "template_default",
        }
    ]
    assert manifest["checks"]["g_c03_passed"] is True
    assert verify_manifest_hash(manifest)
    assert source.read_bytes() == b"legacy-word-source"
    with engine.connect() as connection:
        assert connection.execute(select(tables["jobs"])).fetchall().__len__() == 3


def test_static_audit_reports_reviewed_edges_and_current_tree_passes():
    result = audit_online_retirement(REPOSITORY_ROOT)

    assert result["status"] == "pass", result["violations"]
    assert result["online_only"] is True
    assert result["excluded_exact_prefixes"] == ["backend/app/evaluation/"]
    assert result["observed_legacy_call_edges"]
    assert {edge["kind"] for edge in result["observed_legacy_call_edges"]} == {
        "legacy_candidate_write",
        "legacy_checkpoint",
        "legacy_runner",
    }
    assert all(
        edge["disposition"] == "shared_allowlist"
        for edge in result["observed_legacy_call_edges"]
    )


def test_static_only_fails_before_database_access_on_new_dual_track_edge(
    tmp_path, capsys
):
    project = tmp_path / "project"
    api = project / "backend" / "app" / "api"
    new_domain = project / "backend" / "app" / "services" / "document_analysis"
    frontend = project / "frontend" / "src"
    api.mkdir(parents=True)
    new_domain.mkdir(parents=True)
    frontend.mkdir(parents=True)
    (api / "dual.py").write_text(
        "def legacy_mode():\n"
        "    return configured_generic_runner(None)  # /document-analysis/word\n",
        encoding="utf-8",
    )
    (new_domain / "writer.py").write_text(
        "from app.services.extraction.candidate_store import CandidateStore\n",
        encoding="utf-8",
    )

    result_code = audit_main(["--static-only", "--project-root", str(project)])
    result = json.loads(capsys.readouterr().out)

    assert result_code == 1
    codes = {item["code"] for item in result["violations"]}
    assert "LEGACY_SYNC_PROTOCOL" in codes
    assert "DUAL_TRACK_SWITCH" in codes
    assert "UNREVIEWED_LEGACY_CALL_EDGE" in codes
    assert "UNREVIEWED_CANDIDATE_STORE_IMPORT" in codes
    assert "NEW_RUN_LEGACY_DEPENDENCY" in codes


def test_unknown_word_mode_and_active_lease_fail_closed(tmp_path):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    _insert_target(engine, tables, storage_root)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with engine.begin() as connection:
        connection.execute(
            tables["jobs"].insert(),
            {
                "id": "00000000-0000-0000-0000-000000000024",
                "source_type": "word",
                "source_config": {},
                "status": "complete",
            },
        )
        connection.execute(
            tables["executions"].insert(),
            {
                "job_id": "00000000-0000-0000-0000-000000000021",
                "run_id": "late-worker",
                "status": "running",
                "lease_expires_at": future,
            },
        )
    manifest = _manifest(database_url, project_root, storage_root)

    result = validate_manifest(manifest, database_url)

    assert result["ok"] is False
    assert result["status"] == "G-C03 blocked"
    source_codes = {issue.get("source_code") for issue in result["issues"]}
    assert "UNKNOWN_WORD_JOB_OWNERSHIP" in source_codes
    assert "ACTIVE_LEGACY_LEASES" in source_codes


@pytest.mark.parametrize("reference_kind", ["confirmed", "foreign_key", "json"])
def test_confirmed_and_shared_objects_never_enter_physical_delete(tmp_path, reference_kind):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    source = _insert_target(engine, tables, storage_root)
    job_id = "00000000-0000-0000-0000-000000000021"
    with engine.begin() as connection:
        if reference_kind == "confirmed":
            connection.execute(
                tables["candidates"].insert(),
                {
                    "id": "candidate-1",
                    "job_id": job_id,
                    "review_status": "confirmed",
                    "payload": {"candidate_id": "candidate-1"},
                },
            )
        elif reference_kind == "foreign_key":
            connection.execute(
                tables["templates"].insert(),
                {
                    "id": "template-1",
                    "default_source_job_id": job_id,
                    "status": "published",
                },
            )
        else:
            connection.execute(
                tables["report_runs"].insert(),
                {
                    "id": "report-1",
                    "source_bundle": {"sources": [{"path": str(source)}]},
                    "review_status": "draft",
                },
            )

    manifest = _manifest(database_url, project_root, storage_root)
    result = validate_manifest(manifest, database_url)

    assert result["ok"] is False
    assert manifest["categories"][
        "published_or_confirmed" if reference_kind == "confirmed" else "shared_or_unknown"
    ]
    assert not manifest["categories"]["unpublished_exclusive"]
    assert all(item["action"] != "physical_delete" for item in manifest["items"])


def test_manifest_and_live_file_hash_mismatches_block_without_mutation(tmp_path):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    source = _insert_target(engine, tables, storage_root)
    manifest = _manifest(database_url, project_root, storage_root)
    source.write_bytes(b"changed-after-freeze")

    result = validate_manifest(manifest, database_url)

    assert result["ok"] is False
    assert "LIVE_INVENTORY_HASH_MISMATCH" in {issue["code"] for issue in result["issues"]}
    with engine.connect() as connection:
        assert connection.execute(select(tables["jobs"])).first() is not None
    assert source.exists()

    manifest["items"][0]["content_sha256"] = "0" * 64
    assert (
        validate_manifest(manifest, database_url)["issues"][0]["code"] == "MANIFEST_HASH_MISMATCH"
    )


def test_default_dry_run_and_execute_approval_gates_are_non_destructive(tmp_path):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    source = _insert_target(engine, tables, storage_root)
    manifest = _manifest(database_url, project_root, storage_root)

    dry_run = validate_manifest(manifest, database_url)
    execute_check = validate_manifest(
        manifest,
        database_url,
        execute=True,
        expected_manifest_sha256=manifest["manifest_sha256"],
        maintenance_window="missing",
    )

    assert dry_run["ok"] is True
    assert dry_run["mode"] == "dry-run"
    assert execute_check["ok"] is False
    assert "MAINTENANCE_WINDOW_MISSING" in {issue["code"] for issue in execute_check["issues"]}
    assert source.exists()
    with engine.connect() as connection:
        assert connection.execute(select(tables["jobs"])).first() is not None


def test_cutover_preflight_cli_checks_approval_without_deleting(
    tmp_path, capsys
):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    source = _insert_target(engine, tables, storage_root)
    manifest = _manifest(database_url, project_root, storage_root)
    identity = _authorize(manifest, datetime.now(timezone.utc))
    manifest_path = tmp_path / "approved-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result_code = cleanup_main(
        [
            "--manifest",
            str(manifest_path),
            "--database-url",
            database_url,
            "--cutover-preflight",
            "--expected-manifest-sha256",
            identity,
            "--maintenance-window",
            "mw-021-test",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert result_code == 0
    assert result["ok"] is True
    assert result["mode"] == "cutover_preflight"
    assert result["destructive"] is False
    assert source.exists()
    with engine.connect() as connection:
        assert connection.execute(select(tables["jobs"])).first() is not None


def test_redeploy_cutover_preflight_fails_before_docker_without_explicit_database(
    tmp_path,
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("DOCUMENT_ANALYSIS_CUTOVER_DATABASE_URL", None)

    result = subprocess.run(
        [
            "bash",
            str(REPOSITORY_ROOT / "scripts" / "redeploy.sh"),
            "--prod",
            "--document-analysis-cutover-preflight",
            "--retirement-manifest",
            str(manifest),
            "--expected-manifest-sha256",
            "0" * 64,
            "--maintenance-window",
            "test-window",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "DOCUMENT_ANALYSIS_CUTOVER_DATABASE_URL" in result.stderr
    assert "docker compose pull" not in result.stdout


def test_approved_temp_cleanup_is_ordered_audited_and_idempotent(tmp_path):
    database_url, project_root, storage_root, engine, tables = _environment(tmp_path)
    source = _insert_target(engine, tables, storage_root)
    job_id = "00000000-0000-0000-0000-000000000021"
    with engine.begin() as connection:
        previous_audit_hash = _seed_audit(connection, tables["audit"])
        connection.execute(
            tables["executions"].insert(),
            {
                "job_id": job_id,
                "run_id": "expired-worker-token",
                "status": "running",
                "lease_expires_at": datetime.now(timezone.utc) - timedelta(days=1),
            },
        )
        connection.execute(
            tables["candidates"].insert(),
            {
                "id": "candidate-1",
                "job_id": job_id,
                "review_status": "pending",
                "payload": {"candidate_id": "candidate-1"},
            },
        )
        connection.execute(
            tables["revisions"].insert(),
            {
                "id": "revision-1",
                "candidate_id": "candidate-1",
                "revision": 1,
                "payload": {"state": "pending"},
            },
        )
    manifest = _manifest(database_url, project_root, storage_root)
    now = datetime.now(timezone.utc)
    identity = _authorize(manifest, now)

    first = execute_cleanup(
        manifest,
        database_url,
        expected_manifest_sha256=identity,
        maintenance_window="mw-021-test",
        now=now,
    )
    second = execute_cleanup(
        manifest,
        database_url,
        expected_manifest_sha256=identity,
        maintenance_window="mw-021-test",
        now=now,
    )

    assert first["ok"] is True
    assert first["status"] == "applied"
    assert first["counts"] == {
        "database_rows_deleted": 4,
        "files_deleted": 1,
        "retained_or_invalidated": 0,
        "failed": 0,
    }
    assert second["status"] == "already_applied"
    assert not source.exists()
    with engine.connect() as connection:
        assert connection.execute(select(tables["jobs"])).first() is None
        stale_write = connection.execute(
            update(tables["executions"])
            .where(tables["executions"].c.run_id == "expired-worker-token")
            .values(status="complete")
        )
        assert stale_write.rowcount == 0
        logs = (
            connection.execute(
                select(tables["audit"]).where(
                    tables["audit"].c.action == "word_recognition.retirement.cleanup"
                )
            )
            .mappings()
            .all()
        )
    assert len(logs) == 1
    log = logs[0]
    assert log["details"]["manifest_sha256"] == identity
    assert log["seq"] == 2
    assert log["prev_hash"] == previous_audit_hash
    assert log["entry_hash"]


def test_cli_has_no_force_bypass(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cleanup_main(["--manifest", str(tmp_path / "manifest.json"), "--force"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --force" in capsys.readouterr().err
