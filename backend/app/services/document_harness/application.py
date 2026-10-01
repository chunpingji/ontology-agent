"""Creation boundary for the independent document harness."""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.services.document_analysis.artifact_store import (
    RunArtifactStorage,
    SourceArtifactError,
    stage_upload,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    IdempotencyConflict,
    RunDeleted,
    content_hash,
)

ENGINE = "document-harness-v2"


class HarnessError(RuntimeError):
    def __init__(self, code, message, *, status_code=409, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def source_payload(db, run):
    source = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    return source.payload or {} if source else {}


def is_harness_run(db, run):
    return source_payload(db, run).get("engine") == ENGINE


def is_template_run(db, run):
    return bool((source_payload(db, run).get("origin") or {}).get("template_id"))


def require_harness(db, run):
    if not is_harness_run(db, run):
        raise HarnessError("ENGINE_NOT_APPLICABLE", "该运行不属于独立文档 Harness，请新建运行")


def require_current_flow(db, run):
    from .protocols import PROTOCOL

    require_harness(db, run)
    policy = source_payload(db, run).get("policy", {})
    execution = policy.get("execution_policy", {})
    if (policy.get("protocol") != PROTOCOL or execution.get("flow") != "local_reading"
            or type(execution.get("reading_concurrency")) is not int
            or execution["reading_concurrency"] not in (1, 2)):
        raise HarnessError("HARNESS_NEW_RUN_REQUIRED", "该运行使用旧执行策略，请新建运行")


async def create_run(
    db, engine, *, owner_id, file, root_class_iri, request_key, metadata_mode, origin=None,
):
    """Freeze only this engine's inputs; never inherit a previous extraction policy."""
    if not request_key or len(request_key) > 200:
        raise HarnessError("INVALID_REQUEST", "request_key 长度必须为 1—200 字符", status_code=400)
    iri = urlparse(root_class_iri)
    if (not iri.scheme or not (iri.netloc or iri.scheme == "urn")
            or any(char.isspace() for char in root_class_iri)):
        raise HarnessError("INVALID_ROOT_CLASS", "root_class_iri 必须是完整 IRI", status_code=422)
    if metadata_mode not in {"cached_summary", "generate_summary", "structure_only"}:
        raise HarnessError("INVALID_REQUEST", "metadata_mode 非法", status_code=400)
    run_id = uuid4()
    storage = RunArtifactStorage(settings.document_analysis_storage_dir)
    try:
        staged = await stage_upload(
            file, storage_root=settings.document_analysis_storage_dir, run_id=run_id,
            max_upload_bytes=settings.document_analysis_max_upload_bytes,
        )
    except SourceArtifactError as exc:
        raise HarnessError(exc.code, exc.message, status_code=exc.status_code) from exc
    digest = content_hash({
        "engine": ENGINE, "filename": staged.filename, "document_hash": staged.document_hash,
        "root_class_iri": root_class_iri, "metadata_mode": metadata_mode, "origin": origin,
    })
    try:
        existing = db.scalar(select(DocumentAnalysisRun).where(
            DocumentAnalysisRun.owner_id == owner_id,
            DocumentAnalysisRun.request_key == request_key,
        ))
        if existing is not None:
            if existing.request_hash != digest:
                raise HarnessError("IDEMPOTENCY_CONFLICT", "相同 request_key 已用于不同输入")
            if existing.deletion_state == "deleted":
                raise HarnessError("RUN_DELETED", "该运行已删除", status_code=410)
            require_harness(db, existing)
            storage.discard_run(run_id)
            return existing, False
        from app.services.document_harness.model import freeze_policy
        from app.services.document_harness.ontology import freeze_catalog

        try:
            catalog = freeze_catalog(engine, root_class_iri).model_dump(mode="json")
            policy = freeze_policy()
        except Exception as exc:
            raise HarnessError(
                "HARNESS_INPUT_INVALID", "本体类型或模型配置不可用，未创建运行",
                status_code=422,
            ) from exc
        catalog_hash = content_hash(catalog)
        cards = catalog.get("classes", {})
        root_card = cards.get(root_class_iri, {}) if isinstance(cards, dict) else {}
        store = DocumentAnalysisRunStore(db)
        run, created = store.create_run_with_source(
            owner_id=owner_id, request_key=request_key, filename=staged.filename,
            document_hash=staged.document_hash, root_class_iri=root_class_iri,
            root_class_label=root_card.get("label", root_class_iri),
            request_hash=digest, recognition_run_id=run_id,
            ontology_snapshot_hash=catalog_hash, metadata_mode="structure_only",
            ranking_budget_enabled=False, scope_mode="document_graph",
            provisional_fingerprint=content_hash([digest, catalog_hash, policy]),
            progress={"engine": ENGINE, "extraction_protocol": ENGINE},
            source_artifact_id=f"source:{run_id}", source_storage_uri=staged.storage_uri,
            source_media_type=staged.media_type, source_size_bytes=staged.size_bytes,
            source_payload={
                "engine": ENGINE, "policy": policy, "filename": staged.filename,
                "document_hash": staged.document_hash,
                **({"origin": origin} if origin is not None else {}),
            },
            ontology_artifact_id=f"harness-schema:{catalog_hash}",
            ontology_payload=catalog, ontology_is_exclusive=False,
        )
        if created:
            from .runtime import initialize_state

            initialize_state(db, run, catalog)
        db.commit()
        if not created:
            storage.discard_run(run_id)
        return run, created
    except (IdempotencyConflict, RunDeleted) as exc:
        db.rollback()
        storage.discard_run(run_id)
        raise HarnessError(
            "IDEMPOTENCY_CONFLICT" if isinstance(exc, IdempotencyConflict) else "RUN_DELETED",
            str(exc), status_code=409 if isinstance(exc, IdempotencyConflict) else 410,
        ) from exc
    except Exception:
        db.rollback()
        storage.discard_run(run_id)
        raise
