"""Transactional entry point: verify resources, freeze once, execute and replay."""

from __future__ import annotations

import os
import tempfile
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update

from app.config import settings
from app.models.evidence import EvidenceJobState
from app.models.extraction import AstTemplate, ExtractionJob, GeneratedReport
from app.models.reporting import (
    ReportArtifact,
    ReportBody,
    ReportInputSnapshot,
    ReportOutputResult,
    ReportRun,
    TemplateCompilation,
)
from app.services import audit
from app.services.extraction.candidate_store import CandidateStore
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.performance import measure, timed
from app.services.fact_commit import FactCommitService
from app.services.fact_selector import FactSelector
from app.services.reporting.contract_registry import ContractRegistry
from app.services.reporting.output_renderer import render_docx, render_snapshot
from app.services.reporting.report_snapshot import resolve_snapshot
from app.services.reporting.template_compiler import (
    COMPILER_VERSION,
    compile_template,
    require_valid,
)
from app.services.reporting.template_v2 import ReportingError, TemplateV2

ARTIFACT_ROOT = Path("data/reports/v2")
_compiled_plans = OrderedDict()
_compilation_lock = RLock()


def frozen_row(db, model, identity, payload, actor, **columns):
    digest = evidence_hash(payload)
    row = db.get(model, identity)
    if row:
        if row.content_hash != digest or evidence_hash(row.payload) != digest:
            raise ReportingError("IMMUTABLE_IDENTITY_CONFLICT", status=409)
        return row
    row = model(id=identity, payload=deepcopy(payload), content_hash=digest, actor=actor, **columns)
    db.add(row)
    db.flush()
    return row


def verify_frozen(row):
    if row is None:
        raise ReportingError("RESOURCE_NOT_FOUND", status=404)
    if evidence_hash(row.payload) != row.content_hash:
        raise ReportingError("FROZEN_CONTENT_HASH_MISMATCH")
    return deepcopy(row.payload)


def _require_report_source(job: ExtractionJob) -> None:
    """Prevent new report snapshots from consuming retired ordinary Word products."""
    if (
        isinstance(job.source_type, str)
        and job.source_type.strip().casefold() == "word"
        and (job.source_config or {}).get("mode") != "template_default"
    ):
        raise ReportingError(
            "WORD_RECOGNITION_RETIRED",
            status=410,
            message="Use POST /api/document-analysis/runs",
        )


def _contract_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "contract_ref",
                "condition_ref",
                "claim_ref",
                "policy_ref",
                "conversion_ref",
                "approval_ref",
                "style_profile_ref",
                "publication_policy_ref",
                "workflow_contract_ref",
                "resolution_ref",
                "vocabulary_ref",
                "type_contract_ref",
                "ontology_release_ref",
            } and isinstance(child, str):
                yield child
            if key == "condition_refs" or key == "claim_refs":
                yield from (v for v in child if isinstance(v, str))
            yield from _contract_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _contract_refs(child)


class ReportRunService:
    def __init__(self, db, *, model_schema=None):
        self.db, self.registry = db, ContractRegistry(db)
        self.model_schema = model_schema

    def prepare(self, schema, **kwargs):
        from app.services.reporting.template_preparation import prepare_template

        profile = (schema.get("demo_profile") if isinstance(schema, dict)
                   else getattr(schema, "demo_profile", None))
        if profile:
            raise ReportingError("STATIC_DEMO_TEMPLATE", "请在演示模板页面生成批记录", status=409)
        return prepare_template(self.db, schema, classes=self.model_schema, **kwargs)

    def load_contracts(self, schema):
        template = TemplateV2.model_validate(schema)
        from app.services.ontology_model_context import ModelContext
        from app.services.reporting.template_preparation import template_style

        ontology = ModelContext(self.db, self.model_schema).resolve(template.ontology_release_ref)
        if ontology["kind"] != "ontology":
            raise ReportingError("ONTOLOGY_RELEASE_MISMATCH")
        contracts = {template.ontology_release_ref: ontology}
        style = template_style(template)
        if style:
            contracts[style["contract_id"]] = style
        queue = list(_contract_refs(template.model_dump(mode="json")))
        while queue:
            ref = queue.pop()
            if ref in contracts:
                continue
            try:
                record = self.registry.load(ref, published=False)
            except ReportingError:
                continue  # The compiler diagnoses only reachable contract references.
            contracts[ref] = record
            queue.extend(_contract_refs(record["definition"]))
        return ontology["definition"]["classes"], contracts

    @timed("report_compile")
    def compile(self, schema, actor, template_id=None):
        authored_hash = schema_hash(schema)
        template = self.prepare(schema)
        # Always reload/validate contracts. Status, definition and ontology changes
        # must invalidate compilation even when the template ID stays the same.
        ontology, contracts = self.load_contracts(template)
        key = evidence_hash(
            [COMPILER_VERSION, str(template_id), authored_hash, template, ontology, contracts]
        )
        with _compilation_lock:
            plan = _compiled_plans.get(key)
            if plan is None:
                with measure("report_compile_miss"):
                    plan = compile_template(template, ontology, contracts)
                    plan["schema_hash"] = authored_hash
                    plan["template_id"] = str(template_id) if template_id else None
                    plan.pop("compilation_id", None)
                    plan["compilation_id"] = evidence_hash(plan)
                if plan.get("compilation_id"):
                    _compiled_plans[key] = deepcopy(plan)
                    if len(_compiled_plans) > 32:
                        _compiled_plans.popitem(last=False)
            else:
                with measure("report_compile_hit"):
                    _compiled_plans.move_to_end(key)
                    plan = deepcopy(plan)
        if plan.get("compilation_id"):
            frozen_row(
                self.db,
                TemplateCompilation,
                plan["compilation_id"],
                plan,
                actor,
                template_id=template_id,
                schema_hash=plan["schema_hash"],
            )
        return plan, ontology, {**contracts, **deepcopy(plan.get("generated_contracts", {}))}

    def new_revision(self, original, schema, *, expected_revision, expected_hash, actor):
        actual = evidence_hash(original.schema_json)
        normalized = schema_hash(original.schema_json)
        if expected_hash not in {actual, normalized} or expected_revision != (
            original.revision_no or 1
        ):
            raise ReportingError(
                "TEMPLATE_REVISION_CONFLICT",
                status=409,
                actual={"revision": original.revision_no, "hash": normalized},
            )
        template = self.prepare(schema)
        family = original.template_family_id or str(original.id)
        latest = (
            self.db.scalar(
                select(func.max(AstTemplate.revision_no)).where(
                    AstTemplate.template_family_id == family
                )
            )
            or 1
        )
        if latest != expected_revision:
            raise ReportingError("TEMPLATE_REVISION_CONFLICT", status=409, actual=latest)
        identity = uuid4()
        template = template.model_copy(
            update={
                "template_revision_id": str(identity),
                "template_family_id": family,
                "revision_no": latest + 1,
            }
        )
        primary_class = next(
            (s.class_iri for s in template.source_slots if s.kind == "document"),
            original.iri_pattern,
        )
        source_changed = bool(original.iri_pattern and primary_class != original.iri_pattern)
        row = AstTemplate(
            id=identity,
            name=original.name,
            version=f"v2.{latest + 1}",
            schema_version=2,
            template_family_id=family,
            revision_no=latest + 1,
            schema_json=template.model_dump(mode="json"),
            schema_hash=evidence_hash(template),
            doc_no=template.doc_no,
            iri_pattern=primary_class,
            status="draft",
            created_by=actor,
            owner=original.owner,
            sample_text=original.sample_text,
            sample_content_json=original.sample_content_json,
            sample_analysis=original.sample_analysis,
            sample_docx_path=original.sample_docx_path,
            default_source_path=None if source_changed else original.default_source_path,
            default_source_filename=None if source_changed else original.default_source_filename,
            default_source_job_id=None if source_changed else original.default_source_job_id,
        )
        self.db.add(row)
        self.db.flush()
        audit.append(
            self.db,
            "template.revision_created",
            actor=actor,
            entity_iri=str(row.id),
            details={"parent": str(original.id), "schema_hash": row.schema_hash},
            commit=False,
        )
        return row

    def publish(self, row, expected_hash, compilation_id, actor):
        if row.schema_json.get("schema_version") != 2:
            raise ReportingError("TEMPLATE_MIGRATION_REQUIRED")
        actual = schema_hash(row.schema_json)
        if expected_hash != actual:
            raise ReportingError("TEMPLATE_REVISION_CONFLICT", status=409, actual=actual)
        saved = self.db.get(TemplateCompilation, compilation_id)
        previous = verify_frozen(saved)
        if saved.template_id != row.id or previous.get("schema_hash") != actual:
            raise ReportingError("COMPILATION_IDENTITY_MISMATCH")
        plan, _, _ = self.compile(row.schema_json, actor, row.id)
        require_valid(plan)
        from app.services.ontology_model_context import ModelContext

        model = ModelContext(self.db).resolve(plan["template"]["ontology_release_ref"])
        if model["status"] != "published":
            raise ReportingError(
                "ONTOLOGY_MODEL_NOT_PUBLISHED",
                "模型内容已固定，请在本体发布流程中完成该模型版本的发布",
            )
        if plan["compilation_id"] != compilation_id:
            raise ReportingError("CONTRACT_VERSION_CHANGED", status=409)
        if row.status == "published":
            return row
        changed = self.db.execute(
            update(AstTemplate)
            .where(
                AstTemplate.id == row.id,
                AstTemplate.status == "draft",
                AstTemplate.schema_hash == actual,
            )
            .values(status="published"),
            execution_options={"synchronize_session": False},
        )
        if changed.rowcount != 1:
            raise ReportingError("TEMPLATE_REVISION_CONFLICT", status=409)
        audit.append(
            self.db,
            "template.published",
            actor=actor,
            entity_iri=str(row.id),
            details={"schema_hash": actual, "compilation_id": compilation_id},
            commit=False,
        )
        self.db.expire(row)
        return row

    def start(self, request, actor, *, preview=False, _prepared=None, _source_inputs=None):
        payload = (
            request.model_dump(mode="json") if hasattr(request, "model_dump") else deepcopy(request)
        )
        key = payload["idempotency_key"]
        digest = evidence_hash(payload)
        existing = self.db.scalar(
            select(ReportRun).where(ReportRun.actor == actor, ReportRun.idempotency_key == key)
        )
        if existing:
            if existing.request_hash != digest:
                raise ReportingError("IDEMPOTENCY_CONFLICT", status=409)
            return existing, False
        row = (
            self.db.get(AstTemplate, UUID(payload["template_id"]))
            if payload.get("template_id")
            else None
        )
        schema = (
            payload.get("draft_schema")
            if preview and payload.get("draft_schema")
            else (row.schema_json if row else None)
        )
        if schema is None:
            raise ReportingError("TEMPLATE_NOT_FOUND", status=404)
        if schema.get("demo_profile") or (row and (row.schema_json or {}).get("demo_profile")):
            raise ReportingError("STATIC_DEMO_TEMPLATE", "请在演示模板页面生成批记录", status=409)
        if schema.get("schema_version") != 2:
            raise ReportingError(
                "TEMPLATE_MIGRATION_REQUIRED", template_id=str(row.id) if row else None
            )
        if not preview and (row is None or row.status != "published"):
            raise ReportingError("TEMPLATE_NOT_PUBLISHED")
        template = TemplateV2.model_validate(schema)
        if _prepared is None:
            plan, ontology, contracts = self.compile(template, actor, row.id if row else None)
        else:
            # Internal same-request handoff from coverage; public request bodies
            # cannot supply artifacts. Verify the frozen plan and exact template.
            plan, ontology, contracts = _prepared
            if plan["schema_hash"] != evidence_hash(template) or plan != verify_frozen(
                self.db.get(TemplateCompilation, plan["compilation_id"])
            ):
                raise ReportingError("TEMPLATE_REVISION_CONFLICT", status=409)
        require_valid(plan)
        template = TemplateV2.model_validate(plan["template"])
        for ref in plan["contract_hashes"]:
            if contracts[ref]["status"] != "published" and not (
                preview
                and contracts[ref]["kind"] == "ontology"
                and contracts[ref].get("origin") == "ontology_model_service"
                or contracts[ref] == plan.get("generated_contracts", {}).get(ref)
            ):
                raise ReportingError("CONTRACT_NOT_PUBLISHED", actual=ref)
        source_bindings = payload.get("source_bindings", {})
        if set(source_bindings) - {s.source_slot_id for s in template.source_slots}:
            raise ReportingError("UNKNOWN_SOURCE_SLOT")
        sources = {}
        for slot in template.source_slots:
            selected = source_bindings.get(slot.source_slot_id)
            if not selected:
                sources[slot.source_slot_id] = {"snapshot": None, "state": "unavailable"}
                continue
            job = self.db.get(ExtractionJob, UUID(selected["job_id"]))
            if job is None:
                raise ReportingError("SOURCE_NOT_FOUND", status=404)
            _require_report_source(job)
            if (job.source_config or {}).get("document_role") in {
                "template_sample",
                "training_source",
                "training_report",
            }:
                raise ReportingError("SOURCE_ROLE_INVALID")
            try:
                snapshot = FactCommitService(self.db, None).published_snapshot(
                    job.id, selected.get("snapshot_id")
                )
            except LookupError as exc:
                raise ReportingError("SOURCE_SNAPSHOT_NOT_FOUND", status=404) from exc
            state = self.db.get(EvidenceJobState, job.id, populate_existing=True)
            prepared_source = (_source_inputs or {}).get(str(job.id))
            if prepared_source is not None and prepared_source["revision"] == (
                state.revision if state else 0
            ):
                discovery_run = prepared_source["run"]
                candidate_values = prepared_source["candidates"]
            else:
                discovery_run = deepcopy(state.extraction_run or {}) if state else {}
                candidate_values = CandidateStore(self.db).list(job.id)
            candidates = [c.model_dump(mode="json") for c in candidate_values]
            from app.services.extraction.template_extraction_plan import discovery_revision

            discovery_version = discovery_revision(candidate_values, discovery_run)
            discovery = {}
            for task_id, decision in discovery_run.get("discovery_decisions", {}).items():
                if (
                    snapshot
                    and decision.get("snapshot_id") == snapshot["snapshot_id"]
                    and decision.get("discovery_revision") == discovery_version
                    and decision.get("actor")
                    and decision.get("reason")
                    and decision.get("report_selector_key")
                ):
                    discovery[decision["report_selector_key"]] = {
                        "status": "complete",
                        "object_ids": decision.get("object_iris", []),
                        "snapshot_id": snapshot["snapshot_id"],
                        "proof_ref": evidence_hash([task_id, decision]),
                    }
            selector = FactSelector(snapshot, schema=ontology) if snapshot else None
            roots = selector.instances(slot.class_iri) if selector else []
            root = selected.get("root_entity_id")
            if root and root not in roots:
                raise ReportingError("SUBJECT_SCOPE_MISMATCH")
            if not root and len(roots) == 1:
                root = roots[0]
            elif not root and len(roots) > 1:
                raise ReportingError("SUBJECT_AMBIGUOUS", actual=roots)
            from app.services.ontology_model_context import ModelContext

            compatibility = ModelContext(self.db).compatibility(
                [*candidates, *[r["candidate"] for r in (snapshot or {}).get("assertions", [])]],
                template,
                ontology,
                contracts,
            )
            sources[slot.source_slot_id] = {
                "model_compatibility": compatibility,
                "job_id": str(job.id),
                "snapshot": snapshot,
                "root_entity_id": root,
                "source_filename": job.source_filename,
                "source_role": slot.allowed_role,
                "source_hash": evidence_hash(snapshot) if snapshot else None,
                "candidates": candidates,
                "discovery": discovery,
                "discovery_revision": evidence_hash(discovery_run),
                "review_revision": evidence_hash(candidates),
            }
        records = {
            slot: self.registry.load_record(ref)
            for slot, ref in payload.get("record_refs", {}).items()
        }
        from app.services.reasoning.calculation_review import decision_history

        for source in sources.values():
            if source.get("job_id"):
                job_id = UUID(source["job_id"])
                source["calculation_decisions"] = decision_history(self.db, job_id)
                state = self.db.get(EvidenceJobState, job_id, populate_existing=True)
                source["extraction_completion"] = (
                    (state.extraction_run or {}).get("completion") if state else None
                )
        generated_at = datetime.now(timezone.utc).isoformat()
        for binding in template.definitions.bindings.values():
            if binding.kind == "context" and binding.scope.record_slot == "run":
                contract = contracts[binding.contract_ref]
                if contract["kind"] == "context":
                    filename = next(
                        (
                            s.get("source_filename")
                            for s in sources.values()
                            if s.get("source_filename")
                        ),
                        "",
                    )
                    fields = contract["definition"]["output_type"].get("fields", {})
                    values = {
                        "generated_at": generated_at,
                        "source_filename": filename,
                        "language": payload.get("language", "zh"),
                        "timezone": "UTC",
                    }
                    records["run"] = {
                        "record_id": evidence_hash([key, generated_at]),
                        "contract_id": binding.contract_ref,
                        "values": {k: values[k] for k in fields},
                        "state": "ready",
                        "provenance": {
                            "kind": "run_context",
                            "generated_at": generated_at,
                            "contract_id": binding.contract_ref,
                        },
                    }
        bundle = {
            "template": template.model_dump(mode="json"),
            "template_status": row.status
            if row and schema_hash(row.schema_json) == plan["schema_hash"]
            else "draft",
            "compilation_id": plan["compilation_id"],
            "schema": ontology,
            "contracts": {
                key: value
                for key, value in contracts.items()
                if key in plan["contract_hashes"] or value["kind"] in {"claim", "condition"}
            },
            "sources": sources,
            "records": records,
            "generated_at": generated_at,
            "applicable_at": payload.get("applicable_at"),
            "language": payload.get("language", "zh"),
            "model_enabled": bool(settings.local_llm_enabled),
            "model_endpoint": settings.local_llm_base_url,
            "preview_mode": payload.get("mode"),
        }
        bundle["source_bundle_id"] = evidence_hash(bundle)
        run = ReportRun(
            id=uuid4().hex,
            actor=actor,
            idempotency_key=key,
            request_hash=digest,
            template_id=row.id if row else None,
            compilation_id=plan["compilation_id"],
            source_bundle=bundle,
            source_hash=evidence_hash(bundle),
            purpose=payload.get("purpose", "draft"),
        )
        self.db.add(run)
        self.db.flush()
        source_job = next(
            (source.get("job_id") for source in sources.values() if source.get("job_id")), None
        )
        if source_job and (not preview or payload.get("mode") == "report"):
            self.db.add(
                GeneratedReport(
                    id=uuid4(),
                    job_id=UUID(source_job),
                    report_type="semantic_report",
                    actor=actor,
                    file_path="",
                    report_run_id=run.id,
                    report_status="pending",
                )
            )
            self.db.flush()
        audit.append(
            self.db,
            "report.sources_frozen",
            actor=actor,
            entity_iri=run.id,
            details={"source_hash": run.source_hash, "compilation_id": run.compilation_id},
            commit=False,
        )
        return run, True

    def execute(self, run_id, *, provider=None):
        run = self.get(run_id)
        if run.execution_status == "completed":
            return run
        if run.execution_status == "failed":
            raise ReportingError("RUN_RETRY_REQUIRED", status=409)
        if evidence_hash(run.source_bundle) != run.source_hash:
            raise ReportingError("SOURCE_VERSION_CHANGED")
        worker_token, attempt = uuid4().hex, run.attempt
        changed = self.db.execute(
            update(ReportRun)
            .where(
                ReportRun.id == run.id,
                ReportRun.execution_status == "pending",
                ReportRun.revision_no == run.revision_no,
            )
            .values(
                execution_status="running",
                phase="resolving",
                revision_no=run.revision_no + 1,
                worker_token=worker_token,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            ),
            execution_options={"synchronize_session": False},
        )
        if changed.rowcount != 1:
            raise ReportingError("RUN_ALREADY_RUNNING", status=409)
        self.db.commit()
        self.db.refresh(run)

        def renew():
            changed = self.db.execute(
                update(ReportRun)
                .where(
                    ReportRun.id == run_id,
                    ReportRun.worker_token == worker_token,
                    ReportRun.attempt == attempt,
                    ReportRun.execution_status == "running",
                )
                .values(lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20)),
                execution_options={"synchronize_session": False},
            )
            if changed.rowcount != 1:
                raise ReportingError("RUN_WORKER_REPLACED", status=409)

        try:
            plan = verify_frozen(self.db.get(TemplateCompilation, run.compilation_id))
            if run.input_snapshot_id:
                snapshot = verify_frozen(self.db.get(ReportInputSnapshot, run.input_snapshot_id))
            else:
                snapshot = resolve_snapshot(plan, run.source_bundle)
                frozen_row(
                    self.db,
                    ReportInputSnapshot,
                    snapshot["input_snapshot_id"],
                    snapshot,
                    run.actor,
                    source_hash=run.source_hash,
                )
                run.input_snapshot_id = snapshot["input_snapshot_id"]
                run.material_status, run.phase = snapshot["material_status"], "rendering"
                renew()
                self.db.commit()
            completed = {}
            previous = list(
                self.db.scalars(
                    select(ReportOutputResult)
                    .where(ReportOutputResult.run_id == run.id)
                    .order_by(ReportOutputResult.attempt)
                )
            )
            for result in previous:
                payload = verify_frozen(result)
                if payload["execution_status"] == "completed":
                    completed[payload["output_instance_id"]] = payload

            def checkpoint(result):
                renew()
                identity = evidence_hash([run.id, result["output_instance_id"], run.attempt])
                frozen_row(
                    self.db,
                    ReportOutputResult,
                    identity,
                    result,
                    run.actor,
                    run_id=run.id,
                    output_id=result["output_id"],
                    execution_scope_id=result["execution_scope_id"],
                    attempt=run.attempt,
                )
                self.db.commit()

            if provider is None and run.source_bundle.get("model_enabled"):
                from app.services.reporting.narrative_renderer import local_provider

                if run.source_bundle["model_endpoint"] != settings.local_llm_base_url:
                    raise ReportingError("MODEL_VERSION_CHANGED")
                provider = local_provider
            rendered = render_snapshot(
                snapshot, provider=provider, completed=completed, checkpoint=checkpoint
            )
            renew()
            body = frozen_row(
                self.db,
                ReportBody,
                evidence_hash([run.id, run.attempt]),
                {
                    **{k: v for k, v in rendered.items() if k != "output_results"},
                    "input_snapshot_id": snapshot["input_snapshot_id"],
                    "attempt": run.attempt,
                },
                run.actor,
                run_id=run.id,
                attempt=run.attempt,
            )
            if rendered["execution_status"] == "completed":
                style = run.source_bundle["contracts"][
                    run.source_bundle["template"]["style_profile_ref"]
                ]["definition"]
                self.artifact(run, body, render_docx(rendered["body_ast"], style), "docx")
            run.execution_status = rendered["execution_status"]
            run.phase = "completed" if run.execution_status == "completed" else "failed"
            run.worker_token, run.lease_expires_at = None, None
            audit.append(
                self.db,
                "report.rendered",
                actor=run.actor,
                entity_iri=run.id,
                details={
                    "body_hash": rendered["body_hash"],
                    "attempt": run.attempt,
                    "material_status": run.material_status,
                },
                commit=False,
            )
            self.sync_legacy(run)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run = self.get(run_id)
            if run.worker_token != worker_token:
                raise
            run.execution_status, run.phase = "failed", "failed"
            run.worker_token, run.lease_expires_at = None, None
            run.error = getattr(exc, "detail", {"code": "REPORT_EXECUTION_FAILED"})
            self.sync_legacy(run)
            self.db.commit()
            raise
        return run

    def retry(self, run_id, expected_revision):
        run = self.get(run_id)
        changed = self.db.execute(
            update(ReportRun)
            .where(
                ReportRun.id == run_id,
                ReportRun.revision_no == expected_revision,
                or_(
                    ReportRun.execution_status.in_(["completed", "failed"]),
                    and_(
                        ReportRun.execution_status == "running",
                        ReportRun.lease_expires_at < datetime.now(timezone.utc),
                    ),
                ),
            )
            .values(
                attempt=run.attempt + 1,
                revision_no=expected_revision + 1,
                execution_status="pending",
                phase="frozen",
                error=None,
                worker_token=None,
                lease_expires_at=None,
            ),
            execution_options={"synchronize_session": False},
        )
        if changed.rowcount != 1:
            raise ReportingError("RUN_REVISION_CONFLICT", status=409)
        self.db.expire(run)
        return run

    def artifact(
        self, run, body, data, format, *, envelope_id=None, purpose="draft", ast_hash=None
    ):
        file_hash = sha256(data).hexdigest()
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        path = ARTIFACT_ROOT / (file_hash + "." + format)
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=ARTIFACT_ROOT, delete=False) as stream:
                temp = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                if sha256(temp.read_bytes()).hexdigest() != file_hash:
                    raise ReportingError("ARTIFACT_HASH_MISMATCH")
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        elif sha256(path.read_bytes()).hexdigest() != file_hash:
            raise ReportingError("ARTIFACT_HASH_MISMATCH")
        payload = {
            "file_hash": file_hash,
            "ast_hash": ast_hash or body.payload["body_hash"],
            "format": format,
            "purpose": purpose,
            "style_ref": run.source_bundle["template"]["style_profile_ref"],
            "body_id": body.id,
            "envelope_id": envelope_id,
        }
        return frozen_row(
            self.db,
            ReportArtifact,
            evidence_hash([run.id, payload]),
            payload,
            run.actor,
            run_id=run.id,
            body_id=body.id,
            envelope_id=envelope_id,
            format=format,
            file_hash=file_hash,
            file_path=str(path),
            purpose=purpose,
        )

    def get(self, run_id):
        run = self.db.get(ReportRun, run_id, populate_existing=True)
        if run is None:
            raise ReportingError("REPORT_NOT_FOUND", status=404)
        return run

    def body(self, run_id, attempt=None):
        run = self.get(run_id)
        return self.db.scalar(
            select(ReportBody).where(
                ReportBody.run_id == run_id, ReportBody.attempt == (attempt or run.attempt)
            )
        )

    def download(self, run_id, artifact_id):
        self.get(run_id)
        row = self.db.get(ReportArtifact, artifact_id)
        if row is None or row.run_id != run_id:
            raise ReportingError("ARTIFACT_NOT_FOUND", status=404)
        verify_frozen(row)
        path = Path(row.file_path)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != row.file_hash:
            raise ReportingError("ARTIFACT_HASH_MISMATCH")
        return row

    def response(self, run):
        body = self.body(run.id)
        artifacts = (
            list(
                self.db.scalars(
                    select(ReportArtifact).where(
                        ReportArtifact.run_id == run.id,
                        ReportArtifact.body_id == body.id,
                        ReportArtifact.envelope_id.is_(None),
                    )
                )
            )
            if body
            else []
        )
        return {
            "run_id": run.id,
            "source_bundle_id": run.source_bundle["source_bundle_id"],
            "input_snapshot_id": run.input_snapshot_id,
            "template_id": str(run.template_id) if run.template_id else None,
            "template_status": run.source_bundle["template_status"],
            "execution_status": run.execution_status,
            "material_status": run.material_status,
            "review_status": run.review_status,
            "phase": run.phase,
            "attempt": run.attempt,
            "revision_no": run.revision_no,
            "error": run.error,
            "lease_expires_at": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
            "body_hash": body.payload["body_hash"] if body else None,
            "body_ast": body.payload["body_ast"] if body else None,
            "artifacts": [{"artifact_id": a.id, **a.payload} for a in artifacts],
        }

    def sync_legacy(self, run):
        for report in self.db.scalars(
            select(GeneratedReport).where(GeneratedReport.report_run_id == run.id)
        ):
            response = self.response(run)
            artifact = response["artifacts"][0] if response["artifacts"] else None
            report.report_status = run.execution_status
            report.report_error = str(run.error) if run.error else None
            report.coverage_manifest_id = run.input_snapshot_id
            report.narratives = {
                "run_id": run.id,
                "attempt": run.attempt,
                "body_hash": response["body_hash"],
                "material_status": run.material_status,
            }
            if artifact:
                row = self.db.get(ReportArtifact, artifact["artifact_id"])
                report.report_artifact_id = row.id
                report.file_path = row.file_path
                report.file_size = Path(row.file_path).stat().st_size


def schema_hash(schema):
    if isinstance(schema, TemplateV2):
        return evidence_hash(schema)
    return (
        evidence_hash(TemplateV2.model_validate(schema))
        if schema.get("schema_version") == 2
        else evidence_hash(schema)
    )
