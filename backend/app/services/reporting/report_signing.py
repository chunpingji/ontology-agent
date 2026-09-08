"""Reviewed fixed content and authenticated, append-only signing."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update

from app.auth import verify_password
from app.models.ontology_meta import AppUser
from app.models.reporting import (
    ReportArtifact,
    ReportContentReview,
    ReportContentVersion,
    ReportRequest,
    ReportSignature,
    ReportSignatureEvent,
    ReportSignatureSnapshot,
    ReportSigningEnvelope,
    ReportSigningSession,
)
from app.services import audit
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.output_ast import OutputNode
from app.services.reporting.output_renderer import render_docx
from app.services.reporting.report_run_service import ReportRunService, frozen_row, verify_frozen
from app.services.reporting.template_compiler import walk_groups
from app.services.reporting.template_v2 import ReportingError, TemplateV2


class ReportSigning:
    def __init__(self, db):
        self.db = db
        self.runs = ReportRunService(db)

    def request(self, operation, actor, payload):
        # Never persist passwords or password-derived material.
        clean = {k: v for k, v in payload.items() if k != "password"}
        key = clean["idempotency_key"]
        identity = evidence_hash([operation, actor, key])
        receipt = self.db.get(ReportRequest, identity)
        digest = evidence_hash(clean)
        if receipt and receipt.request_hash != digest:
            raise ReportingError("IDEMPOTENCY_CONFLICT", status=409)
        return identity, digest, verify_frozen(receipt) if receipt else None

    def receipt(self, operation, actor, request, payload, result_id):
        identity, digest, _ = request
        frozen_row(
            self.db,
            ReportRequest,
            identity,
            {"result_id": result_id},
            actor,
            operation=operation,
            idempotency_key=payload["idempotency_key"],
            request_hash=digest,
        )

    def content(self, run_id, payload, identity):
        op = "content:" + run_id
        request = self.request(op, identity.username, payload)
        if request[2]:
            return self.db.get(ReportContentVersion, request[2]["result_id"])
        run = self.runs.get(run_id)
        body = self.runs.body(run_id, payload["attempt"])
        data = verify_frozen(body)
        if data["body_hash"] != payload["expected_body_hash"]:
            raise ReportingError("BODY_HASH_MISMATCH", status=409)
        if data["execution_status"] != "completed":
            raise ReportingError("REPORT_EXECUTION_INCOMPLETE")
        policy_ref = run.source_bundle["template"]["publication_policy_ref"]
        policy = run.source_bundle["contracts"][policy_ref]
        row = frozen_row(
            self.db,
            ReportContentVersion,
            uuid4().hex,
            {
                "run_id": run_id,
                "body_id": body.id,
                "body_hash": data["body_hash"],
                "input_snapshot_id": data["input_snapshot_id"],
                "attempt": payload["attempt"],
                "source_hash": run.source_hash,
                "material_status": data["material_status"],
                "template_status": run.source_bundle["template_status"],
                "policy_ref": policy_ref,
                "policy": deepcopy(policy),
                "requires_assisted_review": any(
                    unit.render.kind == "narrative" and unit.render.mode == "assisted"
                    for section in TemplateV2.model_validate(run.source_bundle["template"]).sections
                    for group, _ in walk_groups(section.groups)
                    for unit in group.units
                ),
            },
            identity.username,
            run_id=run_id,
            body_id=body.id,
            input_snapshot_id=data["input_snapshot_id"],
        )
        self.receipt(op, identity.username, request, payload, row.id)
        self.record("report.content_frozen", identity.username, row.id, {"hash": row.content_hash})
        return row

    def review(self, content_id, payload, identity):
        op = "review:" + content_id
        request = self.request(op, identity.username, payload)
        if request[2]:
            return self.db.get(ReportContentReview, request[2]["result_id"])
        content = self.db.get(ReportContentVersion, content_id)
        data = verify_frozen(content)
        if content.content_hash != payload["expected_content_hash"]:
            raise ReportingError("CONTENT_HASH_MISMATCH", status=409)
        policy = data["policy"]["definition"]
        self.account(identity, roles=policy.get("review_roles", ["qa"]))
        if payload["decision"] not in {"approved", "rejected"} or not payload["reason"].strip():
            raise ReportingError("REVIEW_INVALID")
        row = frozen_row(
            self.db,
            ReportContentReview,
            uuid4().hex,
            {
                "content_version_id": content_id,
                "content_hash": content.content_hash,
                "decision": payload["decision"],
                "reason": payload["reason"],
                "reviewer": identity.username,
                "role": identity.role,
            },
            identity.username,
            content_version_id=content_id,
            decision=payload["decision"],
        )
        self.receipt(op, identity.username, request, payload, row.id)
        self.record(
            "report.content_reviewed",
            identity.username,
            content_id,
            {"review_id": row.id, "decision": row.decision},
        )
        return row

    def session(self, content_id, payload, identity):
        op = "session:" + content_id
        request = self.request(op, identity.username, payload)
        if request[2]:
            return self.db.get(ReportSigningSession, request[2]["result_id"])
        content = self.db.get(ReportContentVersion, content_id)
        data = verify_frozen(content)
        if content.content_hash != payload["expected_content_hash"]:
            raise ReportingError("CONTENT_HASH_MISMATCH", status=409)
        policy = data["policy"]["definition"]
        reviews = list(
            self.db.scalars(
                select(ReportContentReview)
                .where(ReportContentReview.content_version_id == content_id)
                .order_by(ReportContentReview.created_at, ReportContentReview.id)
            )
        )
        review = reviews[-1] if reviews else None
        if (policy.get("require_review", True) or data.get("requires_assisted_review")) and (
            not review or review.id != payload.get("review_id") or review.decision != "approved"
        ):
            raise ReportingError("CONTENT_REVIEW_REQUIRED")
        workflow = None
        if policy.get("workflow_contract_ref"):
            workflow = self.runs.registry.load_record(payload.get("workflow_ref"))
            if (
                workflow["contract_id"] != policy["workflow_contract_ref"]
                or workflow["state"] != "ready"
            ):
                raise ReportingError("WORKFLOW_PENDING_REVIEW")
            if policy.get("require_ready_for_submission", True) and (
                workflow["values"].get("ready_for_submission") is not True
            ):
                raise ReportingError("SUBMISSION_NOT_READY")
        parent = (
            self.db.get(ReportSigningSession, payload["parent_ref"])
            if payload.get("parent_ref")
            else None
        )
        if payload.get("parent_ref") and (not parent or parent.content_version_id != content_id):
            raise ReportingError("SIGNING_PARENT_INVALID")
        row = ReportSigningSession(
            id=uuid4().hex,
            content_version_id=content_id,
            content_hash=content.content_hash,
            actor=identity.username,
            parent_id=parent.id if parent else None,
            frozen_context={
                "content": data,
                "policy": policy,
                "review": verify_frozen(review) if review else None,
                "review_id": review.id if review else None,
                "workflow": workflow,
            },
        )
        self.db.add(row)
        self.db.flush()
        self.receipt(op, identity.username, request, payload, row.id)
        self.record(
            "report.signing_session_opened",
            identity.username,
            row.id,
            {"content_hash": row.content_hash},
        )
        return row

    def account(self, identity, *, password=None, roles=None):
        user = self.db.scalar(select(AppUser).where(AppUser.username == identity.username))
        if (
            not user
            or not user.is_active
            or not user.role
            or user.role.name != identity.role
            or (roles is not None and user.role.name not in roles)
        ):
            raise ReportingError("SIGNER_IDENTITY_INVALID", status=403)
        if password is not None and not verify_password(password, user.password_hash):
            raise ReportingError("REAUTHENTICATION_FAILED", status=401)
        return user

    def get_session(self, ref):
        session = self.db.get(ReportSigningSession, ref, populate_existing=True)
        if session is None:
            raise ReportingError("SIGNING_SESSION_NOT_FOUND", status=404)
        content = self.db.get(ReportContentVersion, session.content_version_id)
        if verify_frozen(content) != session.frozen_context["content"] or (
            content.content_hash != session.content_hash
        ):
            raise ReportingError("CONTENT_HASH_MISMATCH")
        return session

    def cas(self, session, expected_revision, **values):
        changed = self.db.execute(
            update(ReportSigningSession)
            .where(
                ReportSigningSession.id == session.id,
                ReportSigningSession.status == "open",
                ReportSigningSession.revision_no == expected_revision,
                ReportSigningSession.content_hash == session.content_hash,
            )
            .values(revision_no=expected_revision + 1, **values),
            execution_options={"synchronize_session": False},
        )
        if changed.rowcount != 1:
            raise ReportingError("SIGNATURE_REVISION_CONFLICT", status=409)
        self.db.refresh(session)

    def sign(self, session_id, payload, identity):
        user = self.account(identity, password=payload["password"])
        op = "signature:" + session_id
        request = self.request(op, identity.username, payload)
        if request[2]:
            return self.db.get(ReportSignature, request[2]["result_id"])
        session = self.get_session(session_id)
        if payload["content_hash"] != session.content_hash:
            raise ReportingError("CONTENT_HASH_MISMATCH", status=409)
        slot = next(
            (
                s
                for s in session.frozen_context["policy"].get("signature_slots", [])
                if s["signature_slot_id"] == payload["signature_slot_id"]
            ),
            None,
        )
        if (
            not slot
            or slot["role"] != user.role.name
            or (slot.get("allowed_signers") and user.username not in slot["allowed_signers"])
        ):
            raise ReportingError("SIGNATURE_SLOT_FORBIDDEN", status=403)
        if payload["meaning"] not in slot.get("meanings", []):
            raise ReportingError("SIGNATURE_MEANING_INVALID")
        if self.db.scalar(
            select(ReportSignature.id).where(
                ReportSignature.session_id == session_id,
                ReportSignature.signature_slot_id == slot["signature_slot_id"],
            )
        ):
            raise ReportingError("SIGNATURE_SLOT_OCCUPIED", status=409)
        self.cas(session, payload["expected_signature_revision"])
        row = frozen_row(
            self.db,
            ReportSignature,
            uuid4().hex,
            {
                "session_id": session_id,
                "content_hash": session.content_hash,
                "signature_slot_id": slot["signature_slot_id"],
                "region_id": slot["region_id"],
                "signature_revision": session.revision_no,
                "meaning": payload["meaning"],
                "signer_id": str(user.id),
                "signer": user.username,
                "display_name": user.display_name or user.username,
                "role": user.role.name,
                "signed_at": datetime.now(timezone.utc).isoformat(),
                "authentication": "account_password",
            },
            identity.username,
            session_id=session_id,
            signature_slot_id=slot["signature_slot_id"],
            signature_revision=session.revision_no,
        )
        self.receipt(op, identity.username, request, payload, row.id)
        self.record(
            "report.signed",
            identity.username,
            session_id,
            {"signature_id": row.id, "signature_revision": session.revision_no},
        )
        return row

    def revoke(self, session_id, payload, identity):
        self.account(identity, password=payload["password"])
        op = "signature_event:" + session_id
        request = self.request(op, identity.username, payload)
        if request[2]:
            return self.db.get(ReportSignatureEvent, request[2]["result_id"])
        session = self.get_session(session_id)
        signature = self.db.get(ReportSignature, payload["signature_ref"])
        if not signature or signature.session_id != session_id:
            raise ReportingError("SIGNATURE_NOT_FOUND", status=404)
        if signature.actor != identity.username or not payload["reason"].strip():
            raise ReportingError("SIGNATURE_REVOCATION_FORBIDDEN", status=403)
        self.cas(session, payload["expected_signature_revision"])
        row = frozen_row(
            self.db,
            ReportSignatureEvent,
            uuid4().hex,
            {
                "event": "revoked",
                "signature_ref": signature.id,
                "reason": payload["reason"],
                "content_hash": session.content_hash,
                "signature_revision": session.revision_no,
            },
            identity.username,
            session_id=session_id,
            signature_id=signature.id,
            signature_revision=session.revision_no,
        )
        self.receipt(op, identity.username, request, payload, row.id)
        self.record("report.signature_revoked", identity.username, session_id, {"event_id": row.id})
        return row

    def envelope(self, session_id, payload, identity):
        op = "envelope:" + session_id
        request = self.request(op, identity.username, payload)
        if request[2]:
            row = self.db.get(ReportSigningEnvelope, request[2]["result_id"])
            self.envelope_artifact(row)
            return row
        session = self.get_session(session_id)
        if payload["content_hash"] != session.content_hash:
            raise ReportingError("CONTENT_HASH_MISMATCH", status=409)
        content = session.frozen_context["content"]
        if payload["purpose"] == "formal" and (
            content["material_status"] != "ready" or content["template_status"] != "published"
        ):
            raise ReportingError("FORMAL_PUBLICATION_BLOCKED")
        if session.status == "open":
            signatures = list(
                self.db.scalars(
                    select(ReportSignature)
                    .where(ReportSignature.session_id == session_id)
                    .order_by(ReportSignature.signature_revision)
                )
            )
            events = list(
                self.db.scalars(
                    select(ReportSignatureEvent)
                    .where(ReportSignatureEvent.session_id == session_id)
                    .order_by(ReportSignatureEvent.signature_revision)
                )
            )
            revoked = {e.signature_id for e in events}
            active = [s for s in signatures if s.id not in revoked]
            required = {
                s["signature_slot_id"]
                for s in session.frozen_context["policy"].get("signature_slots", [])
                if s.get("required", True)
            }
            if payload["purpose"] == "formal" and required - {s.signature_slot_id for s in active}:
                raise ReportingError("SIGNATURES_INCOMPLETE")
            self.cas(session, payload["signature_revision"], status="frozen")
            snapshot = frozen_row(
                self.db,
                ReportSignatureSnapshot,
                uuid4().hex,
                {
                    "content_hash": session.content_hash,
                    "signature_revision": payload["signature_revision"],
                    "signatures": [{"signature_id": s.id, **verify_frozen(s)} for s in active],
                    "events": [{"event_id": e.id, **verify_frozen(e)} for e in events],
                },
                identity.username,
                session_id=session_id,
                signature_revision=payload["signature_revision"],
            )
            session.frozen_signature_id = snapshot.id
            session.envelope_request = {"actor": identity.username, "payload": deepcopy(payload)}
            self.db.commit()
        elif session.envelope_request != {"actor": identity.username, "payload": payload}:
            raise ReportingError("SIGNATURE_REVISION_CONFLICT", status=409)
        snapshot = self.db.get(ReportSignatureSnapshot, session.frozen_signature_id)
        signatures = verify_frozen(snapshot)
        body = self.runs.body(content["run_id"], content["attempt"])
        body_data = verify_frozen(body)
        final_ast = OutputNode.model_validate(
            {
                "node_id": evidence_hash([body.id, snapshot.id]),
                "kind": "document",
                "children": [
                    deepcopy(body_data["body_ast"]),
                    {
                        "node_id": snapshot.id,
                        "kind": "envelope",
                        "text": "签署记录",
                        "children": [
                            {
                                "node_id": s["signature_id"],
                                "kind": "signature",
                                "text": " · ".join(
                                    [s["display_name"], s["role"], s["meaning"], s["signed_at"]]
                                ),
                                "signature_region_id": s["region_id"],
                            }
                            for s in signatures["signatures"]
                        ],
                    },
                ],
            }
        ).model_dump(mode="json")
        parent = (
            self.db.scalar(
                select(ReportSigningEnvelope.id).where(
                    ReportSigningEnvelope.session_id == session.parent_id
                )
            )
            if session.parent_id
            else None
        )
        row = frozen_row(
            self.db,
            ReportSigningEnvelope,
            evidence_hash([session.id, snapshot.id]),
            {
                "session_id": session.id,
                "content_version_id": session.content_version_id,
                "content_hash": session.content_hash,
                "body_hash": body_data["body_hash"],
                "signature_snapshot_id": snapshot.id,
                "signatures": signatures,
                "review": session.frozen_context["review"],
                "review_id": session.frozen_context["review_id"],
                "final_ast": final_ast,
                "final_ast_hash": evidence_hash(final_ast),
                "purpose": payload["purpose"],
                "parent_id": parent,
            },
            identity.username,
            session_id=session.id,
            content_version_id=session.content_version_id,
            signature_snapshot_id=snapshot.id,
            parent_id=parent,
        )
        self.receipt(op, identity.username, request, payload, row.id)
        self.envelope_artifact(row)
        self.record("report.enveloped", identity.username, row.id, {"hash": row.content_hash})
        return row

    def envelope_artifact(self, envelope):
        artifact = self.db.scalar(
            select(ReportArtifact).where(ReportArtifact.envelope_id == envelope.id)
        )
        if artifact:
            return artifact
        data = verify_frozen(envelope)
        content = verify_frozen(self.db.get(ReportContentVersion, envelope.content_version_id))
        run = self.runs.get(content["run_id"])
        body = self.runs.body(run.id, content["attempt"])
        style = run.source_bundle["contracts"][run.source_bundle["template"]["style_profile_ref"]]
        return self.runs.artifact(
            run,
            body,
            render_docx(data["final_ast"], style["definition"]),
            "docx",
            envelope_id=envelope.id,
            purpose=data["purpose"],
            ast_hash=data["final_ast_hash"],
        )

    def record(self, action, actor, resource, details):
        audit.append(
            self.db, action, actor=actor, entity_iri=resource, details=details, commit=False
        )
