"""Authenticated V2 reporting, immutable contracts and signing APIs."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user, get_ontology_engine, require_role
from app.models.extraction import AstTemplate
from app.models.reporting import (
    ContractRevision,
    ReportArtifact,
    ReportContentReview,
    ReportContentVersion,
    ReportInputSnapshot,
    ReportOutputResult,
    ReportSignature,
    ReportSignatureEvent,
    ReportSigningEnvelope,
    ReportSigningSession,
    RuleMigrationPlan,
    TemplateMigrationPlan,
)
from app.services.reporting.contract_registry import ContractRegistry
from app.services.reporting.report_run_service import ReportRunService, schema_hash, verify_frozen
from app.services.reporting.report_signing import ReportSigning
from app.services.reporting.template_v2 import Model, ReportingError, TemplateV2

router = APIRouter(dependencies=[Depends(get_current_user)])
maintainer = require_role("senior_analyst")


class Request(Model):
    idempotency_key: str = Field(min_length=1, max_length=200)


class SourceBinding(Model):
    job_id: UUID
    snapshot_id: str | None = None
    root_entity_id: str | None = None


class RunRequest(Request):
    template_id: UUID
    source_bindings: dict[str, SourceBinding] = Field(default_factory=dict)
    record_refs: dict[str, str] = Field(default_factory=dict)
    applicable_at: str | None = None
    language: str = "zh"
    purpose: Literal["draft", "formal"] = "draft"


class PreviewRequest(Request):
    mode: Literal["layout", "data", "report"]
    template_id: UUID | None = None
    draft_schema: TemplateV2 | None = None
    source_bindings: dict[str, SourceBinding] = Field(default_factory=dict)
    record_refs: dict[str, str] = Field(default_factory=dict)
    applicable_at: str | None = None
    language: str = "zh"
    purpose: Literal["draft"] = "draft"


class CompileRequest(Model):
    expected_hash: str
    draft_schema: TemplateV2 | None = None


class RevisionRequest(Model):
    expected_hash: str
    expected_revision: int = Field(ge=1)
    template_schema: TemplateV2 = Field(alias="schema")


class PublishRequest(Model):
    expected_hash: str
    compilation_id: str


class MigrationRequest(Model):
    expected_hash: str
    target_schema_version: Literal[2] = 2


class AttemptRequest(Model):
    expected_revision: int = Field(ge=1)


class ContractRequest(Model):
    kind: str
    family_id: str
    revision_no: int = Field(ge=1)
    definition: dict


class ContractDecisionRequest(Model):
    expected_hash: str
    decision: Literal["reviewed", "published", "disabled", "rejected"]
    reason: str = Field(min_length=1)


class RecordRequest(Model):
    record_key: str
    revision_no: int = Field(ge=1)
    values: dict | list
    subject_id: str | None = None
    applicable_at: str | None = None


class RecordReviewRequest(Model):
    expected_hash: str
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1)


class ContentRequest(Request):
    expected_body_hash: str
    attempt: int = Field(ge=1)


class ContentReviewRequest(Request):
    expected_content_hash: str
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1)


class SessionRequest(Request):
    expected_content_hash: str
    review_id: str | None = None
    workflow_ref: str | None = None
    parent_ref: str | None = None


class SignatureRequest(Request):
    content_hash: str
    signature_slot_id: str
    meaning: str
    expected_signature_revision: int = Field(ge=0)
    password: SecretStr


class SignatureEventRequest(Request):
    signature_ref: str
    reason: str = Field(min_length=1)
    expected_signature_revision: int = Field(ge=0)
    password: SecretStr


class EnvelopeRequest(Request):
    content_hash: str
    signature_revision: int = Field(ge=0)
    purpose: Literal["draft", "formal"]


def data(request):
    result = request.model_dump(mode="json")
    if hasattr(request, "password"):
        result["password"] = request.password.get_secret_value()
    return result


def frozen_response(row):
    payload = verify_frozen(row)
    return {
        "id": row.id,
        "content_hash": row.content_hash,
        "actor": row.actor,
        "created_at": row.created_at,
        "payload": payload,
    }


def model_service(db, engine):
    from app.services.extraction.extraction_tasks import semantic_schema_from_engine

    return ReportRunService(db, model_schema=lambda: semantic_schema_from_engine(engine))


def template_row(db, template_id):
    row = db.get(AstTemplate, template_id)
    if row is None:
        raise ReportingError("TEMPLATE_NOT_FOUND", status=404)
    return row


@router.get("/report-contracts")
def contracts(db: Session = Depends(get_db)):
    from app.services.reasoning.rule_service import rule_catalog
    from app.services.reporting.template_preparation import POLICY_REF, STYLE_REF, builtin_profile

    registry = ContractRegistry(db)
    return [
        *rule_catalog(), builtin_profile(STYLE_REF), builtin_profile(POLICY_REF),
        *[
            registry.load(row.id, published=False)
            for row in db.scalars(
                select(ContractRevision).order_by(
                    ContractRevision.family_id, ContractRevision.revision_no
                )
            )
        ],
    ]


@router.get("/report-model-context")
def model_context(ref: str = "auto:ontology", db: Session = Depends(get_db),
                  engine=Depends(get_ontology_engine)):
    from app.services.extraction.extraction_tasks import semantic_schema_from_engine
    from app.services.ontology_model_context import ModelContext

    model = ModelContext(db, semantic_schema_from_engine(engine)).resolve(ref)
    db.commit()
    return model


@router.get("/graph-rules")
def graph_rules():
    from app.services.reasoning.rule_service import rule_catalog

    return rule_catalog()


@router.post("/report-contracts", status_code=201)
def create_contract(
    req: ContractRequest, db: Session = Depends(get_db), identity=Depends(maintainer)
):
    row = ContractRegistry(db).create(**data(req), actor=identity.username)
    db.commit()
    return frozen_response(row)


@router.post("/report-contracts/{ref}/decisions", status_code=201)
def decide_contract(
    ref: str,
    req: ContractDecisionRequest,
    db: Session = Depends(get_db),
    identity=Depends(maintainer),
):
    row = ContractRegistry(db).decide(ref, **data(req), actor=identity.username)
    db.commit()
    return frozen_response(row)


@router.post("/report-contracts/ontology-snapshots", status_code=201)
def ontology_snapshot(
    db: Session = Depends(get_db), identity=Depends(maintainer), engine=Depends(get_ontology_engine)
):
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.extraction_tasks import semantic_schema_from_engine

    classes = semantic_schema_from_engine(engine)
    registry = ContractRegistry(db)
    family = "ontology:" + evidence_hash(classes)
    previous = db.scalar(select(ContractRevision).where(ContractRevision.family_id == family))
    row = previous or registry.create(
        kind="ontology",
        family_id=family,
        revision_no=1,
        definition={"classes": classes},
        actor=identity.username,
        server_ontology=True,
    )
    db.commit()
    return frozen_response(row)


@router.post("/report-contracts/{ref}/records", status_code=201)
def create_record(
    ref: str, req: RecordRequest, db: Session = Depends(get_db), identity=Depends(get_current_user)
):
    registry = ContractRegistry(db)
    contract = registry.load(ref)
    if identity.role not in contract["definition"].get("allowed_roles", []):
        raise ReportingError("RECORD_ROLE_FORBIDDEN", status=403)
    row = registry.record(ref, **data(req), actor=identity.username)
    db.commit()
    return frozen_response(row)


@router.get("/report-contracts/{ref}/records")
def contract_records(ref: str, db: Session = Depends(get_db)):
    from app.models.reporting import ContractRecord

    registry = ContractRegistry(db)
    registry.load(ref, published=False)
    return [
        {**frozen_response(row), **registry.load_record(row.id)}
        for row in db.scalars(
            select(ContractRecord)
            .where(ContractRecord.contract_id == ref)
            .order_by(ContractRecord.created_at)
        )
    ]


@router.post("/report-records/{ref}/reviews", status_code=201)
def review_record(
    ref: str,
    req: RecordReviewRequest,
    db: Session = Depends(get_db),
    identity=Depends(require_role("qa")),
):
    ReportSigning(db).account(identity)
    row = ContractRegistry(db).review_record(ref, **data(req), actor=identity.username)
    db.commit()
    return frozen_response(row)


@router.post("/ast-templates/{template_id}/compile")
def compile_report_template(
    template_id: UUID,
    req: CompileRequest,
    db: Session = Depends(get_db),
    identity=Depends(maintainer),
    engine=Depends(get_ontology_engine),
):
    row = template_row(db, template_id)
    if req.expected_hash != schema_hash(row.schema_json):
        raise ReportingError("TEMPLATE_REVISION_CONFLICT", status=409)
    plan, _, _ = model_service(db, engine).compile(
        req.draft_schema or row.schema_json, identity.username, row.id
    )
    db.commit()
    return plan


@router.post("/ast-templates/{template_id}/revisions", status_code=201)
def create_revision(
    template_id: UUID,
    req: RevisionRequest,
    db: Session = Depends(get_db),
    identity=Depends(maintainer),
    engine=Depends(get_ontology_engine),
):
    from app.api.ast_templates import _template_response

    row = model_service(db, engine).new_revision(
        template_row(db, template_id),
        req.template_schema,
        expected_revision=req.expected_revision,
        expected_hash=req.expected_hash,
        actor=identity.username,
    )
    db.commit()
    return _template_response(row)


@router.post("/ast-templates/{template_id}/publish")
def publish_template(
    template_id: UUID,
    req: PublishRequest,
    db: Session = Depends(get_db),
    identity=Depends(maintainer),
    engine=Depends(get_ontology_engine),
):
    from app.api.ast_templates import _template_response

    row = model_service(db, engine).publish(
        template_row(db, template_id), req.expected_hash, req.compilation_id, identity.username
    )
    db.commit()
    return _template_response(row)


@router.post("/ast-templates/{template_id}/migration-plan")
def migration_plan(
    template_id: UUID,
    req: MigrationRequest,
    db: Session = Depends(get_db),
    identity=Depends(maintainer),
):
    from app.services.reporting.report_run_service import frozen_row
    from app.services.reporting.template_migration import migrate_template

    row = template_row(db, template_id)
    if req.expected_hash != schema_hash(row.schema_json):
        raise ReportingError("TEMPLATE_REVISION_CONFLICT", status=409)
    if row.schema_json.get("schema_version") == 2:
        raise ReportingError("ALREADY_OUTPUT_TEMPLATE")
    plan = migrate_template(
        row.schema_json,
        template_id=row.id,
        version=row.version,
        revision_no=row.revision_no or 1,
        family_id=row.template_family_id,
    )
    result = frozen_row(
        db,
        TemplateMigrationPlan,
        plan["migration_plan_id"],
        plan,
        identity.username,
        template_id=row.id,
    )
    db.commit()
    return frozen_response(result)


@router.post("/report-rules/{rule_id}/migration-plan")
def rule_migration_plan(rule_id: UUID, db: Session = Depends(get_db), identity=Depends(maintainer)):
    from app.models.ontology_meta import OntologyDecisionRule
    from app.services.reasoning.rule_migration import migrate_rule
    from app.services.reporting.report_run_service import frozen_row

    rule = db.get(OntologyDecisionRule, rule_id)
    if rule is None:
        raise ReportingError("RULE_NOT_FOUND", status=404)
    plan = migrate_rule(rule)
    result = frozen_row(
        db, RuleMigrationPlan, plan["migration_plan_id"], plan, identity.username, rule_id=rule.id
    )
    db.commit()
    return frozen_response(result)


@router.post("/report-runs", status_code=201)
def create_run(req: RunRequest, db: Session = Depends(get_db), identity=Depends(get_current_user),
               engine=Depends(get_ontology_engine)):
    service = model_service(db, engine)
    run, created = service.start(req, identity.username)
    db.commit()
    if created or run.execution_status == "pending":
        service.execute(run.id)
    return service.response(service.get(run.id))


@router.post("/report-previews")
def preview(req: PreviewRequest, db: Session = Depends(get_db), identity=Depends(get_current_user),
            engine=Depends(get_ontology_engine)):
    service = model_service(db, engine)
    if req.mode == "layout":
        from app.services.reporting.output_ast import OutputNode
        from app.services.reporting.template_compiler import walk_groups

        template = req.draft_schema or TemplateV2.model_validate(
            template_row(db, req.template_id).schema_json
        )
        ast = OutputNode(
            node_id="layout",
            kind="document",
            children=[
                OutputNode(
                    node_id=s.section_id,
                    kind="section",
                    text=s.title,
                    children=[
                        OutputNode(
                            node_id=g.group_id,
                            kind="group",
                            text=g.title,
                            children=[
                                OutputNode(
                                    node_id=u.output_id,
                                    kind="paragraph",
                                    text=u.title
                                    + " "
                                    + " ".join(
                                        "〈" + template.definitions.inputs[i.input_ref].label + "〉"
                                        for i in u.inputs
                                        if i.input_ref in template.definitions.inputs
                                    ),
                                )
                                for u in g.units
                            ],
                        )
                        for g, _ in walk_groups(s.groups)
                    ],
                )
                for s in template.sections
            ],
        )
        return {
            "mode": "layout",
            "body_ast": ast.model_dump(mode="json"),
            "material_status": "incomplete",
            "business_values": False,
        }
    run, created = service.start(req, identity.username, preview=True)
    db.commit()
    if created or run.execution_status == "pending":
        service.execute(run.id)
    return service.response(service.get(run.id))


@router.get("/report-runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    service = ReportRunService(db)
    return service.response(service.get(run_id))


@router.get("/report-runs/{run_id}/inputs")
def get_inputs(run_id: str, db: Session = Depends(get_db)):
    run = ReportRunService(db).get(run_id)
    return verify_frozen(db.get(ReportInputSnapshot, run.input_snapshot_id))


@router.get("/report-runs/{run_id}/coverage")
def get_coverage(run_id: str, db: Session = Depends(get_db)):
    snapshot = get_inputs(run_id, db)
    return {
        key: snapshot[key]
        for key in ("input_snapshot_id", "coverage", "material_status", "blocking_issues")
    }


@router.get("/report-runs/{run_id}/outputs")
def get_outputs(run_id: str, attempt: int | None = None, db: Session = Depends(get_db)):
    run = ReportRunService(db).get(run_id)
    return [
        frozen_response(row)
        for row in db.scalars(
            select(ReportOutputResult).where(
                ReportOutputResult.run_id == run_id,
                ReportOutputResult.attempt == (attempt or run.attempt),
            )
        )
    ]


@router.post("/report-runs/{run_id}/attempts")
def retry(run_id: str, req: AttemptRequest, db: Session = Depends(get_db)):
    service = ReportRunService(db)
    service.retry(run_id, req.expected_revision)
    db.commit()
    return service.response(service.execute(run_id))


@router.get("/report-runs/{run_id}/artifacts/{artifact_id}")
def download(run_id: str, artifact_id: str, db: Session = Depends(get_db)):
    row = ReportRunService(db).download(run_id, artifact_id)
    return FileResponse(
        row.file_path,
        filename=f"report-{run_id}-{row.id[:12]}.{row.format}",
        headers={"ETag": '"' + row.file_hash + '"'},
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/report-runs/{ref}/content-versions", status_code=201)
def content(
    ref: str, req: ContentRequest, db: Session = Depends(get_db), identity=Depends(get_current_user)
):
    row = ReportSigning(db).content(ref, data(req), identity)
    db.commit()
    return frozen_response(row)


@router.get("/report-runs/{ref}/content-versions")
def content_versions(ref: str, db: Session = Depends(get_db)):
    ReportRunService(db).get(ref)
    return [
        frozen_response(row)
        for row in db.scalars(
            select(ReportContentVersion)
            .where(ReportContentVersion.run_id == ref)
            .order_by(ReportContentVersion.created_at)
        )
    ]


@router.post("/report-content-versions/{ref}/reviews", status_code=201)
def review(
    ref: str,
    req: ContentReviewRequest,
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    row = ReportSigning(db).review(ref, data(req), identity)
    db.commit()
    return frozen_response(row)


@router.get("/report-content-versions/{ref}/reviews")
def reviews(ref: str, db: Session = Depends(get_db)):
    verify_frozen(db.get(ReportContentVersion, ref))
    return [
        frozen_response(row)
        for row in db.scalars(
            select(ReportContentReview)
            .where(ReportContentReview.content_version_id == ref)
            .order_by(ReportContentReview.created_at)
        )
    ]


@router.post("/report-content-versions/{ref}/signing-sessions", status_code=201)
def session(
    ref: str, req: SessionRequest, db: Session = Depends(get_db), identity=Depends(get_current_user)
):
    row = ReportSigning(db).session(ref, data(req), identity)
    db.commit()
    return signing_session(row.id, db)


@router.get("/report-signing-sessions/{ref}")
def signing_session(ref: str, db: Session = Depends(get_db)):
    row = ReportSigning(db).get_session(ref)
    return {
        "id": row.id,
        "content_version_id": row.content_version_id,
        "content_hash": row.content_hash,
        "revision_no": row.revision_no,
        "status": row.status,
        "parent_id": row.parent_id,
        "envelope_request": row.envelope_request,
        "envelope_ids": list(
            db.scalars(
                select(ReportSigningEnvelope.id).where(ReportSigningEnvelope.session_id == ref)
            )
        ),
        "policy": row.frozen_context["policy"],
        "signatures": [
            frozen_response(s)
            for s in db.scalars(select(ReportSignature).where(ReportSignature.session_id == ref))
        ],
        "events": [
            frozen_response(e)
            for e in db.scalars(
                select(ReportSignatureEvent).where(ReportSignatureEvent.session_id == ref)
            )
        ],
    }


@router.get("/report-content-versions/{ref}/signing-sessions")
def content_sessions(ref: str, db: Session = Depends(get_db)):
    verify_frozen(db.get(ReportContentVersion, ref))
    return [
        {"id": row.id, "status": row.status}
        for row in db.scalars(
            select(ReportSigningSession)
            .where(ReportSigningSession.content_version_id == ref)
            .order_by(ReportSigningSession.created_at)
        )
    ]


@router.post("/report-signing-sessions/{ref}/signatures", status_code=201)
def sign(
    ref: str,
    req: SignatureRequest,
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    row = ReportSigning(db).sign(ref, data(req), identity)
    db.commit()
    return frozen_response(row)


@router.post("/report-signing-sessions/{ref}/signature-events", status_code=201)
def revoke(
    ref: str,
    req: SignatureEventRequest,
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    row = ReportSigning(db).revoke(ref, data(req), identity)
    db.commit()
    return frozen_response(row)


@router.post("/report-signing-sessions/{ref}/envelopes", status_code=201)
def envelope(
    ref: str,
    req: EnvelopeRequest,
    db: Session = Depends(get_db),
    identity=Depends(get_current_user),
):
    row = ReportSigning(db).envelope(ref, data(req), identity)
    db.commit()
    return signing_envelope(row.id, db)


@router.get("/report-signing-envelopes/{ref}")
def signing_envelope(ref: str, db: Session = Depends(get_db)):
    row = db.get(ReportSigningEnvelope, ref)
    result = frozen_response(row)
    result["artifacts"] = [
        frozen_response(a)
        for a in db.scalars(select(ReportArtifact).where(ReportArtifact.envelope_id == ref))
    ]
    return result
