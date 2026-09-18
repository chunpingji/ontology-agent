"""Role-preserving, bounded hierarchical views with independent self recall."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from pydantic import Field

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.adaptive_retrieval import CONTEXT_VIEW_VERSION
from app.services.extraction.ontology_guided.retrieval_views import RetrievalView


class ContextualView(RetrievalView):
    variants: dict[str, dict] = Field(default_factory=dict)
    source_roles: list[dict] = Field(default_factory=list)
    group_refs: list[dict] = Field(default_factory=list)
    selected_variant: str = "context"
    context_complete: bool = True


def view_configuration(policy):
    return {
        key: getattr(policy, key)
        for key in (
            "view_version",
            "sibling_limit",
            "ancestor_limit",
            "group_member_limit",
        )
    }


def structural_groups(index, policy):
    sections = defaultdict(list)
    for record in index.records:
        sections[record.section_node_id].append(record.record_id)
    groups = {}
    for label, members in [
        *(("field_group", group.record_ids) for group in index.field_groups),
        *(("section", members) for members in sections.values()),
    ]:
        ids = sorted(set(members), key=index.record_positions.__getitem__)
        identity = stable_id(
            "retrieval-group",
            [
                index.ir.document_hash,
                index.ir.structure_hash,
                view_configuration(policy),
                label,
                ids,
            ],
        )
        # Source order gives a deterministic bounded expansion. Omitted members
        # remain in U, with their independent self/context recall pathways.
        groups[identity] = {
            "group_view_id": identity,
            "kind": label,
            "selected_record_ids": ids[: policy.group_member_limit],
            "omitted_record_ids": ids[policy.group_member_limit :],
        }
    return groups


def build_group_views(index, metadata, policy, *, count_tokens_batch, max_record_tokens):
    groups = structural_groups(index, policy)
    summaries = {node.node_id: node for node in metadata.node_summaries}
    for group in groups.values():
        roles, members = [], []
        for rid in group["selected_record_ids"]:
            source = index.record_views_by_id[rid]
            refs = [*source.source_refs, *source.header_refs, *source.note_refs]
            node = summaries.get(index.by_id[rid].section_node_id)
            members.append(
                {
                    "record_id": rid,
                    "heading_path": node.path if node else [],
                    "source": [index.ir.resolve(anchor) for anchor in refs],
                }
            )
            roles.extend(
                {"role": "group_member", "record_id": rid, "anchor": anchor.model_dump(mode="json")}
                for anchor in refs
            )
        group["model_text"] = json.dumps(members, ensure_ascii=False, sort_keys=True)
        group["source_roles"] = roles
        group["input_hash"] = evidence_hash(group["model_text"])
    counts = count_tokens_batch([group["model_text"] for group in groups.values()])
    if len(counts) != len(groups):
        raise ValueError("ranking_model_returned_incomplete_batch")
    for group, count in zip(groups.values(), counts, strict=True):
        group["token_count"] = count
        group["status"] = "complete" if count <= max_record_tokens else "not_rerankable"
    return groups


def build_contextual_views(index, metadata, policy, *, count_tokens_batch, max_record_tokens):
    summaries = {node.node_id: node for node in metadata.node_summaries}
    groups = structural_groups(index, policy)
    memberships = defaultdict(list)
    for identity, group in groups.items():
        for rid in [*group["selected_record_ids"], *group["omitted_record_ids"]]:
            memberships[rid].append(identity)
    sibling_nodes = defaultdict(list)
    for node in metadata.node_summaries:
        sibling_nodes[index.nodes_by_id.get(node.node_id, {}).get("parent_id")].append(node)

    prepared = {}
    texts = {}
    for record in index.records:
        rid = record.record_id
        source = index.record_views_by_id[rid]
        roles = []

        def include(role, refs):
            values = []
            for anchor in refs:
                text = index.ir.resolve(anchor)
                roles.append({"role": role, "anchor": anchor.model_dump(mode="json")})
                values.append(text)
            return values

        own = {
            "record": include("self", source.source_refs),
            "headers": include("header", source.header_refs),
        }
        own_roles = list(roles)
        local = {
            **own,
            "notes": include("condition_context", source.note_refs),
            "parent_table": include("parent_table", source.parent_context_refs),
        }
        node = summaries.get(record.section_node_id)
        local["heading_path"] = node.path if node else []
        ancestors = []
        parent = index.nodes_by_id.get(record.section_node_id, {}).get("parent_id")
        seen = set()
        while parent and parent not in seen and len(ancestors) < policy.ancestor_limit:
            seen.add(parent)
            ancestor = summaries.get(parent)
            if ancestor:
                ancestors.append(
                    {
                        "node_id": parent,
                        "heading": ancestor.heading,
                        "summary": ancestor.summary,
                        "authority": "retrieval_only",
                    }
                )
            parent = index.nodes_by_id.get(parent, {}).get("parent_id")
        local["ancestors"] = ancestors
        sibling_ids = []
        refs = []
        for gid in memberships[rid]:
            group = groups[gid]
            chosen = [
                other
                for other in [*group["selected_record_ids"], *group["omitted_record_ids"]]
                if other != rid
            ]
            chosen.sort(
                key=lambda other: abs(index.record_positions[other] - index.record_positions[rid])
            )
            selected = chosen[: policy.group_member_limit]
            refs.append({"group_view_id": gid, "kind": group["kind"]})
            if group["kind"] == "field_group":
                sibling_ids.extend(selected)
        # Ordinary siblings contribute headings/field labels only, not a copied table.
        local["sibling_headings"] = [
            summary.heading
            for summary in sibling_nodes[
                index.nodes_by_id.get(record.section_node_id, {}).get("parent_id")
            ]
            if summary.node_id != record.section_node_id
        ][: policy.sibling_limit]
        local["field_siblings"] = [
            {
                "record_id": other,
                "text": include(
                    "field_sibling",
                    index.record_views_by_id[other].source_refs,
                ),
            }
            for other in list(dict.fromkeys(sibling_ids))[: policy.sibling_limit]
        ]
        variants = {"self": own, "context": local}
        for kind, payload in variants.items():
            texts[(rid, kind)] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        prepared[rid] = (record, source, node, roles, own_roles, refs)
    keys = list(texts)
    counts = count_tokens_batch([texts[key] for key in keys])
    if len(counts) != len(keys):
        raise ValueError("ranking_model_returned_incomplete_batch")
    counts = dict(zip(keys, counts, strict=True))
    result = {}
    for rid, (record, source, node, roles, own_roles, refs) in prepared.items():
        variants = {
            kind: {
                "model_text": texts[(rid, kind)],
                "source_roles": own_roles if kind == "self" else roles,
                "token_count": counts[(rid, kind)],
                "status": "complete"
                if counts[(rid, kind)] <= max_record_tokens
                else "not_rerankable",
            }
            for kind in ("self", "context")
        }
        result[rid] = ContextualView(
            record_id=rid,
            model_text=variants["context"]["model_text"],
            retrieval_view_hash=evidence_hash(
                [
                    CONTEXT_VIEW_VERSION,
                    view_configuration(policy),
                    source,
                    roles,
                    refs,
                    {kind: item["model_text"] for kind, item in variants.items()},
                ]
            ),
            source_snapshot_hash=index.ir.document_hash,
            structure_text=record.text,
            metadata_text=node.summary or "" if node else "",
            source_refs=source.source_refs,
            binding_refs=[*source.header_refs, *source.parent_context_refs, *source.note_refs],
            token_count=variants["context"]["token_count"],
            status=variants["context"]["status"],
            source_roles=roles,
            group_refs=refs,
            variants=variants,
            view_policy_version=CONTEXT_VIEW_VERSION,
        )
    return result


def select_available_view(view, query_tokens, pair_limit):
    for kind in ("context", "self"):
        variant = view.variants[kind]
        if variant["token_count"] + query_tokens <= pair_limit:
            return view.model_copy(
                update={
                    "model_text": variant["model_text"],
                    "token_count": variant["token_count"],
                    "status": "complete",
                    "source_roles": variant["source_roles"],
                    "selected_variant": kind,
                    "context_complete": kind == "context",
                }
            )
    return view.model_copy(update={"status": "not_rerankable"})


def anchor_hits(index, record_id, terms, *, context_ids=()):
    """Literal matches have anchors; semantic scores have input provenance only."""
    record = index.by_id[record_id]
    hits = []
    units = [("self", u) for u in record.source_units]
    units += [("header", u) for u in record.header_units]
    units += [("condition_context", u) for u in record.note_units]
    units += [("parent_table", u) for u in record.parent_units]
    for other in context_ids:
        if other != record_id:
            units += [("sibling", u) for u in index.by_id[other].source_units]
    for role, unit in units:
        compact = re.sub(r"\s+", "", unit.text).casefold()
        for term in dict.fromkeys(terms):
            if term and re.sub(r"\s+", "", term).casefold() in compact:
                hits.append(
                    {
                        "term": term,
                        "role": role,
                        "attribution": "literal_match",
                        "anchor": index.ir.anchor(unit.evidence_id).model_dump(mode="json"),
                    }
                )
    return hits


def contextual_channels(query, views, record_ids):
    """Separate direct/context signals; group hits select bounded real records."""
    from app.services.extraction.ontology_guided.semantic_retrieval import (
        query_terms,
        sparse_scores,
    )

    terms = query_terms(query)
    signals = {
        kind: sparse_scores(
            {rid: views[rid].variants[kind]["model_text"] for rid in record_ids}, terms
        )
        for kind in ("self", "context")
    }
    scores = {
        rid: signals["self"].get(rid, 0) + signals["context"].get(rid, 0) for rid in record_ids
    }
    groups = {}
    for rid in record_ids:
        for group in views[rid].group_refs:
            if not scores[rid]:
                continue
            gid = group["group_view_id"]
            # A group nominates members; its score is not copied to member scores.
            groups.setdefault(gid, set()).add(rid)
    available = set(record_ids)
    group_order = []
    for members in groups.values():
        group_order.extend(sorted(members & available, key=lambda rid: (-scores[rid], rid)))
    positions = {rid: i for i, rid in enumerate(record_ids)}
    orders = {
        kind: sorted(values, key=lambda rid: (-values[rid], positions[rid]))
        for kind, values in signals.items()
    }
    orders["group"] = list(dict.fromkeys(group_order))
    signals["group"] = {}  # Attribution is in group_refs; there is no fake per-record group score.
    return orders, signals
