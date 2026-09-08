"""Server-verified immutable contracts, lifecycle decisions and workflow records."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import func, select

from app.models.reporting import (
    ConditionalAssertionBinding,
    ConditionDefinition,
    ContractDecision,
    ContractRecord,
    ContractRecordReview,
    ContractRevision,
    OntologyDecisionRuleRevision,
)
from app.services import audit
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.condition_resolver import expression_type, validate_bound_expression
from app.services.reporting.input_resolver import all_issues, typed_value
from app.services.reporting.template_v2 import (
    ContentNode,
    Expression,
    InputRef,
    Model,
    ReportingError,
    TypeSpec,
)


class ValueContract(Model):
    output_type: TypeSpec
    description: str
    applicable_scope: dict[str, str]
    allowed_roles: list[str] = Field(default_factory=lambda: ["senior_analyst"])


class Parameter(Model):
    type: TypeSpec
    semantic_ref: str
    required: bool = True


class RuleField(Model):
    value: Expression
    category: str
    precondition: Expression | None = None
    claim_ref: str | None = None
    requires_implementation: bool = False
    implementation_parameters: list[str] = Field(default_factory=list)


class RuleBranch(Model):
    branch_id: str
    when: Expression
    fields: dict[str, RuleField]


class RuleDefinition(Model):
    output_type: TypeSpec
    parameters: dict[str, Parameter]
    branches: list[RuleBranch]
    description: str
    applicable_scope: dict[str, str]
    legacy_ref: str | None = None
    migration_issues: list[str] = Field(default_factory=list)


class ConditionContract(Model):
    expression: Expression
    parameters: dict[str, Parameter]
    description: str
    applicable_scope: dict[str, str]


class ConditionBindingContract(Model):
    condition_ref: str
    assertion_id: str
    parameters: dict[str, InputRef]
    parameter_subjects: dict[str, str]
    subject_id: str
    object_id: str | None = None
    applicable_at: str | None = None
    polarity: str
    business_nature: str
    description: str


class ClaimContract(Model):
    category: str
    text: str
    precondition: Expression
    evidence_parameters: list[str]
    parameters: dict[str, Parameter]
    subject_parameter: str
    scope_quantifier: str = "exists"
    universe_parameter: str | None = None
    applicable_scope: dict[str, str]
    description: str


class ViewContract(Model):
    output_type: TypeSpec
    allowed_operations: list[str]
    description: str


class ConversionContract(Model):
    from_unit: str
    to_unit: str
    dimension: str
    factor: str
    offset: str = "0"
    description: str


class SignatureSlot(Model):
    signature_slot_id: str
    region_id: str
    role: str
    allowed_signers: list[str] = Field(default_factory=list)
    meanings: list[str] = Field(default_factory=lambda: ["审核确认"])
    required: bool = True


class PolicyContract(Model):
    require_review: bool = True
    review_roles: list[str] = Field(default_factory=lambda: ["qa"])
    signature_slots: list[SignatureSlot] = Field(default_factory=list)
    workflow_contract_ref: str | None = None
    require_ready_for_submission: bool = True


class StyleContract(Model):
    font: str = "Arial"
    font_size_pt: int = Field(default=11, ge=6, le=32)
    assets: list[str] = Field(default_factory=list)


class StaticContract(Model):
    nodes: list[ContentNode]
    description: str


class PromptContract(Model):
    model: str
    tokenizer_ref: str
    temperature: float = Field(default=0, ge=0, le=2)
    max_input_tokens: int = Field(default=8192, ge=1)
    max_output_tokens: int = Field(default=2048, ge=1)
    timeout_s: int = Field(default=120, ge=1, le=900)
    require_review: bool = True
    allowed_texts: list[str] = Field(
        default_factory=lambda: ["", " ", "\n", "，", "。", "；", "：", "、"]
    )


class MigrationReviewContract(Model):
    issue_hash: str
    legacy_schema_hash: str
    target_semantics_hash: str
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class VocabularyContract(Model):
    values: list[str] = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    description: str


class PropertyTypeContract(Model):
    ontology_release_ref: str
    subject_class_iri: str
    property_iri: str
    output_type: TypeSpec
    description: str


KINDS = {
    "context": ValueContract,
    "parameter": ValueContract,
    "workflow": ValueContract,
    "rule": RuleDefinition,
    "condition": ConditionContract,
    "condition_binding": ConditionBindingContract,
    "claim": ClaimContract,
    "view": ViewContract,
    "conversion": ConversionContract,
    "policy": PolicyContract,
    "style": StyleContract,
    "static": StaticContract,
    "prompt": PromptContract,
    "migration_review": MigrationReviewContract,
    "vocabulary": VocabularyContract,
    "property_type": PropertyTypeContract,
}
REVIEWED_KINDS = (frozenset(KINDS) | {"ontology"}) - {"style"}


def _parameters(expression):
    if expression.op == "parameter":
        yield expression.parameter
    for arg in expression.args:
        yield from _parameters(arg)


def validate_contract(kind, definition):
    if kind not in KINDS:
        raise ReportingError("UNKNOWN_CONTRACT_KIND")
    validated = KINDS[kind].model_validate(definition)
    data = validated.model_dump(mode="json")
    if kind == "context":
        fields = validated.output_type.fields
        if validated.output_type.kind != "record" or not set(fields) <= {
            "generated_at",
            "source_filename",
            "language",
            "timezone",
        }:
            raise ReportingError("BUSINESS_CONTEXT_NOT_ALLOWED")
    if kind in {"rule", "condition", "claim"}:
        expressions = (
            [validated.precondition]
            if kind == "claim"
            else [validated.expression]
            if kind == "condition"
            else [
                expr
                for branch in validated.branches
                for expr in [
                    branch.when,
                    *[f.value for f in branch.fields.values()],
                    *[f.precondition for f in branch.fields.values() if f.precondition],
                ]
            ]
        )
        if any(set(_parameters(expr)) - set(validated.parameters) for expr in expressions):
            raise ReportingError("RULE_PARAMETER_MISMATCH")
        for expression in expressions:
            validate_bound_expression(expression, set(validated.parameters))
        parameters = {name: param.type for name, param in validated.parameters.items()}
        guards = (
            [validated.precondition]
            if kind == "claim"
            else [validated.expression]
            if kind == "condition"
            else [
                e
                for branch in validated.branches
                for e in [
                    branch.when,
                    *[f.precondition for f in branch.fields.values() if f.precondition],
                ]
            ]
        )
        for guard in guards:
            if expression_type(guard, parameters=parameters).kind != "boolean":
                raise ReportingError("CONDITION_TYPE_MISMATCH")
    if kind == "claim":
        required_names = {validated.subject_parameter, *validated.evidence_parameters}
        if validated.universe_parameter:
            required_names.add(validated.universe_parameter)
        if (
            required_names - set(validated.parameters)
            or not validated.evidence_parameters
            or validated.scope_quantifier not in {"exists", "all"}
        ):
            raise ReportingError("CLAIM_PARAMETER_MISMATCH")
        if validated.scope_quantifier == "all" and not validated.universe_parameter:
            raise ReportingError("CLAIM_PARAMETER_MISMATCH")
    if kind == "rule":
        typ = (
            validated.output_type.item_type
            if validated.output_type.kind == "list"
            else validated.output_type
        )
        if typ.kind != "record" or not validated.branches:
            raise ReportingError("RULE_OUTPUT_CONTRACT_INVALID")
        branch_ids = [b.branch_id for b in validated.branches]
        if len(set(branch_ids)) != len(branch_ids):
            raise ReportingError("DUPLICATE_RULE_BRANCH")
        for branch in validated.branches:
            if set(branch.fields) - set(typ.fields):
                raise ReportingError("RULE_OUTPUT_CONTRACT_INVALID")
            for name, output in branch.fields.items():
                if output.category not in {"planned_control", "observed_fact", "risk_decision"}:
                    raise ReportingError("CLAIM_CATEGORY_INVALID")
                if output.category == "observed_fact" and not output.claim_ref:
                    raise ReportingError(
                        "CLAIM_PRECONDITION_UNPROVEN",
                        "Observed statements require a reviewed claim contract",
                    )
                expected = typ.fields[name].type
                if output.value.op == "literal":
                    if all_issues(typed_value(name, expected, output.value.value)):
                        raise ReportingError("RULE_OUTPUT_CONTRACT_INVALID")
                else:
                    from app.services.reporting.template_compiler import compatible

                    if not compatible(
                        expression_type(output.value, parameters=parameters), expected
                    ):
                        raise ReportingError("RULE_OUTPUT_CONTRACT_INVALID")
                if set(output.implementation_parameters) - set(validated.parameters):
                    raise ReportingError("RULE_PARAMETER_MISMATCH")
    if kind == "vocabulary" and (
        len(set(validated.values)) != len(validated.values)
        or set(validated.labels) - set(validated.values)
    ):
        raise ReportingError("VOCABULARY_TYPE_MISMATCH")
    if kind == "conversion":
        try:
            factor, offset = Decimal(validated.factor), Decimal(validated.offset)
            if not factor.is_finite() or not offset.is_finite() or factor <= 0:
                raise ValueError("invalid conversion")
        except (InvalidOperation, ValueError) as exc:
            raise ReportingError("UNIT_INCOMPATIBLE") from exc
    if kind == "claim" and validated.category not in {
        "planned_control",
        "observed_fact",
        "risk_decision",
    }:
        raise ReportingError("CLAIM_CATEGORY_INVALID")
    if kind == "condition_binding" and (
        validated.polarity not in {"affirmed", "negated"}
        or validated.business_nature not in {"observed_fact", "planned_control"}
    ):
        raise ReportingError("CONDITION_BINDING_UNRESOLVED")
    if kind == "view" and set(validated.allowed_operations) - {
        "project",
        "filter",
        "sort",
        "group",
        "join",
        "range",
        "unit_convert",
    }:
        raise ReportingError("UNSUPPORTED_VIEW_OPERATION")
    if kind == "static":

        def nodes(items):
            for node in items:
                yield node
                yield from nodes([*node.children, *node.otherwise, *node.unknown])

        if any(n.kind not in {"text", "paragraph"} for n in nodes(validated.nodes)):
            raise ReportingError("STATIC_DYNAMIC_REFERENCE")
        data["nodes_hash"] = evidence_hash(validated.nodes)
    if kind == "policy":
        slots = [s.signature_slot_id for s in validated.signature_slots]
        if len(slots) != len(set(slots)):
            raise ReportingError("DUPLICATE_SIGNATURE_SLOT")
    return data


class ContractRegistry:
    def __init__(self, db):
        self.db = db

    def create(self, *, kind, family_id, revision_no, definition, actor, server_ontology=False):
        if kind == "ontology" and server_ontology:
            payload = deepcopy(definition)
        else:
            payload = validate_contract(kind, definition)
        legacy_rule = None
        if kind == "rule" and payload.get("legacy_ref"):
            from app.models.ontology_meta import OntologyDecisionRule

            try:
                legacy_rule = self.db.get(OntologyDecisionRule, UUID(payload["legacy_ref"]))
            except ValueError:
                legacy_rule = self.db.scalar(
                    select(OntologyDecisionRule).where(
                        OntologyDecisionRule.rule_key == payload["legacy_ref"]
                    )
                )
            if legacy_rule is None:
                raise ReportingError("LEGACY_RULE_NOT_FOUND", status=404)
            payload["legacy_snapshot"] = {
                "rule_id": str(legacy_rule.id),
                "rule_key": legacy_rule.rule_key,
                "version": legacy_rule.version,
                "status": legacy_rule.status,
                "antecedent": deepcopy(legacy_rule.antecedent),
                "consequent": deepcopy(legacy_rule.consequent),
            }
        previous = self.db.scalar(
            select(func.max(ContractRevision.revision_no)).where(
                ContractRevision.family_id == family_id
            )
        )
        previous_kind = self.db.scalar(
            select(ContractRevision.kind).where(ContractRevision.family_id == family_id).limit(1)
        )
        if previous_kind and previous_kind != kind:
            raise ReportingError("CONTRACT_KIND_MISMATCH")
        if revision_no != (previous or 0) + 1:
            raise ReportingError("CONTRACT_REVISION_CONFLICT", status=409, actual=previous)
        identity = evidence_hash([family_id, revision_no, kind, payload])
        row = ContractRevision(
            id=identity,
            family_id=family_id,
            revision_no=revision_no,
            kind=kind,
            payload=payload,
            content_hash=evidence_hash(payload),
            actor=actor,
        )
        self.db.add(row)
        self.db.flush()
        if legacy_rule:
            self.db.add(
                OntologyDecisionRuleRevision(
                    id=row.id, rule_id=legacy_rule.id, revision_no=revision_no
                )
            )
        if kind == "condition":
            self.db.add(ConditionDefinition(id=row.id))
        elif kind == "condition_binding":
            from app.models.evidence import EvidenceAssertion

            condition = self.db.get(ConditionDefinition, payload["condition_ref"])
            assertion = self.db.get(EvidenceAssertion, payload["assertion_id"])
            if not condition or not assertion:
                raise ReportingError("CONDITION_BINDING_UNRESOLVED")
            self.db.add(
                ConditionalAssertionBinding(
                    id=row.id, condition_id=condition.id, assertion_id=assertion.id
                )
            )
        audit.append(
            self.db,
            "report_contract_created",
            actor=actor,
            entity_iri=row.id,
            details={"kind": kind, "hash": row.content_hash},
            commit=False,
        )
        return row

    def decide(self, ref, decision, *, actor, reason, expected_hash):
        row = self.db.get(ContractRevision, ref)
        if row is None:
            raise ReportingError("CONTRACT_NOT_FOUND", status=404)
        if expected_hash != row.content_hash:
            raise ReportingError("CONTRACT_HASH_MISMATCH", status=409)
        if decision not in {"reviewed", "published", "disabled", "rejected"} or not reason.strip():
            raise ReportingError("CONTRACT_DECISION_INVALID")
        decisions = list(
            self.db.scalars(
                select(ContractDecision)
                .where(ContractDecision.contract_id == ref)
                .order_by(ContractDecision.revision_no)
            )
        )
        if (
            decision == "published"
            and row.kind in REVIEWED_KINDS
            and (not decisions or decisions[-1].decision != "reviewed")
        ):
            raise ReportingError("CONTRACT_REVIEW_REQUIRED")
        if decision in {"reviewed", "published"} and row.payload.get("migration_issues"):
            raise ReportingError("MIGRATION_UNRESOLVED")
        if decisions and (
            decisions[-1].decision in {"disabled", "rejected"}
            or (decisions[-1].decision == "published" and decision != "disabled")
        ):
            raise ReportingError("CONTRACT_IMMUTABLE", status=409)
        if decision == "published":
            from app.services.reporting.report_run_service import _contract_refs
            from app.services.reporting.template_compiler import compatible

            for dependency in set(_contract_refs(row.payload)):
                if dependency == ref:
                    raise ReportingError("DEPENDENCY_CYCLE")
                self.load(dependency)
            if row.kind == "rule":
                for branch in row.payload["branches"]:
                    for output in branch["fields"].values():
                        if not output.get("claim_ref"):
                            continue
                        claim = self.load(output["claim_ref"])
                        definition = claim["definition"]
                        if (
                            claim["kind"] != "claim"
                            or definition["category"] != output["category"]
                            or output["value"]["op"] != "literal"
                            or output["value"]["value"] != definition["text"]
                        ):
                            raise ReportingError("CLAIM_TEXT_MISMATCH")
                        for name, parameter in definition["parameters"].items():
                            actual = row.payload["parameters"].get(name)
                            if not actual or not compatible(
                                TypeSpec.model_validate(actual["type"]),
                                TypeSpec.model_validate(parameter["type"]),
                            ):
                                raise ReportingError("CLAIM_PARAMETER_MISMATCH")
        payload = {
            "contract_id": ref,
            "decision": decision,
            "reason": reason,
            "definition_hash": row.content_hash,
        }
        entry = ContractDecision(
            id=uuid4().hex,
            contract_id=ref,
            decision=decision,
            revision_no=len(decisions) + 1,
            payload=payload,
            content_hash=evidence_hash(payload),
            actor=actor,
        )
        self.db.add(entry)
        self.db.flush()
        audit.append(
            self.db,
            "report_contract_" + decision,
            actor=actor,
            entity_iri=ref,
            details={"decision_id": entry.id, "definition_hash": row.content_hash},
            commit=False,
        )
        return entry

    def load(self, ref, *, published=True):
        from app.services.reasoning.rule_service import method
        from app.services.reporting.template_preparation import builtin_profile

        builtin = method(ref) or builtin_profile(ref)
        if builtin:
            return builtin
        row = self.db.get(ContractRevision, ref)
        if row is None:
            raise ReportingError("CONTRACT_NOT_FOUND", status=404, actual=ref)
        if evidence_hash(row.payload) != row.content_hash:
            raise ReportingError("CONTRACT_HASH_MISMATCH")
        decisions = list(
            self.db.scalars(
                select(ContractDecision)
                .where(ContractDecision.contract_id == ref)
                .order_by(ContractDecision.revision_no)
            )
        )
        state = decisions[-1].decision if decisions else "draft"
        if published and state != "published":
            raise ReportingError(
                "RULE_REVISION_NOT_PUBLISHED" if row.kind == "rule" else "CONTRACT_NOT_PUBLISHED",
                actual=ref,
            )
        definition = deepcopy(row.payload)
        reviewed = next((r for r in reversed(decisions) if r.decision == "reviewed"), None)
        if reviewed:
            definition["review_ref"] = reviewed.id
            if row.kind == "rule":
                definition["claims_reviewed"] = True
            if row.kind == "condition_binding":
                definition["association_review_ref"] = reviewed.id
        return {
            "contract_id": row.id,
            "kind": row.kind,
            "family_id": row.family_id,
            "revision_no": row.revision_no,
            "status": state,
            "is_disabled": state == "disabled",
            "definition": definition,
            "definition_hash": evidence_hash(definition),
            "stored_definition_hash": row.content_hash,
            "decision_refs": [r.id for r in decisions],
        }

    def record(self, ref, *, record_key, revision_no, values, subject_id, applicable_at, actor):
        contract = self.load(ref)
        if contract["kind"] not in {"workflow", "parameter"}:
            raise ReportingError("BUSINESS_RECORD_SOURCE_NOT_ALLOWED")
        typ = TypeSpec.model_validate(contract["definition"]["output_type"])
        scope = contract["definition"].get("applicable_scope", {})
        if any(
            scope.get(key) and scope[key] != value
            for key, value in (
                ("subject_id", subject_id),
                ("applicable_at", applicable_at),
            )
        ):
            raise ReportingError("RECORD_SCOPE_MISMATCH")
        value = typed_value("record", typ, values)
        if any(i.state in {"conflict", "invalid"} for i in all_issues(value)):
            raise ReportingError("INPUT_TYPE_MISMATCH")
        payload = {
            "values": values,
            "subject_id": subject_id,
            "applicable_at": applicable_at,
            "contract_id": ref,
        }
        record = ContractRecord(
            id=uuid4().hex,
            contract_id=ref,
            record_key=record_key,
            revision_no=revision_no,
            payload=payload,
            content_hash=evidence_hash(payload),
            actor=actor,
        )
        self.db.add(record)
        self.db.flush()
        audit.append(
            self.db,
            "report_workflow_record_created",
            actor=actor,
            entity_iri=record.id,
            details={"contract_id": ref, "hash": record.content_hash},
            commit=False,
        )
        return record

    def review_record(self, ref, *, decision, reason, expected_hash, actor):
        record = self.db.get(ContractRecord, ref)
        if record is None:
            raise ReportingError("RECORD_NOT_FOUND", status=404)
        if expected_hash != record.content_hash:
            raise ReportingError("SOURCE_VERSION_CHANGED", status=409)
        if decision not in {"approved", "rejected"} or not reason.strip():
            raise ReportingError("RECORD_REVIEW_INVALID")
        payload = {
            "record_id": ref,
            "record_hash": record.content_hash,
            "decision": decision,
            "reason": reason,
        }
        review = ContractRecordReview(
            id=uuid4().hex,
            record_id=ref,
            decision=decision,
            payload=payload,
            content_hash=evidence_hash(payload),
            actor=actor,
        )
        self.db.add(review)
        self.db.flush()
        audit.append(
            self.db,
            "report_workflow_record_reviewed",
            actor=actor,
            entity_iri=ref,
            details={"review_id": review.id, "decision": decision},
            commit=False,
        )
        return review

    def load_record(self, ref):
        record = self.db.get(ContractRecord, ref)
        if record is None:
            raise ReportingError("RECORD_NOT_FOUND", status=404)
        if evidence_hash(record.payload) != record.content_hash:
            raise ReportingError("SOURCE_VERSION_CHANGED")
        reviews = list(
            self.db.scalars(
                select(ContractRecordReview)
                .where(ContractRecordReview.record_id == ref)
                .order_by(ContractRecordReview.created_at, ContractRecordReview.id)
            )
        )
        review = reviews[-1] if reviews else None
        return {
            **deepcopy(record.payload),
            "record_id": ref,
            "revision_no": record.revision_no,
            "record_key": record.record_key,
            "record_hash": record.content_hash,
            "state": "ready" if review and review.decision == "approved" else "pending_review",
            "provenance": {
                "kind": "workflow",
                "record_id": ref,
                "record_hash": record.content_hash,
                "actor": record.actor,
                "review_ref": review.id if review else None,
                "contract_id": record.contract_id,
            },
        }
