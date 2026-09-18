"""Recall tools preserve frozen source ownership and expose incomplete execution."""

from dataclasses import replace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.external_records import FrozenInstanceReader, ResolvedRecord
from app.services.extraction.ontology_guided import tool_runtime as runtime
from app.services.extraction.ontology_guided.claim_protocol import (
    EntityDependencyView,
    ExtractionProfile,
    FrozenClaimSet,
    IdentifierProposal,
    IdentityKeySpec,
    Quote,
    VerificationTargetSpec,
    claim_content_hash,
    compile_schema_card,
)
from app.services.extraction.ontology_guided.context import (
    build_authorized_context,
    build_retrieval_authorization,
)
from app.services.extraction.ontology_guided.contracts import (
    LocalMenu,
    MetadataNode,
    MetadataSnapshot,
    OntologyClassDefinition,
    OntologySnapshot,
    VersionedRef,
)
from app.services.extraction.ontology_guided.evidence_work import plan_evidence_recovery
from app.services.extraction.ontology_guided.mentions import MentionRegistry

from .test_tool_engine_context import authorized_context as _authorized_context_fixture
from .test_tool_runtime import add_property, call
from .test_tool_runtime import tool_context as _tool_context_fixture

authorized_context = _authorized_context_fixture
tool_context = _tool_context_fixture


class FakeGliner25:
    def __init__(self, *, hits=True):
        self.hits = hits

    def prepare_strict(self):
        return {
            "backend": "gliner2.5",
            "max_len": 160,
            "encoder_input_limit": 512,
            "shared_candidate_budget": {"pool_size": 64},
            "max_width": None,
        }

    def extract_batch_with_spans_strict(self, texts, labels, threshold):
        return [
            [{"start": 0, "end": len(text), "text": text, "label": labels[0], "score": 0.8}]
            if self.hits
            else []
            for text in texts
        ]


def ner_context(ctx, *, extractor=None):
    slot = ctx.menu.properties[0].model_copy(update={"description": "Mass of the entity"})
    ontology = OntologySnapshot(
        snapshot_id=ctx.menu.ontology_snapshot_id,
        ontology_hash="frozen",
        classes={
            "urn:Entity": OntologyClassDefinition(
                iri="urn:Entity",
                label="Entity",
                description="Named entity",
                source_hash="source",
                declared_properties=[slot],
            )
        },
    )
    card = compile_schema_card(
        ctx.menu, predicate_iri="urn:mass", profile=ctx.profile, scope=ctx.scope
    )
    return replace(
        ctx,
        stage="discovery",
        cards={card.schema_card_id: card},
        ontology_snapshot=ontology,
        mention_extractor=extractor,
    ), card


def test_ner_uses_physical_identity_and_keeps_label_value_scores_separate(tool_context):
    ctx, card = ner_context(tool_context, extractor=FakeGliner25())
    owner = next(unit for unit in ctx.index.ir.evidence_units if unit.text == "A")
    result = runtime.dispatch_tool(
        call(
            "propose_mentions",
            evidence_ids=[owner.evidence_id],
            schema_card_id=card.schema_card_id,
        ),
        ctx,
    )
    assert result.status == "ok", result
    assert {mention.role for mention in result.data.mentions} == {
        "entity",
        "field_label",
        "field_value",
    }
    anchored = runtime.dispatch_tool(
        call(
            "resolve_source_anchor",
            evidence_id=owner.evidence_id,
            quote="A",
            context_text=None,
        ),
        ctx,
    )
    assert {mention.mention_ref for mention in result.data.mentions} == {anchored.data.mention_ref}
    assert all(mention.score == 0.8 for mention in result.data.mentions)
    assert result.data.coverage.unprocessed_units == []
    assert result.data.limits.candidate_pool_limit == 64


def test_ner_no_model_and_unauthorized_sources_never_become_no_match(tool_context):
    ctx, card = ner_context(tool_context)
    identity = ctx.context.fragments[0].anchor.evidence_id
    result = runtime.dispatch_tool(
        call(
            "propose_mentions",
            evidence_ids=[identity],
            schema_card_id=card.schema_card_id,
        ),
        ctx,
    )
    assert result.status == "blocked" and result.issues[0].code == "model_unavailable"
    denied = runtime.dispatch_tool(
        call(
            "propose_mentions",
            evidence_ids=["foreign"],
            schema_card_id=card.schema_card_id,
        ),
        replace(ctx, mention_extractor=FakeGliner25()),
    )
    assert denied.status == "blocked" and denied.issues[0].code == "reference_outside_scope"
    empty = runtime.dispatch_tool(
        call(
            "propose_mentions",
            evidence_ids=[identity],
            schema_card_id=card.schema_card_id,
        ),
        replace(ctx, mention_extractor=FakeGliner25(hits=False)),
    )
    assert empty.status == "no_match" and empty.data.mentions == []


def test_ner_omissions_are_visible_and_do_not_change_source_permissions(tool_context):
    ctx, card = ner_context(tool_context, extractor=FakeGliner25())
    ctx = replace(ctx, limits=replace(ctx.limits, max_returned_mentions=1))
    identity = ctx.context.fragments[0].anchor.evidence_id
    original = ctx.context.model_dump(mode="json")
    result = runtime.dispatch_tool(
        call(
            "propose_mentions",
            evidence_ids=[identity],
            schema_card_id=card.schema_card_id,
        ),
        ctx,
    )
    assert len(result.data.mentions) == 1
    assert result.data.omissions[0].code == "mention_limit_exceeded"
    assert ctx.context.model_dump(mode="json") == original


def test_repeated_missing_definitions_do_not_consume_model_result_budget(tool_context, monkeypatch):
    ctx, card = ner_context(tool_context, extractor=FakeGliner25())
    build = runtime.build_extraction_vocabulary

    def missing_definitions(*args, **kwargs):
        vocabulary = build(*args, **kwargs)
        vocabulary["missing"] = [
            {"role": "entity", "iri": f"urn:Missing{i}", "reason": "definition_missing"}
            for i in range(25)
        ]
        return vocabulary

    monkeypatch.setattr(runtime, "build_extraction_vocabulary", missing_definitions)
    result = runtime.dispatch_tool(call(
        "propose_mentions", evidence_ids=[ctx.context.fragments[0].anchor.evidence_id],
        schema_card_id=card.schema_card_id,
    ), ctx)
    assert result.status == "ok"
    assert [item.code for item in result.data.omissions] == ["definition_missing"]
    assert not result.data.coverage.unprocessed_units


def external_context(ctx, *, incomplete=False):
    owner = next(unit for unit in ctx.index.ir.evidence_units if unit.text == "A")
    registry = MentionRegistry(ctx.index.ir, ctx.index)
    mention = registry.register(
        evidence_id=owner.evidence_id, start=0, end=1, text="A",
        record_view_ref=ctx.index.record_views_by_id[ctx.task.record_id].record_view_id,
    )
    reader = FrozenInstanceReader(
        {
            "archive": [
                ResolvedRecord(
                    system="mock",
                    dataset="records",
                    key="1",
                    version="v1",
                    class_iri="urn:Entity",
                    label_field="name",
                    fields={"name": "A", "note": "metadata only"},
                    field_predicates={},
                )
            ]
        },
        name_fields={"archive": ["name"]},
        incomplete_sources=["archive"] if incomplete else [],
    )
    return replace(
        ctx,
        stage="discovery",
        registered_mentions={mention.mention_id: mention},
        instance_reader=reader,
        external_source_ids=("archive",),
    ), mention.mention_id


def test_query_instances_returns_candidates_and_keeps_identity_unchecked(tool_context):
    ctx, mention = external_context(tool_context, incomplete=True)
    offered = next(tool for tool in runtime.build_tool_definitions(ctx, ctx.stage)
                   if tool["name"] == "query_instances")["parameters"]["properties"]
    assert offered["source_ids"]["items"]["enum"] == ["archive"]
    assert offered["class_iri"]["enum"] == ["urn:Entity"]
    result = runtime.dispatch_tool(
        call(
            "query_instances",
            mention_ref=mention,
            class_iri=offered["class_iri"]["enum"][0],
            source_ids=offered["source_ids"]["items"]["enum"],
        ),
        ctx,
    )
    assert result.status == "ok", result
    assert result.data.identity_status == "not_checked"
    assert result.data.excluded_count is None
    assert result.data.incomplete_sources == ["archive"]
    assert result.data.candidates[0].mapped_fields == []
    assert result.data.candidates[0].metadata
    other = replace(ctx, external_source_ids=("other",))
    next_tool = next(tool for tool in runtime.build_tool_definitions(other, other.stage)
                     if tool["name"] == "query_instances")
    assert next_tool["parameters"]["properties"]["source_ids"]["items"]["enum"] == ["other"]
    assert offered["source_ids"]["items"]["enum"] == ["archive"]
    denied = runtime.dispatch_tool(
        call(
            "query_instances",
            mention_ref=mention,
            class_iri="urn:Entity",
            source_ids=["hidden"],
        ),
        ctx,
    )
    assert denied.status == "blocked" and denied.issues[0].field_path == "/source_ids"


def test_query_requires_registered_mention_and_checked_frozen_source_span(tool_context):
    ctx, mention = external_context(tool_context)
    result = runtime.dispatch_tool(
        call(
            "query_instances",
            mention_ref="unregistered",
            class_iri="urn:Entity",
            source_ids=["archive"],
        ),
        ctx,
    )
    assert result.issues[0].field_path == "/mention_ref"
    forged = ctx.registered_mentions[mention].model_copy(deep=True)
    forged.source_spans[0].text = "B"
    result = runtime.dispatch_tool(
        call(
            "query_instances",
            mention_ref=mention,
            class_iri="urn:Entity",
            source_ids=["archive"],
        ),
        replace(ctx, registered_mentions={mention: forged}),
    )
    assert result.status == "blocked"


def keyed_external_context(tool_context, *, frozen=False):
    ctx, mention = external_context(tool_context)
    units = {unit.text: unit for unit in ctx.index.ir.evidence_units}
    original = ctx.entity_dependencies["s"]
    proposal = original.proposal.model_copy(update={"identifier_claims": [IdentifierProposal(
        predicate_iri="urn:code", value_quote=Quote(
            evidence_id=units["3.8–6.6"].evidence_id, text="3.8–6.6", context_text=None,
        ),
    )]})
    payload = original.model_dump(mode="python", exclude={"content_hash"})
    payload["proposal"] = proposal
    entity = EntityDependencyView(**payload, content_hash=evidence_hash(payload))
    key = IdentityKeySpec(class_iri="urn:Entity", property_iris=["urn:code"],
                          namespace="records", scope="dataset", declaration_ref="profile-1")
    profile = ExtractionProfile(identity_keys=[key])
    card = compile_schema_card(ctx.menu, predicate_iri="urn:mass", profile=profile, scope=ctx.scope)
    claims = {}
    if frozen:
        claim = VerificationTargetSpec(
            target_id="entity-target", target_kind="entity", claim_ref=entity.entity_ref,
            payload=proposal, scope=ctx.scope, dependency_refs=[], required_facets=["type"],
            content_hash=claim_content_hash("entity", proposal, ctx.scope, []),
        )
        claims[claim.claim_ref.id] = claim
    reader = FrozenInstanceReader({"archive": [ResolvedRecord(
        system="mock", dataset="records", key="1", version="v1", class_iri="urn:Entity",
        label_field="name", fields={"name": "Different archive name", "code": "3.8–6.6"},
        field_predicates={"code": "urn:code"},
    )]}, name_fields={"archive": ["name"]})
    return replace(
        ctx, stage="discovery", recovery_kind="reproposal" if frozen else "none", profile=profile,
        cards={card.schema_card_id: card}, frozen_claims=claims,
        entity_dependencies={} if frozen else {"s": entity}, instance_reader=reader,
    ), mention


@pytest.mark.parametrize("frozen", [False, True])
def test_query_derives_declared_key_from_exact_current_entity_owner(tool_context, frozen):
    ctx, mention = keyed_external_context(tool_context, frozen=frozen)
    before = {key: value.model_dump(mode="json") for key, value in ctx.frozen_claims.items()}
    result = runtime.dispatch_tool(call(
        "query_instances", mention_ref=mention, class_iri="urn:Entity", source_ids=["archive"],
    ), ctx)
    assert result.status == "ok", result
    assert result.data.identity_status == "not_checked"
    assert len(result.data.candidates) == 1
    assert {match.match_kind for match in result.data.candidates[0].matches} == {"exact_key"}
    assert ctx.instance_queries == {}
    assert before == {
        key: value.model_dump(mode="json") for key, value in ctx.frozen_claims.items()
    }


@pytest.mark.parametrize("reason", [
    "duplicate_identifier", "ambiguous_owner", "wrong_class", "other_row", "undeclared_key",
    "conflicting_keys", "wrong_mention", "competing_row_owner",
])
def test_ambiguous_or_unowned_key_falls_back_to_name_recall(tool_context, reason):
    ctx, mention = keyed_external_context(tool_context)
    entity = ctx.entity_dependencies["s"]
    proposal = entity.proposal.model_copy(deep=True)
    if reason == "duplicate_identifier":
        proposal.identifier_claims *= 2
    elif reason == "other_row":
        unit = next(unit for unit in ctx.index.ir.evidence_units if unit.text == "7.5")
        proposal.identifier_claims[0].value_quote = Quote(
            evidence_id=unit.evidence_id, text=unit.text, context_text=None,
        )
    elif reason == "wrong_mention":
        unit = next(unit for unit in ctx.index.ir.evidence_units if unit.text == "B")
        proposal.mentions = [Quote(evidence_id=unit.evidence_id, text="B", context_text=None)]
    elif reason == "wrong_class":
        proposal.class_iri = "urn:Other"
    payload = entity.model_dump(mode="python", exclude={"content_hash"})
    payload.update(proposal=proposal, class_iri=proposal.class_iri)
    entity = EntityDependencyView(**payload, content_hash=evidence_hash(payload))
    dependencies = {"s": entity}
    if reason == "ambiguous_owner":
        payload["entity_ref"] = VersionedRef(id="another-entity", revision=1)
        dependencies["other"] = EntityDependencyView(**payload, content_hash=evidence_hash(payload))
    elif reason == "competing_row_owner":
        other = proposal.model_copy(deep=True)
        other.local_id = "other"
        other.mentions = [other.identifier_claims[0].value_quote]
        other.identifier_claims = []
        payload.update(entity_ref=VersionedRef(id="another-entity", revision=1), proposal=other)
        dependencies["other"] = EntityDependencyView(**payload, content_hash=evidence_hash(payload))
    cards = dict(ctx.cards)
    if reason == "undeclared_key":
        cards = {}
    elif reason == "conflicting_keys":
        card = next(iter(cards.values())).model_copy(deep=True)
        card.identity_keys.append(card.identity_keys[0].model_copy(update={"namespace": "other"}))
        cards = {card.schema_card_id: card}
    ctx = replace(ctx, entity_dependencies=dependencies, cards=cards)
    result = runtime.dispatch_tool(call(
        "query_instances", mention_ref=mention, class_iri="urn:Entity", source_ids=["archive"],
    ), ctx)
    assert result.status == "no_match", result
    assert result.data.identity_status == "not_checked"
    assert result.data.candidates == []


def test_frozen_identifier_does_not_expand_verification_tool_permissions(tool_context):
    ctx, mention = keyed_external_context(tool_context, frozen=True)
    ctx = replace(ctx, stage="verification")
    result = runtime.dispatch_tool(call(
        "query_instances", mention_ref=mention, class_iri="urn:Entity", source_ids=["archive"],
    ), ctx)
    assert result.status == "blocked"
    assert result.issues[0].code == "tool_not_allowed"


def retrieval_context(fixture):
    trusted = fixture["trusted"]
    task, base, index = fixture["task"], fixture["base"], fixture["index"]
    menu = LocalMenu(
        menu_id="menu-1",
        ontology_snapshot_id="ontology-1",
        subject=task.subject,
        properties=trusted["card"].predicates,
    )
    metadata = MetadataSnapshot(
        snapshot_id="metadata",
        analysis_id=index.ir.analysis_id,
        document_hash=index.ir.document_hash,
        structure_hash=index.ir.structure_hash,
        summary_version="v1",
        generation_source="structure_only",
        dependency_hash="a" * 64,
    )
    return runtime.ToolContext(
        task=task,
        context=base,
        base_context=base,
        index=index,
        menu=menu,
        profile=trusted["profile"],
        scope=trusted["scope"],
        stage="verification",
        recovery_kind="evidence",
        frozen_claims={},
        local_ref_map={},
        entity_dependencies={"root": trusted["entity_dependencies"][0]},
        limits=runtime.ToolLimits(max_result_tokens=10000),
        measure_result_tokens=len,
        metadata=metadata,
        target_seed=trusted["target_seed"],
        run_fingerprint=trusted["run_fingerprint"],
        subject_node=trusted["subject_node"],
    )


def test_retrieval_returns_full_records_without_granting_worker_permissions(authorized_context):
    ctx = retrieval_context(authorized_context)
    arguments = call(
        "retrieve_evidence",
        subject_id=ctx.task.subject.entity_id,
        predicate_iri=ctx.task.predicate_iri,
        missing_facets=["counterevidence"],
    )
    original = ctx.context.model_dump(mode="json")
    result = runtime.dispatch_tool(arguments, ctx)
    assert result.status == "ok", result
    assert result.data.new_evidence
    assert ctx.context.model_dump(mode="json") == original
    counter = authorized_context["counter_ref"]
    assert counter.evidence_id in result.data.evidence_ids
    denied = runtime.dispatch_tool(
        call("inspect_evidence", evidence_ids=[counter.evidence_id]), ctx
    )
    assert denied.status == "blocked"
    card = compile_schema_card(
        ctx.menu, predicate_iri=ctx.task.predicate_iri, profile=ctx.profile, scope=ctx.scope
    )
    inputs = dict(
        index=ctx.index,
        target_seed=ctx.target_seed,
        card=card,
        profile=ctx.profile,
        entity_dependencies=list(ctx.entity_dependencies.values()),
        scope=ctx.scope,
        subject_node=ctx.subject_node,
    )
    auth = build_retrieval_authorization(
        ctx.task, ctx.base_context, result.data.record_ids, **inputs
    )
    rebuilt, _ = build_authorized_context(
        ctx.task,
        ctx.base_context,
        authorization=auth,
        evidence_revision=2,
        run_fingerprint=ctx.run_fingerprint,
        **inputs,
    )
    assert rebuilt.context_hash == result.data.context_hash
    assert all(
        not f.fact_eligible
        for f in rebuilt.fragments
        if f.anchor.evidence_id == counter.evidence_id
    )
    committed = replace(ctx, context=rebuilt, authorization=auth, evidence_revision=2)
    inspected = runtime.dispatch_tool(
        call("inspect_evidence", evidence_ids=[counter.evidence_id]), committed
    )
    assert inspected.status == "ok"
    again = runtime.dispatch_tool(arguments, committed)
    assert again.status == "no_match" and not again.data.new_evidence
    assert again.data.context_hash == rebuilt.context_hash


def test_retrieval_reconstruction_preserves_known_counterevidence_roles(authorized_context):
    fixture = authorized_context
    current = fixture["authorization"]
    trusted = {key: value for key, value in fixture["trusted"].items() if key != "run_fingerprint"}
    rebuilt = build_retrieval_authorization(
        fixture["task"],
        fixture["base"],
        current.record_ids,
        index=fixture["index"],
        current=current,
        **trusted,
    )
    assert rebuilt.bindings.counterevidence_refs == current.bindings.counterevidence_refs
    assert all(fragment in rebuilt.fragments for fragment in current.fragments)
    with pytest.raises(ValueError, match="retrieval base policy"):
        build_retrieval_authorization(
            fixture["task"],
            fixture["base"],
            current.record_ids,
            index=fixture["index"],
            current=current.model_copy(update={"task_id": "foreign"}),
            **trusted,
        )


def test_repair_only_relocates_one_quoted_value_with_owner_and_field_support(tool_context):
    ctx = replace(add_property(tool_context), recovery_kind="reproposal")
    original = ctx.frozen_claims["claim-p"]
    proposal = original.payload.model_copy(deep=True)
    proposal.value_quote.evidence_id = "misaddressed"
    target = original.model_copy(update={
        "payload": proposal,
        "content_hash": claim_content_hash(
            "property", proposal, original.scope, original.dependency_refs,
        ),
    })
    ctx = replace(ctx, frozen_claims={"claim-p": target})
    result = runtime.dispatch_tool(
        call(
            "propose_repair",
            claim_id="claim-p",
            issue_code="citation_quote_not_in_source",
        ),
        ctx,
    )
    assert result.status == "ok", result
    assert len(result.data.proposed_quotes) == 1
    assert result.data.proposed_quotes[0].text == "3.8–6.6"
    denied = runtime.dispatch_tool(
        call(
            "propose_repair",
            claim_id="claim-p",
            issue_code="citation_quote_not_in_source",
        ),
        replace(ctx, recovery_kind="none"),
    )
    assert denied.status == "blocked" and denied.issues[0].code == "tool_not_allowed"
    unsupported = runtime.dispatch_tool(
        call(
            "propose_repair",
            claim_id="claim-p",
            issue_code="invent_semantic_value",
        ),
        ctx,
    )
    assert unsupported.issues[0].code == "repair_issue_unsupported"


def test_repair_cannot_rewrite_a_resolving_quote_or_act_without_owner_evidence(tool_context):
    ctx = replace(add_property(tool_context), recovery_kind="reproposal")
    args = call("propose_repair", claim_id="claim-p", issue_code="citation_quote_not_in_source")
    result = runtime.dispatch_tool(args, ctx)
    assert result.data.proposed_quotes == [] and result.data.reason_code == "quote_resolves"
    no_owner = ctx.context.model_copy(update={"subject_evidence_refs": []})
    result = runtime.dispatch_tool(call(
        "propose_repair", claim_id="claim-p", issue_code="field_column_mismatch",
    ), replace(ctx, context=no_owner))
    assert result.data.proposed_quotes == []


def test_recovery_is_pure_bounded_and_uses_only_frozen_source_clues(tool_context):
    ctx = add_property(tool_context)
    values = dict(
        entities=[], properties=[ctx.frozen_claims["claim-p"].payload], relations=[],
        external_links=[], observations=[], assertion_generation=1, evidence_revision=1,
        local_ref_map=dict(ctx.local_ref_map), claim_issues={},
    )
    frozen = FrozenClaimSet(content_hash=evidence_hash(values), **values)
    original = frozen.model_dump(mode="json")
    args = dict(task=ctx.task, frozen=frozen, missing_facets=["value"], index=ctx.index,
                already_seen=set(), remaining_calls=2, recovery_used=False)
    recovery = plan_evidence_recovery(**args)
    assert recovery.action == "reproposal" and recovery.required_model_calls == 2
    assert plan_evidence_recovery(**(args | {"remaining_calls": 1})).action == "none"
    assert plan_evidence_recovery(**(args | {"recovery_used": True})).action == "none"
    assert plan_evidence_recovery(**(args | {"remaining_calls": 0})).action == "none"
    assert plan_evidence_recovery(**(args | {"missing_facets": []})).action == "none"
    assert frozen.model_dump(mode="json") == original


def test_existing_summary_ranking_only_returns_original_record_sources(authorized_context):
    ctx = retrieval_context(authorized_context)
    counter = authorized_context["counter_ref"]
    original = ctx.index.ir.unit(counter.evidence_id)
    node = MetadataNode(
        node_id=original.section_node_id, heading="条件", path=["条件"],
        summary="数量字段：这是摘要中的虚构数值99999，不是原文。",
        summary_status="completed", summary_source="llm",
    )
    metadata = ctx.metadata.model_copy(update={"node_summaries": [node],
                                              "generation_source": "model_summary"})
    ctx = replace(ctx, metadata=metadata)
    result = runtime.dispatch_tool(call(
        "retrieve_evidence", subject_id=ctx.task.subject.entity_id,
        predicate_iri=ctx.task.predicate_iri, missing_facets=["counterevidence"],
    ), ctx)
    assert result.status == "ok"
    assert counter.evidence_id in result.data.evidence_ids
    assert "99999" not in result.model_dump_json()
    assert all(identity in {unit.evidence_id for unit in ctx.index.ir.evidence_units}
               for identity in result.data.evidence_ids)


def test_retrieval_rejects_stale_summary_and_foreign_subject(authorized_context):
    ctx = retrieval_context(authorized_context)
    args = call("retrieve_evidence", subject_id=ctx.task.subject.entity_id,
                predicate_iri=ctx.task.predicate_iri, missing_facets=["counterevidence"])
    result = runtime.dispatch_tool(args, replace(
        ctx, metadata=ctx.metadata.model_copy(update={"document_hash": "0" * 64}),
    ))
    assert result.status == "blocked" and result.issues[0].code == "reference_version_mismatch"
    result = runtime.dispatch_tool(call(
        "retrieve_evidence", subject_id="foreign", predicate_iri=ctx.task.predicate_iri,
        missing_facets=["counterevidence"],
    ), ctx)
    assert result.status == "blocked"
