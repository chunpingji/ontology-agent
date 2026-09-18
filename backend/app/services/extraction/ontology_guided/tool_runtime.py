"""Local, typed dispatch over the current task's already authorized materials.

Handlers return proposals/results only. The caller owns counting attempts,
confirming result references, persistence and every graph mutation.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from pydantic import ValidationError

from app.schemas.evidence import EvidenceModel
from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id
from app.services.extraction.external_records import (
    ExternalInstanceReader,
    InstanceQuery,
    build_instance_query,
)
from app.services.extraction.literal_normalizer import canonical_unit
from app.services.extraction.ontology_guided.claim_protocol import (
    EntityDependencyView,
    EntityProposal,
    ExternalLinkProposal,
    ExtractionProfile,
    PropertyProposal,
    QuantityPolicy,
    Quote,
    RelationProposal,
    SchemaCard,
    VerificationTargetSpec,
    VerifiedTarget,
    compile_schema_card,
    ref_key,
)
from app.services.extraction.ontology_guided.context import (
    ContextAuthorization,
    TaskContext,
    build_authorized_context,
    build_retrieval_authorization,
)
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    GraphNode,
    LocalMenu,
    MetadataSnapshot,
    OntologySnapshot,
    SlotSpec,
    SourceMention,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.field_bindings import contains, field_bindings
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import plan_slot
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
from app.services.extraction.ontology_guided.tool_contracts import (
    RELATION_PROFILE,
    TOOL_DEFINITIONS,
    AnchorData,
    BindingData,
    CheckClaimBindingArgs,
    EvidenceData,
    EvidenceTable,
    EvidenceUnit,
    FindReferentCandidatesArgs,
    GetSchemaCardArgs,
    InspectEvidenceArgs,
    InstanceData,
    MentionCoverage,
    MentionData,
    MentionLimits,
    MentionSuggestion,
    MetricData,
    ProposeMentionsArgs,
    ProposeRepairArgs,
    QueryInstancesArgs,
    ReferentCandidate,
    ReferentCandidateData,
    RelationValidationData,
    RepairData,
    ResolveSourceAnchorArgs,
    RetrievalCoverage,
    RetrievalData,
    RetrieveEvidenceArgs,
    SchemaCardData,
    ShaclData,
    ToolCall,
    ToolErrorResult,
    ToolIssue,
    ToolName,
    ToolResult,
    ToolStage,
    ValidateGraphArgs,
    ValidateMetricArgs,
)
from app.services.extraction.tool_validation.evidence import _raw_span, _unit_binding
from app.services.extraction.tool_validation.mentions import propose_vocabulary_mentions
from app.services.extraction.tool_validation.vocabulary import (
    VocabularyOverlay,
    build_extraction_vocabulary,
)
from app.services.llm.model_runtime import ModelCancelled, ModelWaitFailure
from app.services.llm.model_runtime import check_cancelled as runtime_check_cancelled

_ROLES = {
    **{role: role for role in ("target", "header", "note", "parent", "binding", "counterevidence")},
    "record_heading_or_header": "header",
    "parent_table_context": "parent",
    "table_note_metadata": "note",
    "field_group_binding": "binding",
    "named_object_binding": "binding",
    "subject_binding": "binding",
    "required_context": "binding",
    "retrieval_group_binding": "binding",
    "resolved_reference_chain": "binding",
}


@dataclass(frozen=True)
class ToolLimits:
    max_result_tokens: int
    max_calls_per_response: int = 8
    max_calls_per_lineage: int = 16
    max_external_candidates: int = 8
    max_evidence_units_per_call: int = 16
    max_returned_mentions: int = 64
    max_retrieval_records: int = 4

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in self.__dict__.values()):
            raise ValueError("tool limits must be positive integers")


@dataclass(frozen=True)
class ToolContext:
    task: RecognitionTask
    context: TaskContext
    index: RecordIndex
    menu: LocalMenu
    profile: ExtractionProfile
    scope: TraversalScope
    stage: ToolStage
    recovery_kind: Literal["none", "evidence", "reproposal"]
    frozen_claims: Mapping[str, VerificationTargetSpec]
    local_ref_map: Mapping[str, VersionedRef]
    entity_dependencies: Mapping[str, EntityDependencyView]
    limits: ToolLimits
    measure_result_tokens: Callable[[str], int]
    cards: Mapping[str, SchemaCard] = field(default_factory=dict)
    authorization: ContextAuthorization | None = None
    verified_claims: Mapping[str, VerifiedTarget] = field(default_factory=dict)
    binding_result: BindingData | None = None
    metric_result: MetricData | None = None
    relation_checks: Mapping[str, RelationValidationData] = field(default_factory=dict)
    ontology_snapshot: OntologySnapshot | None = None
    vocabulary_overlay: VocabularyOverlay | None = None
    mention_extractor: object | None = None
    registered_mentions: Mapping[str, SourceMention] = field(default_factory=dict)
    instance_reader: ExternalInstanceReader | None = None
    external_source_ids: tuple[str, ...] = ()
    instance_queries: Mapping[str, InstanceQuery] = field(default_factory=dict)
    metadata: MetadataSnapshot | None = None
    base_context: TaskContext | None = None
    target_seed: VerificationTarget | None = None
    run_fingerprint: str | None = None
    subject_node: GraphNode | None = None
    evidence_revision: int = 1
    check_cancelled: Callable[[], None] = runtime_check_cancelled
    reference_resolution: bool = False

    def __post_init__(self):
        if self.stage not in ("discovery", "verification", "finalize"):
            raise ValueError("invalid tool stage")
        if self.recovery_kind not in ("none", "evidence", "reproposal"):
            raise ValueError("invalid recovery mode")
        for name in ("frozen_claims", "local_ref_map", "entity_dependencies", "cards",
                     "verified_claims", "registered_mentions", "instance_queries",
                     "relation_checks"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


class _ToolFailure(ValueError):
    def __init__(self, code: str, field_path: str | None = None):
        super().__init__(code)
        self.code, self.field_path = code, field_path
        self.reason_code = code


def _issue(code, path=None, evidence_ids=()):
    message = f"检查未通过：{code}；请核对当前授权引用和参数。"
    if code == "result_budget_exceeded":
        message = (
            "完整工具结果超过预算，本次未返回数据或授予引用；"
            "可缩小原文单元列表或谓词范围后在剩余调用预算内重试。"
        )
    elif code == "citation_quote_not_in_source" and path == "/context_text":
        message = "context_text 必须为包含 quote 的同源逐字片段；不需要消歧时请填 null。"
    return ToolIssue(
        code=code,
        field_path=path,
        message=message,
        evidence_ids=list(evidence_ids),
    )


def _error(code, path=None, *, status="blocked", result_type=ToolErrorResult):
    return result_type(status=status, data=None, evidence_refs=[], issues=[_issue(code, path)])


def _pointer(location):
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in location)


def parse_tool_arguments(name: ToolName, arguments_json: str) -> EvidenceModel:
    definition = TOOL_DEFINITIONS.get(name)
    if definition is None:
        raise _ToolFailure("unknown_tool")

    def reject(_value):
        raise ValueError("nonstandard JSON constant")

    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate key")
            obj[key] = value
        return obj

    try:
        json.loads(arguments_json, parse_constant=reject, object_pairs_hook=unique_object)
    except (TypeError, ValueError) as exc:
        raise _ToolFailure("invalid_tool_arguments") from exc
    try:
        parsed = definition.args_type.model_validate_json(arguments_json, strict=True)
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0]
        raise _ToolFailure(
            "invalid_tool_arguments", _pointer(error["loc"]) if error["loc"] else None
        ) from exc
    if isinstance(parsed, InspectEvidenceArgs) and (
        not parsed.evidence_ids or len(parsed.evidence_ids) != len(set(parsed.evidence_ids))
    ):
        raise _ToolFailure("invalid_tool_arguments", "/evidence_ids")
    return parsed


def _permitted(name, ctx, caller):
    definition = TOOL_DEFINITIONS.get(name)
    if name == "find_referent_candidates" and not ctx.reference_resolution:
        return False
    if name == "validate_graph" and caller == "model" and (
        ctx.stage != "verification"
        or not any(t.target_kind == "relation" for t in ctx.frozen_claims.values())
    ):
        return False
    return (
        definition is not None
        and caller in ("model", "controller")
        and ctx.stage in definition.allowed_stages
        and (caller == "controller" or definition.model_callable)
        and (name != "propose_repair" or ctx.recovery_kind != "none")
    )


def build_tool_definitions(ctx: ToolContext, stage: str, *, strict: bool = False) -> list[dict]:
    if stage != ctx.stage:
        raise ValueError("tool stage differs from current context")
    unavailable = set()
    if ctx.mention_extractor is None or ctx.ontology_snapshot is None:
        unavailable.add("propose_mentions")
    if ctx.instance_reader is None or not ctx.external_source_ids:
        unavailable.add("query_instances")
    if any(value is None for value in (
        ctx.metadata, ctx.base_context, ctx.target_seed, ctx.run_fingerprint, ctx.subject_node,
    )):
        unavailable.add("retrieve_evidence")
    if not ctx.frozen_claims:
        unavailable.update({"check_claim_binding", "propose_repair"})
    if not any(t.target_kind == "relation" for t in ctx.frozen_claims.values()):
        unavailable.add("validate_graph")
    definitions = [
        definition.to_openai(strict=strict)
        for name, definition in TOOL_DEFINITIONS.items()
        if name in _HANDLERS and name not in unavailable and _permitted(name, ctx, "model")
    ]
    for definition in definitions:
        if definition["name"] == "validate_graph":
            properties = definition["parameters"]["properties"]
            properties["claim_id"]["enum"] = sorted(
                t.claim_ref.id for t in ctx.frozen_claims.values() if t.target_kind == "relation"
            )
            properties["shape_profile_id"]["enum"] = [RELATION_PROFILE]
        if definition["name"] == "query_instances":
            properties = definition["parameters"]["properties"]
            properties["source_ids"]["items"]["enum"] = sorted(set(ctx.external_source_ids))
            properties["class_iri"]["enum"] = sorted(_instance_classes(ctx))
        if definition["name"] == "find_referent_candidates":
            properties = definition["parameters"]["properties"]
            properties["class_iris"]["items"]["enum"] = sorted(_instance_classes(ctx))
            properties["scope_id"]["enum"] = [ctx.scope.scope_id]
    return definitions


def _instance_classes(ctx):
    return {ctx.task.subject.class_iri} | {
        iri for edge in ctx.menu.relationships for iri in edge.range_class_iris
    }


def dispatch_tool(
    call: ToolCall,
    ctx: ToolContext,
    *,
    caller: Literal["model", "controller"] = "model",
) -> ToolResult:
    from uuid import uuid4

    from app.services.llm.model_runtime import observe

    operation_id = uuid4().hex
    observe("operation_start", operation_id=operation_id,
            kind="validation" if caller == "controller" else "tool",
            name=call.name, arguments=call.arguments_json, caller=caller)
    try:
        result = _dispatch_tool(call, ctx, caller=caller)
    except BaseException:
        observe("operation_end", operation_id=operation_id, status="interrupted")
        raise
    validation = getattr(result.data, "validation_status", None)
    display_status = (
        "blocked" if validation == "failed" else "incomplete" if validation == "incomplete"
        else result.status
    )
    observe("operation_end", operation_id=operation_id, status=display_status,
            result=result.model_dump(mode="json"))
    return result


def _dispatch_tool(
    call: ToolCall,
    ctx: ToolContext,
    *,
    caller: Literal["model", "controller"] = "model",
) -> ToolResult:
    """Dispatch one already counted attempt; never confirm or mutate its results."""
    ctx.check_cancelled()
    if call.name not in TOOL_DEFINITIONS:
        return _error("unknown_tool")
    if not _permitted(call.name, ctx, caller):
        return _error("tool_not_allowed")
    result_type = ToolErrorResult
    try:
        args = parse_tool_arguments(call.name, call.arguments_json)
        result_type = TOOL_DEFINITIONS[call.name].result_type
        handler = _HANDLERS.get(call.name)
        if handler is None:
            return _error("tool_unavailable", result_type=result_type)
        _validate_context(ctx)
        result = handler(args, ctx)
        ctx.check_cancelled()
        if caller == "model":
            encoded = canonical_json(result.model_dump(mode="json"))
            size = ctx.measure_result_tokens(encoded)
            if type(size) is not int or size < 0:
                raise ValueError("invalid result token measurement")
            if size > ctx.limits.max_result_tokens:
                return _error("result_budget_exceeded", result_type=result_type)
        return result
    except _ToolFailure as exc:
        return _error(exc.code, exc.field_path, result_type=result_type)
    except (ModelCancelled, ExecutionLost, ModelWaitFailure):
        raise
    except Exception:
        return _error("tool_execution_failed", status="error", result_type=result_type)


def to_function_call_output(call_id: str, result: ToolResult) -> dict:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": canonical_json(result.model_dump(mode="json")),
    }


def _validate_context(ctx):
    target = ctx.context.target
    if (
        ctx.menu.subject != ctx.task.subject
        or target.subject_ref != ctx.task.subject
        or target.task_id != ctx.task.task_id
        or ctx.context.record_id != ctx.task.record_id
        or target.document_context.document_hash != ctx.index.ir.document_hash
    ):
        raise _ToolFailure("reference_version_mismatch")
    if ctx.authorization is not None:
        authorization = ctx.authorization
        ir = ctx.index.ir
        if (
            authorization.task_id != ctx.task.task_id
            or authorization.ir_identity.document_hash != ir.document_hash
            or authorization.ir_identity.parser_version != ir.parser_version
            or authorization.ir_identity.structure_hash != ir.structure_hash
        ):
            raise _ToolFailure("reference_version_mismatch")
    for fragment in ctx.context.fragments:
        try:
            if ctx.index.ir.resolve(fragment.anchor) != fragment.text:
                raise ValueError("source changed")
        except ValueError as exc:
            raise _ToolFailure("reference_version_mismatch") from exc
        if fragment.purpose not in _ROLES:
            raise _ToolFailure("evidence_role_unresolved")
        if ctx.authorization is not None and not any(
            item.evidence_id == fragment.anchor.evidence_id
            and item.role == fragment.purpose
            and item.fact_eligible == fragment.fact_eligible
            and item.span_start == (fragment.anchor.span_start or 0)
            and item.span_end == (fragment.anchor.span_end or len(fragment.text))
            for item in ctx.authorization.fragments
        ):
            raise _ToolFailure("reference_outside_scope")


def _get_schema_card(args: GetSchemaCardArgs, ctx: ToolContext) -> ToolResult[SchemaCardData]:
    if args.subject_id != ctx.task.subject.entity_id:
        raise _ToolFailure("reference_outside_scope", "/subject_id")
    if args.predicate_iri is not None and args.predicate_iri not in {
        predicate.iri for predicate in (*ctx.menu.properties, *ctx.menu.relationships)
    }:
        raise _ToolFailure("reference_outside_scope", "/predicate_iri")
    card = compile_schema_card(
        ctx.menu, predicate_iri=args.predicate_iri, profile=ctx.profile, scope=ctx.scope
    )
    return ToolResult[SchemaCardData](
        status="ok",
        data=SchemaCardData(
            cards=[card],
            unsupported_constraints=card.unsupported_constraints,
        ),
        evidence_refs=[],
        issues=[],
    )


def _record_id(fragment, ctx):
    anchor = fragment.anchor
    if ctx.authorization is not None:
        matches = {
            item.record_id
            for item in ctx.authorization.fragments
            if item.evidence_id == anchor.evidence_id
            and item.role == fragment.purpose
            and item.span_start == (anchor.span_start or 0)
            and item.span_end == (anchor.span_end or len(fragment.text))
        }
        if len(matches) == 1:
            return matches.pop()
        raise _ToolFailure("reference_outside_scope")
    from app.services.extraction.ontology_guided.context import fragment_record_id

    try:
        return fragment_record_id(fragment, ctx.index, ctx.context.record_id)
    except ValueError as exc:
        raise _ToolFailure("evidence_record_ambiguous") from exc


def _table(evidence_id, ctx):
    unit = ctx.index.ir.unit(evidence_id)
    if not unit.table_path:
        return None
    tables = ctx.index.tables
    rows, columns = tables.rows(unit, data_only=False), tables.columns(unit)
    headers = []
    for fragment in ctx.context.fragments:
        header = ctx.index.ir.unit(fragment.anchor.evidence_id)
        if (
            tables.is_header(header)
            and header.table_path == unit.table_path
            and columns & tables.columns(header)
        ):
            headers.append(header.evidence_id)
    return EvidenceTable(
        table_path=unit.table_path,
        source_cell_id=unit.source_cell_id,
        logical_rows=sorted(rows),
        logical_columns=sorted(columns),
        column_header_refs=list(dict.fromkeys(headers)),
        cell_structure_valid=bool(rows and columns and unit.source_cell_id in tables.cells),
    )


def _inspect_evidence(args: InspectEvidenceArgs, ctx: ToolContext) -> ToolResult[EvidenceData]:
    ids = args.evidence_ids
    if not ids or len(ids) != len(set(ids)):
        raise _ToolFailure("invalid_tool_arguments", "/evidence_ids")
    if len(ids) > ctx.limits.max_evidence_units_per_call:
        raise _ToolFailure("tool_budget_exhausted", "/evidence_ids")
    allowed = {fragment.anchor.evidence_id for fragment in ctx.context.fragments}
    for position, identity in enumerate(ids):
        if identity not in allowed:
            raise _ToolFailure("reference_outside_scope", f"/evidence_ids/{position}")
    units = []
    for fragment in ctx.context.fragments:
        anchor = fragment.anchor
        if anchor.evidence_id not in ids:
            continue
        start = anchor.span_start or 0
        units.append(
            EvidenceUnit(
                evidence_id=anchor.evidence_id,
                record_id=_record_id(fragment, ctx),
                text=fragment.text,
                span_start=start,
                span_end=start + len(fragment.text),
                role=_ROLES[fragment.purpose],
                fact_eligible=fragment.fact_eligible,
                table=_table(anchor.evidence_id, ctx),
            )
        )
    return ToolResult[EvidenceData](
        status="ok",
        data=EvidenceData(
            units=units,
            omitted_ids=[],
            coverage_complete=True,
        ),
        evidence_refs=list(ids),
        issues=[],
    )


def _quote(quote: Quote, ctx, *, fact_required=False):
    if quote.evidence_id not in {f.anchor.evidence_id for f in ctx.context.fragments}:
        raise _ToolFailure("reference_outside_scope")
    try:
        anchor, text = resolve_fragment_quote(
            quote.evidence_id,
            quote.text,
            ctx.context.fragments,
            fact_required=fact_required,
            context_text=quote.context_text,
        )
    except ValueError as exc:
        code = {
            "ambiguous_source_quote": "citation_quote_ambiguous",
            "ambiguous_allowed_source_fragments": "citation_quote_ambiguous",
            "source_quote_outside_scope": "reference_outside_scope",
            "source_excerpt_mismatch": "citation_quote_not_in_source",
        }.get(str(exc))
        raise _ToolFailure(code or "citation_quote_not_in_source") from exc
    if ctx.index.ir.resolve(anchor) != text:
        raise _ToolFailure("reference_version_mismatch")
    return anchor


def _resolve_source_anchor(
    args: ResolveSourceAnchorArgs, ctx: ToolContext
) -> ToolResult[AnchorData]:
    if args.evidence_id not in {f.anchor.evidence_id for f in ctx.context.fragments}:
        raise _ToolFailure("reference_outside_scope", "/evidence_id")
    if args.context_text is not None and (
        not args.context_text or args.quote not in args.context_text
    ):
        raise _ToolFailure("citation_quote_not_in_source", "/context_text")
    anchor = _quote(
        Quote(evidence_id=args.evidence_id, text=args.quote, context_text=args.context_text), ctx
    )
    key = ctx.index.physical_mention_key(anchor.evidence_id, anchor.span_start, anchor.span_end)
    mention_ref = stable_id("source-mention", [ctx.index.ir.analysis_id, key, args.quote])
    return ToolResult[AnchorData](
        status="ok",
        data=AnchorData(
            anchor=anchor,
            text=args.quote,
            mention_ref=mention_ref,
        ),
        evidence_refs=[anchor.evidence_id],
        issues=[],
    )


def _entity(local_id, claim, ctx):
    ref = ctx.local_ref_map.get(local_id)
    if ref is None or ref_key(ref) not in {ref_key(r) for r in claim.dependency_refs}:
        raise _ToolFailure("reference_version_mismatch", "/claim_id")
    for entity in ctx.entity_dependencies.values():
        if entity.entity_ref == ref:
            EntityDependencyView.model_validate(entity.model_dump(mode="json"), strict=True)
            for anchor in entity.source_refs:
                if not any(contains(fragment.anchor, anchor) for fragment in ctx.context.fragments):
                    raise _ToolFailure("reference_outside_scope", "/claim_id")
                ctx.index.ir.resolve(anchor)
            root = (
                entity.grounding_kind == "document_root"
                and ref == ctx.context.target.document_context.root_ref
            )
            return entity.class_iri, entity.source_refs, root
    for target in ctx.frozen_claims.values():
        if target.target_kind == "entity" and target.claim_ref == ref:
            proposal = target.payload
            quotes = proposal.mentions or [item.quote for item in proposal.record_components]
            return proposal.class_iri, [_quote(q, ctx) for q in quotes], False
    raise _ToolFailure("reference_outside_scope", "/claim_id")


def _owner_issues(owners, value, ctx):
    if not value.table_path:
        return []
    tables = ctx.index.tables
    target = ctx.index.ir.unit(value.evidence_id)
    value_rows = tables.rows(target)
    table_owners = [ctx.index.ir.unit(owner.evidence_id) for owner in owners if owner.table_path]
    if not table_owners:
        return [_issue("owner_identity_unproven")]
    matching = [
        owner
        for owner in table_owners
        if owner.table_path == target.table_path and tables.rows(owner) & value_rows
    ]
    if not matching:
        return [_issue("owner_row_mismatch")]
    if all(
        value_rows < tables.rows(owner) and owner.source_cell_id != target.source_cell_id
        for owner in matching
    ):
        return [_issue("owner_record_ambiguous")]
    return []


def _check_claim_binding(args: CheckClaimBindingArgs, ctx: ToolContext) -> ToolResult[BindingData]:
    claim = ctx.frozen_claims.get(args.claim_id)
    if claim is None:
        raise _ToolFailure("reference_outside_scope", "/claim_id")
    try:
        VerificationTargetSpec.model_validate(claim.model_dump(mode="json"), strict=True)
    except ValidationError as exc:
        raise _ToolFailure("reference_version_mismatch", "/claim_id") from exc
    if claim.scope != ctx.scope:
        raise _ToolFailure("reference_outside_scope", "/claim_id")
    payload = claim.payload
    resolved, issues, source_unit = [], [], None
    if isinstance(payload, EntityProposal):
        card = compile_schema_card(
            ctx.menu, predicate_iri=None, profile=ctx.profile, scope=ctx.scope
        )
        if payload.class_iri not in card.class_iris:
            issues.append(_issue("entity_type_outside_menu"))
        quotes = payload.mentions or [part.quote for part in payload.record_components]
        resolved.extend(_quote(quote, ctx) for quote in quotes)
    else:
        subject_class, owners, root = _entity(payload.subject_id, claim, ctx)
        if ctx.local_ref_map[payload.subject_id] != VersionedRef(
            id=ctx.task.subject.entity_id,
            revision=ctx.task.subject.revision,
        ):
            raise _ToolFailure("reference_outside_scope", "/claim_id")
        resolved.extend(owners)
        if isinstance(payload, ExternalLinkProposal):
            resolved.extend(_quote(quote, ctx) for quote in payload.identity_support)
        else:
            predicate = next(
                (
                    item
                    for item in (*ctx.menu.properties, *ctx.menu.relationships)
                    if item.iri == payload.predicate_iri
                ),
                None,
            )
            expected = SlotSpec if isinstance(payload, PropertyProposal) else EdgeSpec
            if not isinstance(predicate, expected) or subject_class != ctx.task.subject.class_iri:
                raise _ToolFailure("reference_outside_scope", "/claim_id")
            if predicate.constraint_status != "resolved":
                issues.append(_issue("constraint_unresolved"))
            qualifiers = payload.qualifiers
            resolved.extend(_quote(q, ctx) for q in qualifiers.condition_support)
            resolved.extend(_quote(q.quote, ctx) for q in qualifiers.scope_qualifiers)
            if isinstance(payload, PropertyProposal):
                value = _quote(payload.value_quote, ctx, fact_required=True)
                fields = [_quote(q, ctx) for q in payload.field_support]
                units = [_quote(q, ctx) for q in payload.unit_support]
                resolved.extend([value, *fields, *units])
                if not root:
                    issues.extend(_owner_issues(owners, value, ctx))
                bindings = ctx.context.field_bindings or field_bindings(
                    ctx.index, ctx.context.record_id
                )
                matching = [
                    binding
                    for binding in bindings
                    if any(contains(ref, value) for ref in binding.target_value_refs)
                ]
                if matching:
                    if not any(
                        any(
                            contains(label, field)
                            for label in binding.label_refs
                            for field in fields
                        )
                        for binding in matching
                    ):
                        issues.append(
                            _issue(
                                "field_column_mismatch" if fields else "field_role_source_missing"
                            )
                        )
                    if payload.bridge_kind in ("role_mapped_table", "owned_field_group"):
                        kind = (
                            "table" if payload.bridge_kind == "role_mapped_table" else "field_group"
                        )
                        if not any(binding.kind == kind for binding in matching):
                            issues.append(_issue("field_binding_kind_mismatch"))
                elif value.table_path or payload.bridge_kind in (
                    "role_mapped_table",
                    "owned_field_group",
                ):
                    issues.append(_issue("field_binding_missing"))
                elif not fields:
                    issues.append(_issue("field_role_source_missing"))
                sources = {}
                for fragment in ctx.context.fragments:
                    identity = fragment.anchor.evidence_id
                    table = _table(identity, ctx)
                    sources[identity] = {
                        "text": ctx.index.ir.unit(identity).text,
                        "column_header_refs": table.column_header_refs if table else [],
                    }
                raw = {"ref": value.evidence_id, "start": value.span_start, "end": value.span_end}
                try:
                    # Only the physical substring/qualifier boundary gate is reused.
                    # Full intervals remain intact; this does not require a scalar.
                    _raw_span(
                        payload.value_quote.text,
                        {**raw, "quote": payload.value_quote.text},
                        {},
                        sources,
                    )
                except ValueError as exc:
                    code = str(exc)
                    issues.append(
                        _issue(
                            "quantity_quote_incomplete"
                            if code == "scalar_value_required"
                            else code,
                        )
                    )
                for unit in units:
                    text, issue = _unit_binding(
                        raw,
                        {
                            "ref": unit.evidence_id,
                            "quote": ctx.index.ir.resolve(unit),
                            "start": unit.span_start,
                            "end": unit.span_end,
                        },
                        sources,
                    )
                    if issue:
                        issues.append(_issue(issue))
                    if source_unit is not None and canonical_unit(text) != canonical_unit(
                        source_unit
                    ):
                        issues.append(_issue("source_unit_conflict"))
                    source_unit = text
                if not units and (
                    predicate.canonical_unit
                    or any(
                        policy.predicate_iri == predicate.iri
                        and policy.unit_requirement == "physical"
                        for policy in ctx.profile.quantity_policies
                    )
                ):
                    issues.append(_issue("unit_source_missing"))
            elif isinstance(payload, RelationProposal):
                for identity in payload.object_ids:
                    class_iri, anchors, _ = _entity(identity, claim, ctx)
                    resolved.extend(anchors)
                    if class_iri not in predicate.range_class_iris:
                        issues.append(_issue("range_mismatch"))
                resolved.extend(
                    _quote(q, ctx) for q in (*payload.bridge_support, *payload.selection_support)
                )
                if not payload.bridge_support:
                    issues.append(_issue("bridge_source_missing"))
                if len(payload.object_ids) > 1 and not payload.selection_support:
                    issues.append(_issue("selection_source_missing"))
    incomplete = {
        "constraint_unresolved",
        "unit_source_missing",
        "field_role_source_missing",
        "field_binding_missing",
        "owner_identity_unproven",
        "owner_record_ambiguous",
        "bridge_source_missing",
        "selection_source_missing",
    }
    status = (
        "passed"
        if not issues
        else "incomplete"
        if all(i.code in incomplete for i in issues)
        else "failed"
    )
    # The same source may prove several facets; repeated anchors add no evidence.
    resolved = list({canonical_json(ref.model_dump(mode="json")): ref for ref in resolved}.values())
    return ToolResult[BindingData](
        status="ok",
        data=BindingData(
            claim_ref=claim.claim_ref,
            validation_status=status,
            resolved_role_refs=resolved,
            source_unit=source_unit,
            issues=issues,
        ),
        evidence_refs=list(dict.fromkeys(ref.evidence_id for ref in resolved)),
        issues=[],
    )


def _propose_mentions(args: ProposeMentionsArgs, ctx: ToolContext) -> ToolResult[MentionData]:
    card = ctx.cards.get(args.schema_card_id)
    if (card is None or card.menu_id != ctx.menu.menu_id
            or card.subject_ref != VersionedRef(
                id=ctx.task.subject.entity_id, revision=ctx.task.subject.revision,
            )):
        raise _ToolFailure("reference_outside_scope", "/schema_card_id")
    inspected = _inspect_evidence(InspectEvidenceArgs(evidence_ids=args.evidence_ids), ctx)
    if ctx.ontology_snapshot is None or ctx.mention_extractor is None:
        raise _ToolFailure("model_unavailable")
    if card.ontology_snapshot_id != ctx.ontology_snapshot.snapshot_id:
        raise _ToolFailure("reference_version_mismatch")
    vocabulary = build_extraction_vocabulary(
        ctx.ontology_snapshot, card.class_iris, overlay=ctx.vocabulary_overlay,
    )
    predicates = {predicate.iri for predicate in card.predicates}
    vocabulary["entries"] = {
        label: entry for label, entry in vocabulary["entries"].items()
        if entry["role"] in ("entity", "record_anchor", "unit") or entry["iri"] in predicates
    }
    vocabulary["groups"] = {
        role: [label for label in labels if label in vocabulary["entries"]]
        for role, labels in vocabulary["groups"].items()
    }
    vocabulary["missing"] = [entry for entry in vocabulary["missing"]
                             if entry["role"] in ("entity", "record_anchor", "unit")
                             or entry["iri"] in predicates]
    sources, units = {}, {}
    for unit in inspected.data.units:
        key = stable_id("ner-source", [unit.evidence_id, unit.span_start, unit.span_end])
        sources[key] = {"text": unit.text}
        units[key] = unit
    raw = propose_vocabulary_mentions(
        sources, vocabulary=vocabulary, extractor=ctx.mention_extractor,
    )
    if raw["execution_status"] not in ("completed", "partial"):
        raise _ToolFailure("model_unavailable" if raw["execution_status"] == "unavailable"
                           else "mention_extraction_incomplete")
    limits = raw["limits"]
    actual_limits = [limits.get("max_len"), limits.get("encoder_input_limit"),
                     limits.get("shared_candidate_budget", {}).get("pool_size")]
    if any(type(value) is not int or value < 1 for value in actual_limits):
        raise _ToolFailure("model_limits_invalid")
    suggestions = []
    for span in raw["spans"]:
        unit = units[span["ref"]]
        start, end = unit.span_start + span["start"], unit.span_start + span["end"]
        anchor = ctx.index.ir.anchor(unit.evidence_id, start, end)
        if ctx.index.ir.resolve(anchor) != span["text"]:
            raise _ToolFailure("reference_version_mismatch")
        entry = vocabulary["entries"][span["label"]]
        role = "entity" if entry["role"] == "record_anchor" else entry["role"]
        key = ctx.index.physical_mention_key(unit.evidence_id, start, end)
        suggestions.append(MentionSuggestion(
            mention_ref=stable_id("source-mention", [ctx.index.ir.analysis_id, key, span["text"]]),
            evidence_id=unit.evidence_id, start=start, end=end, text=span["text"],
            class_iris=[entry["iri"]] if role == "entity" else [],
            predicate_iris=[entry["iri"]] if role in ("field_label", "field_value") else [],
            role=role, score=span["score"],
        ))
    # Missing vocabulary entries often share one reason; the tool exposes reasons,
    # so repeating the same issue per entry adds no information to model context.
    omissions = [_issue(reason) for reason in dict.fromkeys(
        item["reason"] for item in vocabulary["missing"]
    )]
    if len(suggestions) > ctx.limits.max_returned_mentions:
        omissions.append(_issue("mention_limit_exceeded"))
    covered = set(raw["coverage"]["covered_refs"])
    processed = [identity for identity in args.evidence_ids if all(
        key in covered for key, unit in units.items() if unit.evidence_id == identity
    )]
    unprocessed = [identity for identity in args.evidence_ids if identity not in processed]
    if unprocessed:
        omissions.append(_issue("mention_windows_incomplete", evidence_ids=unprocessed))
    data = MentionData(
        mentions=suggestions[:ctx.limits.max_returned_mentions],
        coverage=MentionCoverage(requested_units=args.evidence_ids, processed_units=processed,
                                 unprocessed_units=unprocessed),
        limits=MentionLimits(
            labels_per_batch=limits["labels_per_batch"], window_chars=limits["chunk_chars"],
            overlap_chars=limits["overlap_chars"], word_limit=actual_limits[0],
            encoder_token_limit=actual_limits[1], candidate_pool_limit=actual_limits[2],
            max_returned_mentions=ctx.limits.max_returned_mentions,
        ), omissions=omissions,
    )
    return ToolResult[MentionData](
        status="no_match" if not suggestions and not omissions and not unprocessed else "ok",
        data=data, evidence_refs=args.evidence_ids, issues=[],
    )


def _instance_query_from_entities(args, ctx, mention, names):
    """Use one exact local owner and one declared key for recall, never identity proof."""
    proposals = {}
    for reference, proposal in [
        *((view.entity_ref, view.proposal) for view in ctx.entity_dependencies.values()
          if view.proposal is not None),
        *((claim.claim_ref, claim.payload) for claim in ctx.frozen_claims.values()
          if claim.target_kind == "entity" and isinstance(claim.payload, EntityProposal)),
    ]:
        key = ref_key(reference)
        if key in proposals and proposals[key] != proposal:
            return None
        proposals[key] = proposal
    physical = {
        ctx.index.physical_mention_key(span.evidence_id, span.start, span.end)
        for span in mention.source_spans
    }
    owners, resolved = [], []
    for proposal in proposals.values():
        quotes = [*proposal.mentions, *(part.quote for part in proposal.record_components
                                       if part.role == "subject")]
        try:
            anchors = [_quote(quote, ctx) for quote in quotes]
        except _ToolFailure:
            continue
        resolved.append((proposal, anchors))
        if physical <= {
            ctx.index.physical_mention_key(anchor.evidence_id, anchor.span_start, anchor.span_end)
            for anchor in anchors
        }:
            owners.append(proposal)
    if len(owners) != 1 or owners[0].class_iri != args.class_iri:
        return None
    proposal = owners[0]
    declared = {canonical_json(key.model_dump(mode="json")): key
                for card in ctx.cards.values()
                if card.menu_id == ctx.menu.menu_id
                and card.ontology_snapshot_id == ctx.menu.ontology_snapshot_id
                and card.subject_ref == VersionedRef(
                    id=ctx.task.subject.entity_id, revision=ctx.task.subject.revision,
                )
                for key in card.identity_keys if key.class_iri == args.class_iri}
    prepared = []
    for identity_key in declared.values():
        identifiers = [claim for claim in proposal.identifier_claims
                       if claim.predicate_iri in identity_key.property_iris]
        try:
            anchors = [_quote(claim.value_quote, ctx) for claim in identifiers]
        except _ToolFailure:
            continue
        identities = {span.evidence_id for span in mention.source_spans}
        identities.update(anchor.evidence_id for anchor in anchors)
        record_sets = [
            {record.record_id for record in ctx.index.records_by_evidence.get(identity, [])}
            for identity in identities
        ]
        owners_in_source = set.intersection(*record_sets) if record_sets else set()
        if len(owners_in_source) != 1:
            continue
        owner = next(iter(owners_in_source))
        if any(other is not proposal and any(
            record.record_id == owner for anchor in other_anchors
            for record in ctx.index.records_by_evidence.get(anchor.evidence_id, [])
        ) for other, other_anchors in resolved):
            continue
        query = build_instance_query(
            source_ids=args.source_ids, class_iri=args.class_iri, name_quotes=names,
            identifier_quotes=identifiers, identity_key=identity_key,
            owner_scope_by_evidence={identity: owner for identity in identities},
            limit=ctx.limits.max_external_candidates,
        )
        if query.key_components:
            prepared.append(query)
    return prepared[0] if len(prepared) == 1 else None


def _referent_mention(mention_ref, ctx, path):
    """Validate a registered physical mention without granting source permissions."""
    mention = ctx.registered_mentions.get(mention_ref)
    if mention is None or mention.mention_id != mention_ref:
        raise _ToolFailure("reference_outside_scope", path)
    if mention.analysis_id != ctx.index.ir.analysis_id:
        raise _ToolFailure("reference_version_mismatch", path)
    anchors = []
    for span in mention.source_spans:
        try:
            anchor = ctx.index.ir.anchor(span.evidence_id, span.start, span.end)
            physical = ctx.index.physical_mention_key(span.evidence_id, span.start, span.end)
            expected = stable_id("source-mention", [ctx.index.ir.analysis_id, physical, span.text])
            if (expected != mention_ref or span.text != mention.text
                    or ctx.index.ir.resolve(anchor) != span.text):
                raise ValueError("mention identity changed")
        except (ValueError, KeyError) as exc:
            raise _ToolFailure("reference_version_mismatch", path) from exc
        if not any(contains(fragment.anchor, anchor) for fragment in ctx.context.fragments):
            raise _ToolFailure("reference_outside_scope", path)
        anchors.append(anchor)
    return mention, anchors


def _referent_name_key(text):
    # Retrieval only: spelling normalization must never create an entity identity.
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _anaphoric_referent(text):
    normalized = _referent_name_key(text)
    return normalized in {
        "其", "它", "它们", "其余", "本品", "本产品", "该产品", "it", "they", "them",
    } or normalized.startswith((
        "该", "上述", "前述", "此", "本批", "this", "that", "these", "those",
    ))


def _find_referent_candidates(
    args: FindReferentCandidatesArgs, ctx: ToolContext,
) -> ToolResult[ReferentCandidateData]:
    """Bounded recall from explicit dependencies, never a global graph search or merge."""
    if args.scope_id != ctx.scope.scope_id:
        raise _ToolFailure("reference_outside_scope", "/scope_id")
    for name, values in (("mention_refs", args.mention_refs), ("class_iris", args.class_iris)):
        if len(values) != len(set(values)) or any(not value for value in values):
            raise _ToolFailure("invalid_tool_arguments", "/" + name)
    if len(args.mention_refs) > ctx.limits.max_evidence_units_per_call:
        raise _ToolFailure("tool_budget_exhausted", "/mention_refs")
    if not set(args.class_iris) <= _instance_classes(ctx):
        raise _ToolFailure("reference_outside_scope", "/class_iris")
    requested = [
        _referent_mention(ref, ctx, f"/mention_refs/{position}")
        for position, ref in enumerate(args.mention_refs)
    ]
    requested_keys = {
        ctx.index.physical_mention_key(anchor.evidence_id, anchor.span_start, anchor.span_end)
        for _, anchors in requested for anchor in anchors
    }
    requested_names = {_referent_name_key(mention.text) for mention, _ in requested}
    anaphora = any(_anaphoric_referent(mention.text) for mention, _ in requested)
    positions = {
        unit.evidence_id: position for position, unit in enumerate(ctx.index.ir.evidence_units)
    }
    input_positions = [
        positions[anchor.evidence_id] for _, anchors in requested for anchor in anchors
    ]
    candidates, seen = [], {}
    for view in ctx.entity_dependencies.values():
        ctx.check_cancelled()
        try:
            EntityDependencyView.model_validate(view.model_dump(mode="json"), strict=True)
        except ValueError as exc:
            raise _ToolFailure("reference_version_mismatch") from exc
        key = ref_key(view.entity_ref)
        if view.entity_ref.id in seen:
            if seen[view.entity_ref.id] != (key, view.content_hash):
                raise _ToolFailure("reference_version_mismatch")
            continue
        seen[view.entity_ref.id] = (key, view.content_hash)
        if view.class_iri not in args.class_iris or view.proposal is None:
            continue
        for anchor in view.source_refs:
            try:
                ctx.index.ir.resolve(anchor)
            except (ValueError, KeyError) as exc:
                raise _ToolFailure("reference_version_mismatch") from exc
            if not any(contains(fragment.anchor, anchor) for fragment in ctx.context.fragments):
                raise _ToolFailure("reference_outside_scope")
        quotes = view.proposal.mentions or [
            part.quote for part in view.proposal.record_components if part.role == "subject"
        ]
        name_anchors = [_quote(quote, ctx) for quote in quotes]
        if any(not any(contains(source, anchor) for source in view.source_refs)
               for anchor in name_anchors):
            raise _ToolFailure("reference_version_mismatch")
        physical = {
            ctx.index.physical_mention_key(anchor.evidence_id, anchor.span_start, anchor.span_end)
            for anchor in name_anchors
        }
        if physical & requested_keys:
            reason, rank = "physical_mention", 0
        elif {_referent_name_key(quote.text) for quote in quotes} & requested_names:
            reason, rank = "normalized_name", 1
        elif anaphora:
            reason, rank = "anaphora_context", 2
        else:
            continue
        sources = list(view.source_refs)
        distinctions = []
        for quote in [*(part.quote for part in view.proposal.record_components
                        if part.role == "context"),
                      *(item.value_quote for item in view.proposal.identifier_claims)]:
            anchor = _quote(quote, ctx)
            if anchor not in sources:
                sources.append(anchor)
            if quote.text not in distinctions:
                distinctions.append(quote.text)
        mention_refs = [
            stable_id("source-mention", [
                ctx.index.ir.analysis_id,
                ctx.index.physical_mention_key(
                    anchor.evidence_id, anchor.span_start, anchor.span_end,
                ),
                ctx.index.ir.resolve(anchor),
            ]) for anchor in name_anchors
        ]
        candidate = ReferentCandidate(
            candidate_entity_ref=view.entity_ref, class_iri=view.class_iri,
            mention_refs=list(dict.fromkeys(mention_refs)), source_refs=sources,
            source_texts=[ctx.index.ir.resolve(anchor) for anchor in sources],
            reason=reason, role="binding", fact_eligible=False, distinctions=distinctions,
        )
        distance = min(abs(positions[source.evidence_id] - position)
                       for source in sources for position in input_positions)
        candidates.append(((rank, distance, key), candidate))
    candidates.sort(key=lambda value: value[0])
    selected = [candidate for _, candidate in candidates[:8]]
    excluded = len(candidates) - len(selected)
    return ToolResult[ReferentCandidateData](
        status="ok" if selected else "no_match",
        data=ReferentCandidateData(
            candidates=selected, truncated=bool(excluded), excluded_count=excluded,
            identity_status="not_checked",
        ),
        evidence_refs=list(dict.fromkeys(
            anchor.evidence_id for candidate in selected for anchor in candidate.source_refs
        )),
        issues=[],
    )


def _query_instances(args: QueryInstancesArgs, ctx: ToolContext) -> ToolResult[InstanceData]:
    mention = ctx.registered_mentions.get(args.mention_ref)
    if mention is None or mention.mention_id != args.mention_ref:
        raise _ToolFailure("reference_outside_scope", "/mention_ref")
    if not args.source_ids or not set(args.source_ids) <= set(ctx.external_source_ids):
        raise _ToolFailure("reference_outside_scope", "/source_ids")
    if args.class_iri not in _instance_classes(ctx):
        raise _ToolFailure("reference_outside_scope", "/class_iri")
    if ctx.instance_reader is None:
        raise _ToolFailure("external_source_unavailable")
    names = []
    if mention.analysis_id != ctx.index.ir.analysis_id:
        raise _ToolFailure("reference_version_mismatch")
    for span in mention.source_spans:
        anchor = ctx.index.ir.anchor(span.evidence_id, span.start, span.end)
        physical = ctx.index.physical_mention_key(span.evidence_id, span.start, span.end)
        expected_mention = stable_id(
            "source-mention", [ctx.index.ir.analysis_id, physical, span.text],
        )
        if expected_mention != mention.mention_id or span.text != mention.text:
            raise _ToolFailure("reference_version_mismatch", "/mention_ref")
        if (ctx.index.ir.resolve(anchor) != span.text or not any(
            contains(fragment.anchor, anchor) for fragment in ctx.context.fragments
        )):
            raise _ToolFailure("reference_outside_scope", "/mention_ref")
        names.append(Quote(evidence_id=span.evidence_id, text=span.text, context_text=None))
    prepared = ctx.instance_queries.get(args.mention_ref)
    if prepared is None:
        prepared = _instance_query_from_entities(args, ctx, mention, names)
    if prepared is not None:
        if (prepared.class_iri != args.class_iri
                or not set(args.source_ids) <= set(prepared.source_ids)):
            raise _ToolFailure("reference_outside_scope")
        if prepared.name_quotes != names:
            raise _ToolFailure("reference_version_mismatch")
        for component in prepared.key_components:
            _quote(component.quote, ctx)
        components = prepared.key_components
    else:
        components = []
    query = InstanceQuery(
        source_ids=args.source_ids, class_iri=args.class_iri, name_quotes=names,
        key_components=components, limit=ctx.limits.max_external_candidates,
    )
    result = ctx.instance_reader.search(query)
    reported = set(result.searched_sources) | set(result.incomplete_sources)
    if (reported != set(query.source_ids) or len(result.candidates) > query.limit or any(
        candidate.source_id not in result.searched_sources or candidate.class_iri != args.class_iri
        for candidate in result.candidates
    )):
        raise _ToolFailure("external_source_result_invalid")
    data = InstanceData(**result.model_dump(mode="python"), identity_status="not_checked")
    complete_empty = (
        not data.candidates and not data.incomplete_sources and data.excluded_count == 0
    )
    return ToolResult[InstanceData](
        status="no_match" if complete_empty else "ok", data=data,
        evidence_refs=list(dict.fromkeys(
            quote.evidence_id for quote in [*names, *(part.quote for part in components)]
        )),
        issues=[_issue("external_source_incomplete")] if data.incomplete_sources else [],
    )


def _retrieve_evidence(args: RetrieveEvidenceArgs, ctx: ToolContext) -> ToolResult[RetrievalData]:
    if (args.subject_id != ctx.task.subject.entity_id
            or args.predicate_iri != ctx.task.predicate_iri):
        raise _ToolFailure("reference_outside_scope")
    if not args.missing_facets or len(args.missing_facets) != len(set(args.missing_facets)):
        raise _ToolFailure("invalid_tool_arguments", "/missing_facets")
    if ctx.verified_claims and not set(args.missing_facets) <= {
        facet for verified in ctx.verified_claims.values() for facet in verified.missing_facets
    }:
        raise _ToolFailure("verification_gap_mismatch", "/missing_facets")
    if any(value is None for value in (
        ctx.metadata, ctx.base_context, ctx.target_seed, ctx.run_fingerprint, ctx.subject_node,
    )):
        raise _ToolFailure("retrieval_context_unavailable")
    if (ctx.metadata.document_hash != ctx.index.ir.document_hash
            or ctx.metadata.structure_hash != ctx.index.ir.structure_hash):
        raise _ToolFailure("reference_version_mismatch")
    slot = next((p for p in (*ctx.menu.properties, *ctx.menu.relationships)
                 if p.iri == args.predicate_iri), None)
    if slot is None:
        raise _ToolFailure("reference_outside_scope")
    plan = plan_slot(ctx.task.subject, slot, ctx.index, ctx.metadata,
                     ontology_hash=ctx.context.target.ontology_hash)
    prior_ids = ctx.authorization.record_ids if ctx.authorization else [ctx.task.record_id]
    seen = {fragment.anchor.evidence_id for fragment in ctx.context.fragments}
    candidates = [item.record_id for item in plan.records if item.record_id not in prior_ids
                  and any(unit.evidence_id not in seen
                          for unit in ctx.index.by_id[item.record_id].source_units)]
    selected = candidates[:ctx.limits.max_retrieval_records]
    card = compile_schema_card(
        ctx.menu, predicate_iri=slot.iri, profile=ctx.profile, scope=ctx.scope,
    )
    trusted = dict(
        index=ctx.index, target_seed=ctx.target_seed, card=card, profile=ctx.profile,
        entity_dependencies=list(ctx.entity_dependencies.values()), scope=ctx.scope,
        subject_node=ctx.subject_node,
    )
    authorization = build_retrieval_authorization(
        ctx.task, ctx.base_context, [*prior_ids, *selected], current=ctx.authorization, **trusted,
    )
    if selected:
        proposed, _ = build_authorized_context(
            ctx.task, ctx.base_context, authorization=authorization,
            evidence_revision=ctx.evidence_revision + 1, run_fingerprint=ctx.run_fingerprint,
            **trusted,
        )
        context_hash = proposed.context_hash
    else:
        context_hash = ctx.context.context_hash
    return ToolResult[RetrievalData](
        status="ok" if selected else "no_match",
        data=RetrievalData(
            record_ids=authorization.record_ids,
            evidence_ids=list(dict.fromkeys(f.evidence_id for f in authorization.fragments)),
            context_hash=context_hash, new_evidence=bool(selected),
            coverage=RetrievalCoverage(
                examined_records=selected, unattempted_records=candidates[len(selected):],
                stop_reason="record_budget_exhausted" if len(candidates) > len(selected) else None,
            ),
        ), evidence_refs=[], issues=[],
    )


def _propose_repair(args: ProposeRepairArgs, ctx: ToolContext) -> ToolResult[RepairData]:
    claim = ctx.frozen_claims.get(args.claim_id)
    if claim is None or claim.scope != ctx.scope:
        raise _ToolFailure("reference_outside_scope", "/claim_id")
    try:
        VerificationTargetSpec.model_validate(claim.model_dump(mode="json"), strict=True)
    except ValidationError as exc:
        raise _ToolFailure("reference_version_mismatch", "/claim_id") from exc
    if args.issue_code not in {
        "citation_quote_not_in_source", "citation_quote_ambiguous", "field_column_mismatch",
    }:
        raise _ToolFailure("repair_issue_unsupported", "/issue_code")
    payload = claim.payload
    proposed = []
    if isinstance(payload, PropertyProposal):
        _entity(payload.subject_id, claim, ctx)
        if args.issue_code != "field_column_mismatch":
            try:
                _quote(payload.value_quote, ctx)
            except _ToolFailure:
                pass
            else:
                return ToolResult[RepairData](
                    status="ok", data=RepairData(claim_ref=claim.claim_ref,
                                                 proposed_quotes=[], reason_code="quote_resolves"),
                    evidence_refs=[], issues=[],
                )
        labels = [_quote(quote, ctx) for quote in payload.field_support]
        for binding in field_bindings(ctx.index, ctx.task.record_id):
            if not labels or not all(any(contains(ref, label) for ref in binding.label_refs)
                                     for label in labels):
                continue
            if not any(contains(ref, owner) for ref in binding.owner_candidate_refs
                       for owner in ctx.context.subject_evidence_refs):
                continue
            for target in binding.target_value_refs:
                if not any(contains(fragment.anchor, target) for fragment in ctx.context.fragments):
                    continue
                text = ctx.index.ir.resolve(target)
                if text.count(payload.value_quote.text) == 1:
                    proposed.append(Quote(evidence_id=target.evidence_id,
                                          text=payload.value_quote.text, context_text=text))
    unique = {canonical_json(quote): quote for quote in proposed}
    return ToolResult[RepairData](
        status="ok", data=RepairData(
            claim_ref=claim.claim_ref,
            proposed_quotes=list(unique.values()) if len(unique) == 1 else [],
            reason_code="unique_quote_relocation" if len(unique) == 1 else "repair_not_unique",
        ), evidence_refs=[], issues=[],
    )


def _metric_inputs(claim_id, ctx):
    target = ctx.frozen_claims.get(claim_id)
    if (target is None or not isinstance(target.payload, PropertyProposal)
            or target.scope != ctx.scope):
        raise _ToolFailure("reference_outside_scope", "/claim_id")
    slot = next((p for p in ctx.menu.properties if p.iri == target.payload.predicate_iri), None)
    if slot is None:
        raise _ToolFailure("reference_outside_scope", "/claim_id")
    card = compile_schema_card(
        ctx.menu, predicate_iri=slot.iri, profile=ctx.profile, scope=ctx.scope,
    )
    policy = next((p for p in card.quantity_policies if p.predicate_iri == slot.iri), None)
    if policy is None:
        policy = QuantityPolicy(
            predicate_iri=slot.iri, allowed_forms=["scalar"], endpoint_role=None,
            allowed_target_units=[], unit_requirement="not_declared",
            declaration_ref=card.schema_card_id,
        )
    return target, slot, policy


def _validate_metric(args: ValidateMetricArgs, ctx: ToolContext) -> ToolResult[MetricData]:
    from app.services.extraction.tool_validation.metric import normalize_metric

    target, slot, policy = _metric_inputs(args.claim_id, ctx)
    verified = ctx.verified_claims.get(args.claim_id)
    if verified is None or ctx.binding_result is None:
        raise _ToolFailure("semantic_not_checked", "/claim_id")
    value = normalize_metric(
        target.payload.value_quote.text, slot, quantity_policy=policy,
        source_unit=ctx.binding_result.source_unit, target_unit=args.target_unit_id,
        binding=ctx.binding_result, verified=verified,
        target=target, candidate_ref=target.claim_ref,
    )
    return ToolResult[MetricData](status="ok", data=value, evidence_refs=[], issues=[])


def relation_check_matches(result, target, ctx):
    """Only a result for these exact frozen inputs satisfies the mandatory call."""
    return (
        result is not None and result.profile == RELATION_PROFILE
        and result.claim_ref == target.claim_ref and result.content_hash == target.content_hash
        and result.context_hash == ctx.context.context_hash
        and result.ontology_snapshot_id == ctx.menu.ontology_snapshot_id
        and result.menu_hash == evidence_hash(ctx.menu)
    )


def _validate_relation(args, ctx, target):
    from app.services.extraction.ontology_guided.claim_identity import duplicate_mentions

    if args.shape_profile_id != RELATION_PROFILE:
        raise _ToolFailure("relation_profile_mismatch", "/shape_profile_id")
    binding = _check_claim_binding(CheckClaimBindingArgs(claim_id=args.claim_id), ctx)
    duplicates = duplicate_mentions(
        [t.payload for t in ctx.frozen_claims.values() if t.target_kind == "entity"],
        ctx.entity_dependencies.values(), ctx.local_ref_map, lambda q: _quote(q, ctx),
    )
    # Only the submitted relation's endpoints participate in this result.
    duplicates = {key: ref for key, ref in duplicates.items()
                  if key in [target.payload.subject_id, *target.payload.object_ids]}
    issues = [*binding.issues, *(binding.data.issues if binding.data else [])]
    duplicate_failure = bool(duplicates) and not ctx.reference_resolution
    identity_status = "failed" if duplicate_failure else "passed"
    if duplicate_failure:
        issues.extend(_issue("entity_referent_already_registered", f"/entities/{key}")
                      for key in duplicates)
    ontology_status = binding.data.validation_status if binding.data else "incomplete"
    status = ("failed" if identity_status == "failed" or ontology_status == "failed"
              else ontology_status)
    return ToolResult[ShaclData | RelationValidationData](
        status="ok", data=RelationValidationData(
            profile=RELATION_PROFILE, claim_ref=target.claim_ref,
            content_hash=target.content_hash, context_hash=ctx.context.context_hash,
            ontology_snapshot_id=ctx.menu.ontology_snapshot_id, menu_hash=evidence_hash(ctx.menu),
            ontology_status=ontology_status, identity_status=identity_status,
            validation_status=status, duplicate_entities=duplicates, issues=issues,
            semantic_status="not_checked", checked_constraints=[
                "task_subject", "predicate_menu", "range", "source_binding",
                "exact_mention_identity",
            ],
        ), evidence_refs=binding.evidence_refs, issues=[],
    )


def _validate_graph(args: ValidateGraphArgs, ctx: ToolContext):
    from app.services.extraction.tool_validation.shacl import validate_metric_result

    target = ctx.frozen_claims.get(args.claim_id)
    if target is not None and target.target_kind == "relation":
        return _validate_relation(args, ctx, target)
    if ctx.stage != "finalize":
        raise _ToolFailure("tool_not_allowed", "/claim_id")
    target, slot, policy = _metric_inputs(args.claim_id, ctx)
    if ctx.metric_result is None:
        raise _ToolFailure("metric_not_passed", "/claim_id")
    value = validate_metric_result(
        ctx.metric_result, slot=slot, quantity_policy=policy, candidate_ref=target.claim_ref,
        shape_profile_id=args.shape_profile_id, raw=target.payload.value_quote.text,
    )
    issues = []
    if not value.evaluated:
        issues.append(_issue("shacl_not_evaluated"))
    elif not value.coverage.complete:
        issues.append(_issue("shacl_coverage_incomplete"))
    elif value.conforms is False:
        issues.append(_issue("shacl_nonconformant"))
    return ToolResult[ShaclData | RelationValidationData](
        status="ok", data=value, evidence_refs=[], issues=issues,
    )


_HANDLERS = MappingProxyType(
    {
        "get_schema_card": _get_schema_card,
        "inspect_evidence": _inspect_evidence,
        "resolve_source_anchor": _resolve_source_anchor,
        "check_claim_binding": _check_claim_binding,
        "propose_mentions": _propose_mentions,
        "query_instances": _query_instances,
        "find_referent_candidates": _find_referent_candidates,
        "retrieve_evidence": _retrieve_evidence,
        "propose_repair": _propose_repair,
        "validate_metric": _validate_metric,
        "validate_graph": _validate_graph,
    }
)
