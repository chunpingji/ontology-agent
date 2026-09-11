"""Application boundary for the document-analysis runs v1 contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisControlOperation,
    DocumentAnalysisRun,
)
from app.services.document_analysis.artifact_store import (
    RunArtifactStorage,
    SourceArtifactError,
    stage_upload,
)
from app.services.document_analysis.public_projection import (
    PUBLIC_TO_INTERNAL_PROJECTION,
    public_graph_payload,
    public_ranking_payload,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    HeadConflict,
    IdempotencyConflict,
    InvalidRunState,
    RunDeleted,
    RunNotFound,
    content_hash,
)
from app.services.document_analysis.state_artifacts import decode_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    CONTRACT_VERSION,
    OntologySnapshot,
    SubjectRef,
)
from app.services.extraction.ontology_guided.ontology_plan import (
    compile_local_menu,
    ontology_snapshot_from_engine,
)

PUBLIC_STATUS = {
    "queued": "queued",
    "running": "running",
    "pausing": "running",
    "paused": "paused",
    "finished": "finished",
    "failed": "retryable_failure",
    "blocked_dependency": "blocked_dependency",
    "cancelled": "cancelled",
}
PUBLIC_STAGE = {
    "ingest": "accepted",
    "parse": "parsing",
    "metadata": "preparing_metadata",
    "recognition": "extracting",
    "finalize": "complete",
}
ARTIFACT_KINDS = ("source", "structure", "metadata", "graph")
EVENT_TYPES = {"run_state", "progress", "artifact", "warning", "error", "tombstone"}
METADATA_MODES = {"cached_summary", "generate_summary", "structure_only"}
WRITE_ROLES = {"senior_analyst"}


class DocumentAnalysisError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.current_revision = current_revision


def _links(run_id: UUID | str) -> dict[str, str]:
    base = f"/api/document-analysis/runs/{run_id}"
    return {
        "self": base,
        "metadata": f"{base}/metadata",
        "graph": f"{base}/graph",
        "source": f"{base}/source",
        "events": f"{base}/events",
    }


def _absolute_iri(value: str) -> bool:
    if not value or any(char.isspace() for char in value):
        return False
    parsed = urlparse(value)
    return bool(parsed.scheme and (parsed.netloc or parsed.scheme == "urn"))


def _request_hash(
    *, filename: str, document_hash: str, root_class_iri: str, metadata_mode: str,
    origin: dict | None = None,
) -> str:
    return content_hash(
        {
            "contract_version": CONTRACT_VERSION,
            "document_hash": document_hash,
            "filename": filename,
            "root_class_iri": root_class_iri,
            "metadata_mode": metadata_mode,
            "scope_mode": "document_graph",
            "focus_path": [],
            **({"origin": origin} if origin is not None else {}),
        }
    )


def _status(run: DocumentAnalysisRun) -> str:
    if run.deletion_state in {"requested", "deleting"}:
        return "deleting"
    if run.deletion_state == "deleted":
        return "deleted"
    return PUBLIC_STATUS.get(run.execution_status, "retryable_failure")


def _stage(run: DocumentAnalysisRun) -> str:
    return PUBLIC_STAGE.get(run.stage, "extracting")


def _availability(value: str | None) -> str:
    if value in {"ready", "partial", "failed"}:
        return value
    return "pending"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _available_actions(run: DocumentAnalysisRun, role: str | None) -> list[str]:
    if role not in WRITE_ROLES or run.deletion_state != "none":
        return []
    if run.execution_status == "pausing":
        return ["cancel", "delete"]
    status = _status(run)
    if status in {"queued", "running"}:
        return ["pause", "cancel", "delete"]
    if status in {"paused", "retryable_failure"}:
        actions = ["resume", "cancel", "delete"]
        if run.expires_at is None or _aware(run.expires_at) > datetime.now(UTC):
            actions.append(
                "ranking_budget_disable" if run.ranking_budget_enabled else "ranking_budget_enable"
            )
        return actions
    if status == "blocked_dependency":
        return ["cancel", "delete"]
    if status in {"finished", "cancelled"}:
        return ["delete"]
    return []


def _progress(run: DocumentAnalysisRun) -> dict[str, Any]:
    source = dict(run.progress or {})
    attempted = int(source.get("tasks_attempted", 0))
    supported = int(source.get("supported", 0))
    unsupported = int(source.get("unsupported", 0))
    undetermined = int(source.get("undetermined", 0))
    phase_counts = source.get("phase_counts") or {}
    return {
        "tasks_attempted": attempted,
        "model_calls": int(source.get("model_calls", 0)),
        "model_calls_reserved": int(source.get("model_calls_reserved", 0)),
        "model_calls_unresolved": int(source.get("model_calls_unresolved", 0)),
        "records_planned": int(source.get("records_planned", 0)),
        "records_examined": int(source.get("records_examined", 0)),
        "records_incomplete": int(source.get("records_incomplete", 0)),
        "records_unattempted": int(source.get("records_unattempted", 0)),
        "phase_counts": {
            "phase1": int(phase_counts.get("phase1", 0)),
            "phase2": int(phase_counts.get("phase2", 0)),
        },
        "decisions": {
            "supported": supported,
            "unsupported": unsupported,
            "undetermined": undetermined,
            "not_checked": max(0, attempted - supported - unsupported - undetermined),
        },
        "pending_frontiers": int(source.get("pending_frontiers", 0)),
        "stop_reason": (
            None if run.execution_status in {"queued", "running", "pausing"}
            else run.stop_reason or source.get("stop_reason")
        ),
        "contract_version": CONTRACT_VERSION,
        "event_head": run.event_head,
        "artifact_revision": run.artifact_revision,
    }


def _run_error(run: DocumentAnalysisRun) -> dict[str, Any] | None:
    if not run.error:
        return None
    return {
        "code": str(run.error.get("code") or "ANALYSIS_FAILED"),
        "stage": _stage(run),
        "retryable": bool(run.error.get("retryable", False)),
        "safe_detail": str(
            run.error.get("safe_detail") or run.error.get("message") or "文档分析未完成"
        ),
        "occurred_at": _aware(run.updated_at),
    }


class DocumentAnalysisApplication:
    def __init__(self, db: Session, *, ontology_engine: object) -> None:
        self.db = db
        self.engine = ontology_engine
        self.store = DocumentAnalysisRunStore(db)
        self.storage = RunArtifactStorage(settings.document_analysis_storage_dir)

    async def create_run(
        self,
        *,
        owner_id: str,
        file: UploadFile,
        root_class_iri: str,
        request_key: str,
        metadata_mode: str,
        origin: dict | None = None,
    ) -> tuple[DocumentAnalysisRun, bool]:
        if not request_key or len(request_key) > 200:
            raise DocumentAnalysisError(
                "INVALID_REQUEST", "request_key 长度必须为 1—200 字符", status_code=400
            )
        if metadata_mode not in METADATA_MODES:
            raise DocumentAnalysisError("INVALID_REQUEST", "metadata_mode 非法", status_code=400)
        if not _absolute_iri(root_class_iri):
            raise DocumentAnalysisError(
                "INVALID_ROOT_CLASS", "root_class_iri 必须是完整 IRI", status_code=422
            )

        run_id = uuid4()
        try:
            staged = await stage_upload(
                file,
                storage_root=settings.document_analysis_storage_dir,
                run_id=run_id,
                max_upload_bytes=settings.document_analysis_max_upload_bytes,
            )
        except SourceArtifactError as exc:
            raise DocumentAnalysisError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc

        request_digest = _request_hash(
            filename=staged.filename,
            document_hash=staged.document_hash,
            root_class_iri=root_class_iri,
            metadata_mode=metadata_mode,
            origin=origin,
        )
        existing = self.db.scalar(
            select(DocumentAnalysisRun).where(
                DocumentAnalysisRun.owner_id == owner_id,
                DocumentAnalysisRun.request_key == request_key,
            )
        )
        if existing is not None:
            self.storage.discard_run(run_id)
            if existing.request_hash != request_digest:
                raise DocumentAnalysisError(
                    "IDEMPOTENCY_CONFLICT",
                    "相同 request_key 已用于不同输入",
                    status_code=409,
                    current_revision=existing.revision,
                )
            if existing.deletion_state == "deleted":
                raise DocumentAnalysisError("RUN_DELETED", "该运行已删除", status_code=410)
            return existing, False

        try:
            ontology = ontology_snapshot_from_engine(self.engine, root_class_iri)
        except Exception as exc:
            self.storage.discard_run(run_id)
            raise DocumentAnalysisError(
                "ONTOLOGY_UNAVAILABLE",
                "当前本体不可用，未创建分析运行",
                status_code=503,
                retryable=True,
            ) from exc
        root = ontology.classes.get(root_class_iri)
        if root is None:
            self.storage.discard_run(run_id)
            raise DocumentAnalysisError(
                "INVALID_ROOT_CLASS",
                "所选类型不在当前本体或不符合根类型策略",
                status_code=422,
            )
        provisional_fingerprint = evidence_hash(
            {
                "request_hash": request_digest,
                "ontology_hash": ontology.ontology_hash,
                "root_class_iri": root_class_iri,
                "metadata_mode": metadata_mode,
            }
        )
        try:
            run, created = self.store.create_run_with_source(
                owner_id=owner_id,
                request_key=request_key,
                filename=staged.filename,
                document_hash=staged.document_hash,
                root_class_iri=root_class_iri,
                source_artifact_id=f"source:{run_id}",
                source_storage_uri=staged.storage_uri,
                source_media_type=staged.media_type,
                source_size_bytes=staged.size_bytes,
                request_hash=request_digest,
                root_class_label=root.label,
                ontology_snapshot_hash=ontology.ontology_hash,
                metadata_mode=metadata_mode,
                scope_mode="document_graph",
                ranking_budget_enabled=settings.semantic_ranking_budget_enabled,
                provisional_fingerprint=provisional_fingerprint,
                recognition_run_id=run_id,
                progress={},
                source_payload={
                    "filename": staged.filename,
                    "document_hash": staged.document_hash,
                    "performance_policy": {
                        "state_storage_version": 2,
                        "frontier_version": 2,
                        "recognition_inflight": 1,
                        "template_interleaving": (
                            settings.document_analysis_template_interleaving
                            or settings.document_analysis_evidence_repair_enabled
                        ),
                        **({"evidence_repair": "evidence-repair-v1",
                            "incremental_performance": "incremental-performance-v1",
                            "state_storage_version": 3,
                            "state_baseline_interval": 32,
                            "semantic_expansion": "bounded-semantic-v1",
                            "process_granularity": "whole-method-field-v1",
                            "attribute_priority": "source-field-priority-v1",
                            "heuristic_policy": "heuristic-first-v3",
                            "field_bindings": "ir-field-bindings-v1",
                            "owner_binding": "source-owned-binding-v2",
                            "scope_protocol": "source-quoted-scope-v1",
                            "evidence_work": "evidence-work-v2",
                            "literal_quotes": "source-integer-quotes-v2",
                            "proof_menu": "proof-menu-v1", "identity": "physical-mention-v1",
                            "model_call_state_version": 2, "max_lineage_calls": 8}
                           if settings.document_analysis_evidence_repair_enabled else {}),
                    } if (settings.document_analysis_performance_enabled
                          or settings.document_analysis_evidence_repair_enabled) else {},
                    **({"origin": origin} if origin is not None else {}),
                },
                ontology_artifact_id=ontology.snapshot_id,
                ontology_payload=ontology.model_dump(mode="json"),
                ontology_is_exclusive=False,
                queued_event_payload={
                    "contract_version": CONTRACT_VERSION,
                    "recognition_run_id": str(run_id),
                    "run_revision": 1,
                    "event_head": 1,
                    "artifact_revision": 1,
                    "status": "queued",
                    "stage": "accepted",
                },
            )
            self.db.commit()
        except (IdempotencyConflict, RunDeleted) as exc:
            self.db.rollback()
            self.storage.discard_run(run_id)
            raise self.map_store_error(exc) from exc
        except Exception:
            self.db.rollback()
            self.storage.discard_run(run_id)
            raise
        if not created:
            self.storage.discard_run(run_id)
        return run, created

    def list_runs(self, owner_id: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        runs, has_more = self.store.list_owned(owner_id, limit=limit, offset=offset)
        return {
            "contract_version": CONTRACT_VERSION,
            "items": [
                {
                    "contract_version": CONTRACT_VERSION,
                    "recognition_run_id": run.recognition_run_id,
                    "run_revision": run.revision,
                    "event_head": run.event_head,
                    "artifact_revision": run.artifact_revision,
                    "status": _status(run),
                    "stage": _stage(run),
                    "input": {
                        "filename": run.filename,
                        "root_class_iri": run.root_class_iri,
                        "root_class_label": run.root_class_label,
                        "metadata_mode": run.metadata_mode,
                    },
                    "created_at": _aware(run.created_at),
                    "expires_at": _aware(run.expires_at),
                }
                for run in runs
            ],
            "has_more": has_more,
        }

    def get_run(self, run_id: UUID | str, owner_id: str) -> DocumentAnalysisRun:
        try:
            return self.store.get_owned(run_id, owner_id)
        except (RunNotFound, RunDeleted) as exc:
            raise self.map_store_error(exc) from exc

    def create_response(
        self, run: DocumentAnalysisRun, *, idempotent_replay: bool
    ) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": run.recognition_run_id,
            "run_revision": run.revision,
            "event_head": run.event_head,
            "artifact_revision": run.artifact_revision,
            "status": _status(run),
            "stage": _stage(run),
            "idempotent_replay": idempotent_replay,
            "input": {
                "filename": run.filename,
                "root_class_iri": run.root_class_iri,
                "root_class_label": run.root_class_label,
                "metadata_mode": run.metadata_mode,
            },
            "created_at": _aware(run.created_at),
            "expires_at": _aware(run.expires_at),
            "links": _links(run.recognition_run_id),
        }

    def status_response(self, run: DocumentAnalysisRun, *, role: str | None) -> dict[str, Any]:
        manifest = dict(run.artifact_manifest or {})
        ontology_entry = manifest.get("ontology_snapshot") or {}
        return {
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": run.recognition_run_id,
            "run_revision": run.revision,
            "event_head": run.event_head,
            "artifact_revision": run.artifact_revision,
            "status": _status(run),
            "stage": _stage(run),
            "ranking_budget_enabled": run.ranking_budget_enabled,
            "input": {
                "filename": run.filename,
                "root_class_iri": run.root_class_iri,
                "root_class_label": run.root_class_label,
                "metadata_mode": run.metadata_mode,
                "scope_mode": run.scope_mode,
            },
            "identities": {
                "analysis_id": run.analysis_id,
                "ontology_snapshot_id": ontology_entry.get("artifact_id"),
                "metadata_snapshot_id": run.metadata_snapshot_id,
                "graph_snapshot_id": run.graph_snapshot_id,
                "structure_snapshot_id": (manifest.get("source_header") or {}).get("artifact_id")
                or (manifest.get("structure") or {}).get("artifact_id"),
                "ranking_summary_id": (manifest.get("ranking_summary") or {}).get("artifact_id"),
                "fingerprint_status": "frozen" if run.run_fingerprint else "provisional",
            },
            "artifacts": {
                kind: _availability((manifest.get(kind) or {}).get("status"))
                for kind in ARTIFACT_KINDS
            },
            "progress": _progress(run),
            "error": _run_error(run),
            "available_actions": _available_actions(run, role),
            "created_at": _aware(run.created_at),
            "started_at": _aware(run.started_at),
            "paused_at": _aware(run.paused_at),
            "finished_at": _aware(run.finished_at),
            "expires_at": _aware(run.expires_at),
        }

    def _artifact_payload(
        self, run: DocumentAnalysisRun, kind: str
    ) -> tuple[dict[str, Any], str] | None:
        self.assert_artifacts_readable(run)
        ref = self.store.get_artifact(run.recognition_run_id, run.owner_id, kind)
        if ref is None:
            return None
        artifact = self.db.get(DocumentAnalysisArtifact, ref.artifact_id)
        if artifact is None or artifact.payload is None:
            return None
        if artifact.content_hash != ref.content_hash:
            raise DocumentAnalysisError(
                "INVALID_REQUEST",
                "运行产物水位不一致",
                status_code=409,
                retryable=True,
                current_revision=run.revision,
            )
        return decode_state(self.store, run, dict(artifact.payload)), _availability(ref.status)

    @staticmethod
    def assert_artifacts_readable(run: DocumentAnalysisRun) -> None:
        if run.expires_at is not None and _aware(run.expires_at) <= datetime.now(UTC):
            raise DocumentAnalysisError("RUN_EXPIRED", "运行已过期", status_code=410)
        if run.deletion_state != "none":
            raise DocumentAnalysisError("RUN_DELETED", "运行正在删除或已删除", status_code=410)

    def metadata_response(self, run: DocumentAnalysisRun) -> dict[str, Any]:
        committed = self._artifact_payload(run, "metadata")
        if committed is None:
            committed = self._artifact_payload(run, "structure")
            if committed is None:
                return {
                    "contract_version": CONTRACT_VERSION,
                    "recognition_run_id": run.recognition_run_id,
                    "run_revision": run.revision,
                    "event_head": run.event_head,
                    "artifact_revision": run.artifact_revision,
                    "availability": "pending",
                    "stage": _stage(run),
                    "retry_after_ms": 1000,
                    "metadata": None,
                    "analysis": None,
                    "metadata_snapshot": None,
                    "filename": None,
                    "content": None,
                    "section_tree": None,
                    "pagination": None,
                    "warnings": [],
                    "error": None,
                }
            payload, _status_value = committed
            availability = "partial"
        else:
            payload, availability = committed
        ir = payload["analysis"]
        metadata = payload.get("metadata_snapshot")
        return {
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": run.recognition_run_id,
            "run_revision": run.revision,
            "event_head": run.event_head,
            "artifact_revision": run.artifact_revision,
            "availability": availability,
            "stage": _stage(run),
            "retry_after_ms": None,
            "metadata": None,
            "analysis": {
                "analysis_id": ir["analysis_id"],
                "document_hash": ir["document_hash"],
                "structure_hash": ir["structure_hash"],
                "parser_version": str(ir["parser_version"]),
                "structure_policy_version": ir["structure_policy_version"],
            },
            "metadata_snapshot": (
                {
                    "snapshot_id": metadata["snapshot_id"],
                    "generation_source": metadata["generation_source"],
                    "summary_version": metadata.get("summary_version"),
                    "summary_model_identity": metadata.get("summary_model_identity"),
                    "dependency_hash": metadata["dependency_hash"],
                    "frozen": True,
                }
                if metadata
                else None
            ),
            "filename": payload["filename"],
            "content": payload["content"],
            "section_tree": payload["section_tree"],
            "pagination": payload["pagination"],
            "warnings": list(payload.get("warnings") or []),
            "error": None,
        }

    def graph_response(self, run: DocumentAnalysisRun, *, projection: str) -> dict[str, Any]:
        if projection not in PUBLIC_TO_INTERNAL_PROJECTION:
            raise DocumentAnalysisError("INVALID_REQUEST", "未知 graph projection", status_code=400)
        compact = self._artifact_payload(run, "public_graph")
        committed = compact or self._artifact_payload(run, "graph")
        summary = self._artifact_payload(run, "ranking_summary")
        ranking_artifact = None if summary else self._artifact_payload(run, "ranking_state")
        ranking_state = ranking_artifact[0] if ranking_artifact else {}
        if committed is None:
            return {
                "contract_version": CONTRACT_VERSION,
                "recognition_run_id": run.recognition_run_id,
                "run_revision": run.revision,
                "event_head": run.event_head,
                "artifact_revision": run.artifact_revision,
                "availability": "pending",
                "projection": projection,
                "graph_snapshot": None,
                "entities": [],
                "properties": [],
                "relationships": [],
                "invalidated_refs": [],
                "ranking": {
                    **(summary[0] if summary else public_ranking_payload(ranking_state)),
                    "budget_enabled": run.ranking_budget_enabled,
                },
                "coverage": {
                    "subjects": [],
                    "records_planned": 0,
                    "records_examined": 0,
                    "records_incomplete": 0,
                    "records_unattempted": 0,
                    "phase2_started": False,
                    "pending_frontiers": 0,
                    "stop_reason": None,
                },
                "unresolved": {
                    "unsupported": 0,
                    "undetermined": 0,
                    "not_checked": 0,
                    "unassociated_entities": 0,
                },
                "error": None,
            }
        payload, availability = committed
        if ranking_artifact:
            payload["ranking_state"] = ranking_state
        response = public_graph_payload(
            recognition_run_id=str(run.recognition_run_id),
            run_revision=run.revision,
            event_head=run.event_head,
            artifact_revision=run.artifact_revision,
            availability=availability,
            projection=projection,
            stored_payload=payload,
        )
        response["ranking"]["budget_enabled"] = run.ranking_budget_enabled
        if summary:
            response["ranking"] = {**summary[0], "budget_enabled": run.ranking_budget_enabled}
        # A checkpoint graph is immutable replay evidence. Its last batch can
        # predate a pause or resume; overlay only the current execution cause,
        # leaving its coverage counts and per-slot results intact.
        if run.execution_status in {"queued", "running", "pausing"}:
            response["coverage"]["stop_reason"] = None
        elif run.stop_reason:
            response["coverage"]["stop_reason"] = (
                "service_failure" if run.stop_reason == "recognition_model_not_configured"
                else run.stop_reason
            )
        response["error"] = None
        if compact:
            menus = payload.get("predicate_menus") or {}
            for entity in response["entities"]:
                if entity["entity_id"] in menus:
                    entity["predicate_menu"] = menus[entity["entity_id"]]
            return response
        frozen_ontology = self._artifact_payload(run, "ontology_snapshot")
        if frozen_ontology:
            ontology = OntologySnapshot.model_validate(frozen_ontology[0])
            for entity in response["entities"]:
                if entity["class_iri"] not in ontology.classes:
                    # External range classes may lack a local definition. An
                    # unknown menu is distinct from an explicitly empty menu.
                    continue
                menu = compile_local_menu(ontology, SubjectRef(
                    entity_id=entity["entity_id"], revision=entity["revision"],
                    class_iri=entity["class_iri"],
                    is_document_root=entity["seed_origin"] == "user_selected",
                ))
                entity["predicate_menu"] = [
                    {"predicate_iri": item.iri, "predicate_label": item.label, "kind": item.kind}
                    for item in [*menu.relationships, *menu.properties]
                ]
        return response

    def ranking_summary_response(self, run: DocumentAnalysisRun) -> dict[str, Any]:
        summary = self._artifact_payload(run, "ranking_summary")
        if summary:
            result = summary[0]
        else:
            legacy = self._artifact_payload(run, "ranking_state")
            result = public_ranking_payload(legacy[0] if legacy else {})
        return {**result, "budget_enabled": run.ranking_budget_enabled}

    def source_selection_response(
        self, run: DocumentAnalysisRun, *, selection_ref: str
    ) -> dict[str, Any]:
        compact = self._artifact_payload(run, "source_selections")
        if not compact:
            legacy = self.source_response(run, selection_ref=selection_ref)
            return {
                key: value for key, value in legacy.items() if key not in {"filename", "content"}
            }
        payload = compact[0]
        registered = payload["selection_registry"].get(selection_ref)
        if registered is None:
            raise DocumentAnalysisError("RUN_NOT_FOUND", "来源定位不存在", status_code=404)
        return {
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": run.recognition_run_id,
            **{key: payload[key] for key in ("analysis_id", "document_hash", "structure_hash")},
            "selection": {key: value for key, value in registered.items() if key != "anchors"},
            "anchors": list(registered.get("anchors") or []),
        }

    def source_response(
        self, run: DocumentAnalysisRun, *, selection_ref: str | None
    ) -> dict[str, Any]:
        committed = self._artifact_payload(run, "metadata") or self._artifact_payload(
            run, "structure"
        )
        if committed is None:
            raise DocumentAnalysisError(
                "INVALID_REQUEST", "原文预览尚未就绪", status_code=409, retryable=True
            )
        payload, _availability_value = committed
        ir = payload["analysis"]
        selection = None
        anchors: list[dict[str, Any]] = []
        if selection_ref is not None:
            graph = self._artifact_payload(run, "source_selections") or self._artifact_payload(
                run, "graph"
            )
            registry = (graph[0].get("selection_registry") if graph else None) or {}
            registered = registry.get(selection_ref)
            if registered is None:
                raise DocumentAnalysisError("RUN_NOT_FOUND", "来源定位不存在", status_code=404)
            selection = {key: value for key, value in registered.items() if key != "anchors"}
            anchors = list(registered.get("anchors") or [])
        return {
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": run.recognition_run_id,
            "analysis_id": ir["analysis_id"],
            "document_hash": ir["document_hash"],
            "structure_hash": ir["structure_hash"],
            "filename": payload["filename"],
            "content": payload["content"],
            "selection": selection,
            "anchors": anchors,
        }

    def events_response(
        self, run: DocumentAnalysisRun, *, after_sequence: int
    ) -> list[tuple[int, str, dict[str, Any]]]:
        rows = self.store.list_events(
            run.recognition_run_id,
            run.owner_id,
            after_sequence=after_sequence,
        )
        result: list[tuple[int, str, dict[str, Any]]] = []
        for row in rows:
            raw = dict(row.payload or {})
            event_type = row.event_type if row.event_type in EVENT_TYPES else "progress"
            status = raw.get("status")
            if status not in {
                "queued",
                "running",
                "finished",
                "retryable_failure",
                "blocked_dependency",
                "paused",
                "cancelled",
                "deleting",
                "deleted",
                "expired",
            }:
                status = _status(run)
            stage = raw.get("stage")
            if stage in PUBLIC_STAGE:
                stage = PUBLIC_STAGE[stage]
            if stage not in {
                "accepted",
                "storing_source",
                "converting",
                "parsing",
                "preparing_metadata",
                "freezing_inputs",
                "planning",
                "extracting",
                "projecting",
                "finalizing",
                "complete",
            }:
                stage = _stage(run)
            data = {
                "contract_version": CONTRACT_VERSION,
                "recognition_run_id": run.recognition_run_id,
                "run_revision": int(raw.get("run_revision", run.revision)),
                "event_head": row.sequence,
                "artifact_revision": int(raw.get("artifact_revision", run.artifact_revision)),
                "event_id": f"event:{run.recognition_run_id}:{row.sequence}",
                "status": status,
                "stage": stage,
                "artifact_kind": None,
                "availability": None,
            }
            if event_type == "artifact":
                kind = raw.get("artifact_kind")
                availability = _availability(raw.get("availability"))
                if kind in ARTIFACT_KINDS:
                    data["artifact_kind"] = kind
                    data["availability"] = availability
                else:
                    event_type = "progress"
            result.append((row.sequence, event_type, data))
        return result

    def original_source(self, run: DocumentAnalysisRun) -> tuple[Path, str, str, str]:
        ref = self.store.get_artifact(run.recognition_run_id, run.owner_id, "source")
        if ref is None:
            raise DocumentAnalysisError("RUN_EXPIRED", "运行原文已清理", status_code=410)
        artifact = self.db.get(DocumentAnalysisArtifact, ref.artifact_id)
        if artifact is None or not artifact.storage_uri:
            raise DocumentAnalysisError("RUN_EXPIRED", "运行原文已清理", status_code=410)
        try:
            path = self.storage.resolve(run.recognition_run_id, artifact.storage_uri)
        except SourceArtifactError as exc:
            raise DocumentAnalysisError(exc.code, exc.message, status_code=exc.status_code) from exc
        if not path.is_file():
            raise DocumentAnalysisError("RUN_EXPIRED", "运行原文已清理", status_code=410)
        return (
            path,
            artifact.media_type or "application/octet-stream",
            run.filename,
            run.document_hash,
        )

    def _deleted_delete_response(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
        request_key: str,
        role: str,
    ) -> dict[str, Any] | None:
        """Return a safe tombstone receipt without exposing a foreign run."""

        try:
            self.store.get_tombstone(recognition_run_id, owner_id)
        except RunNotFound:
            # No marker may mean a live run.  A foreign marker deliberately
            # takes the same path and the following live lookup will still 404.
            return None
        if role not in WRITE_ROLES:
            raise DocumentAnalysisError("ROLE_FORBIDDEN", "当前角色无运行写权限", status_code=403)
        try:
            payload = self.store.replay_tombstone_delete_result(
                recognition_run_id,
                owner_id,
                expected_revision=expected_revision,
                request_key=request_key,
            )
        except (RunNotFound, RunDeleted, IdempotencyConflict, InvalidRunState) as exc:
            raise self.map_store_error(exc) from exc
        if payload is None:  # The authenticated marker cannot disappear normally.
            raise DocumentAnalysisError("RUN_STATE_CONFLICT", "删除回执已不可用", status_code=409)
        return payload

    def delete_control(
        self,
        recognition_run_id: UUID | str,
        owner_id: str,
        *,
        expected_revision: int,
        request_key: str,
        role: str,
    ) -> tuple[DocumentAnalysisRun | None, dict[str, Any], bool]:
        """Request DELETE or replay its original 202 after physical cleanup."""

        deleted_payload = self._deleted_delete_response(
            recognition_run_id,
            owner_id,
            expected_revision=expected_revision,
            request_key=request_key,
            role=role,
        )
        if deleted_payload is not None:
            return None, deleted_payload, True

        try:
            run = self.get_run(recognition_run_id, owner_id)
            updated, replay = self.control(
                run,
                action="delete",
                expected_revision=expected_revision,
                request_key=request_key,
                reason=None,
                role=role,
            )
            return (
                updated,
                self.control_response(
                    updated,
                    action="delete",
                    role=role,
                    request_key=request_key,
                ),
                replay,
            )
        except DocumentAnalysisError as exc:
            # Cleanup may replace the run between the first tombstone lookup
            # and the live-run CAS.  Recheck once so that exact retries retain
            # their idempotency guarantee across that boundary.
            if exc.code != "RUN_DELETED":
                raise
            deleted_payload = self._deleted_delete_response(
                recognition_run_id,
                owner_id,
                expected_revision=expected_revision,
                request_key=request_key,
                role=role,
            )
            if deleted_payload is None:
                raise
            return None, deleted_payload, True

    def control(
        self,
        run: DocumentAnalysisRun,
        *,
        action: str,
        expected_revision: int,
        request_key: str,
        reason: str | None,
        role: str,
    ) -> tuple[DocumentAnalysisRun, bool]:
        if role not in WRITE_ROLES:
            raise DocumentAnalysisError("ROLE_FORBIDDEN", "当前角色无运行写权限", status_code=403)
        previous_ranking_budget_enabled = run.ranking_budget_enabled
        try:
            outcome = self.store.request_control(
                run.recognition_run_id,
                run.owner_id,
                action=action,
                expected_revision=expected_revision,
                request_key=request_key,
                reason=reason,
                expires_at=(
                    datetime.now(UTC) + timedelta(days=settings.document_analysis_retention_days)
                    if action in {"pause", "cancel"}
                    else None
                ),
                include_receipt=True,
            )
            updated, receipt, replay = outcome
            if receipt is None:
                raise InvalidRunState("idempotent control operation has no receipt")
            if not replay:
                public_status = _status(updated)
                event_type = "tombstone" if action == "delete" else "run_state"
                self.store.append_owner_event(
                    updated.recognition_run_id,
                    updated.owner_id,
                    expected_head=updated.event_head,
                    event_key=f"control:{action}:{request_key}",
                    event_type=event_type,
                    payload={
                        "contract_version": CONTRACT_VERSION,
                        "recognition_run_id": str(updated.recognition_run_id),
                        "run_revision": updated.revision + 1,
                        "event_head": updated.event_head + 1,
                        "artifact_revision": updated.artifact_revision,
                        "status": public_status,
                        "stage": _stage(updated),
                        **({
                            "action": action,
                            "actor": run.owner_id,
                            "actor_role": role,
                            "reason": reason,
                            "ranking_budget_enabled": updated.ranking_budget_enabled,
                            "previous_ranking_budget_enabled": previous_ranking_budget_enabled,
                            "control_version": updated.control_version,
                        } if action in {"ranking_budget_enable", "ranking_budget_disable"} else {}),
                    },
                )
            final_run = self.store.get_owned(
                updated.recognition_run_id, updated.owner_id, include_deleted=True
            )
            if replay:
                self.store.replay_control_result(receipt)
            else:
                response_payload = self.control_response(final_run, action=action, role=role)
                self.store.freeze_control_result(
                    receipt,
                    result_revision=final_run.revision,
                    payload=response_payload,
                )
            self.db.commit()
            return final_run, replay
        except (HeadConflict, IdempotencyConflict, InvalidRunState, RunDeleted) as exc:
            self.db.rollback()
            raise self.map_store_error(exc) from exc

    def control_response(
        self,
        run: DocumentAnalysisRun,
        *,
        action: str,
        role: str,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        if request_key is not None:
            receipt = self.db.get(
                DocumentAnalysisControlOperation,
                (run.recognition_run_id, action, request_key),
            )
            if receipt is None:
                raise DocumentAnalysisError(
                    "RUN_STATE_CONFLICT",
                    "控制操作缺少可回放结果",
                    status_code=409,
                )
            try:
                return self.store.replay_control_result(receipt)
            except InvalidRunState as exc:
                raise self.map_store_error(exc) from exc
        return {
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": str(run.recognition_run_id),
            "run_revision": run.revision,
            "event_head": run.event_head,
            "artifact_revision": run.artifact_revision,
            "status": _status(run),
            "stage": _stage(run),
            "operation": action,
            "ranking_budget_enabled": run.ranking_budget_enabled,
            "operation_status": "accepted",
            "available_actions": _available_actions(run, role),
        }

    @staticmethod
    def map_store_error(exc: Exception) -> DocumentAnalysisError:
        if isinstance(exc, RunNotFound):
            return DocumentAnalysisError("RUN_NOT_FOUND", "运行不存在", status_code=404)
        if isinstance(exc, RunDeleted):
            if getattr(exc, "details", {}).get("final_state") == "expired":
                return DocumentAnalysisError("RUN_EXPIRED", "运行已过期", status_code=410)
            return DocumentAnalysisError("RUN_DELETED", "运行已删除", status_code=410)
        if isinstance(exc, IdempotencyConflict):
            return DocumentAnalysisError(
                "IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不同", status_code=409
            )
        if isinstance(exc, HeadConflict):
            actual = exc.details.get("actual") if hasattr(exc, "details") else None
            return DocumentAnalysisError(
                "RUN_REVISION_CONFLICT",
                "运行状态已变化，请刷新后重试",
                status_code=409,
                retryable=True,
                current_revision=actual,
            )
        if isinstance(exc, InvalidRunState):
            return DocumentAnalysisError(
                "RUN_STATE_CONFLICT", "当前运行状态不允许该操作", status_code=409
            )
        return DocumentAnalysisError("INVALID_REQUEST", str(exc), status_code=400)


def weak_etag(run: DocumentAnalysisRun) -> str:
    return (
        f'W/"run:{run.recognition_run_id}:revision:{run.revision}:artifact:{run.artifact_revision}"'
    )


def graph_etag(run: DocumentAnalysisRun, projection: str) -> str:
    manifest = run.artifact_manifest or {}
    digest = content_hash({
        "projection": projection, "graph": run.graph_snapshot_id,
        "summary": manifest.get("ranking_summary"), "status": _status(run),
        "stop_reason": run.stop_reason, "budget_enabled": run.ranking_budget_enabled,
        "revision": run.revision, "artifact_revision": run.artifact_revision,
    })
    return f'W/"graph:{run.recognition_run_id}:{digest}"'
