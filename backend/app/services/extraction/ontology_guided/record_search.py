"""Sparse record/card retrieval. Similarity admits work, never proves a fact."""

from __future__ import annotations

import re
from collections import Counter

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.current_work import WorkMap
from app.services.extraction.ontology_guided.reading_groups import heading_context
from app.services.extraction.ontology_guided.record_discovery import merge_record_schema_cards
from app.services.extraction.ontology_guided.schema_region_routing import (
    metadata_node_text,
    metadata_region_is_bounded,
    resolve_metadata_region,
    routing_card_text,
    routing_heading_match,
)
from app.services.extraction.ontology_guided.semantic_retrieval import cosine_scores
from app.services.extraction.ontology_guided.table_reading import table_lead_in_refs

RECORD_SEARCH_VERSION = "semantic-record-discovery-v1"


def card_text(card, ontology):
    """Semantic labels/descriptions, without transport schemas or opaque IDs."""
    lines = []
    for cls in card.class_cards:
        lines.extend([cls.label, ontology.classes[cls.class_iri].description])
        lines.extend(prop.label for prop in cls.properties)
        if ontology.lexical_context:
            lines.extend(term.text for term in ontology.lexical_context.annotations.get(
                cls.class_iri, [],
            ))
    return "\n".join(dict.fromkeys(line for line in lines if line))


def record_text(source_ids, index):
    # Headings assist retrieval only. assemble_record_discovery_context retains
    # the original per-fragment fact permissions for the actual model request.
    headings = dict.fromkeys(ref.evidence_id for rid in source_ids
                             for ref in heading_context(index, rid))
    sources = {unit.evidence_id for rid in source_ids for unit in index.by_id[rid].source_units}
    return "\n".join([
        *(index.ir.unit(identity).text for identity in headings),
        *(index.ir.resolve(ref) for ref in table_lead_in_refs(index, source_ids)
          if ref.evidence_id not in sources),
        *(index.source_text(rid, include_context=True) for rid in source_ids),
    ])


class RecordSearch:
    """One current shortlist per reading group; no record × card task ledger."""

    def __init__(
        self,
        slots,
        *,
        cards,
        ordinary_cards,
        ontology,
        index,
        policy,
        routing_cards=(),
        metadata=None,
    ):
        self.cards, self.ordinary_cards = cards, ordinary_cards
        self.ontology, self.index, self.policy = ontology, index, policy
        self.routing_cards = {card.routing_card_id: card for card in routing_cards}
        self.metadata = metadata
        self.routing_enabled = policy.schema_region_routing is not None
        if self.routing_cards and not self.routing_enabled:
            raise ValueError("record_search_routing_policy_required")
        if self.routing_enabled and metadata is None:
            raise ValueError("record_search_routing_metadata_required")
        if any(card_id not in cards for card in self.routing_cards.values()
               for card_id in card.execution_card_ids):
            raise ValueError("record_search_routing_execution_card_missing")
        self.scope_hash = evidence_hash([
            RECORD_SEARCH_VERSION, slots, ordinary_cards, policy, index.ir.document_hash,
            list(self.routing_cards.values()),
            metadata.dependency_hash if metadata is not None and self.routing_enabled else None,
        ])
        self.rows = WorkMap()
        preceding_group = None
        for position, (sources, card_ids, field_id) in enumerate(slots):
            key = evidence_hash([sources, field_id])
            self.rows[key] = {
                "source_record_ids": sources, "field_id": field_id, "position": position,
                "release_after": preceding_group if field_id else None,
                "ranked": bool(field_id), "admitted": 0, "unselected": 0,
                "card_counts": {}, "card_projections": {},
                "attached_card_ids": [] if field_id else list(card_ids),
                "remaining": [{"card_id": card_id, "score": 1.0} for card_id in card_ids]
                if field_id else [],
            }
            if not field_id:
                preceding_group = key

    def restore(self, rows):
        restored = WorkMap.load(rows)
        if set(restored) != set(self.rows):
            raise ValueError("record_search_scope_changed")
        for key, row in restored.items():
            expected = self.rows[key]
            if (any(row[k] != expected[k] for k in (
                "source_record_ids", "field_id", "position", "release_after",
            ))):
                raise ValueError("record_search_scope_changed")
            execution_card_id = row.get("execution_card_id")
            if execution_card_id:
                self._merged_card(
                    row.get("source_card_ids", []), expected_id=execution_card_id,
                )
            for card_id, projection in row["card_projections"].items():
                if projection["source_card_ids"] != row.get("source_card_ids", []):
                    raise ValueError("record_search_projection_source_changed")
                self._projected_card(
                    projection["source_card_ids"], projection["class_iris"],
                    expected_id=card_id,
                )
            used_cards = {item["card_id"] for item in row["remaining"]} | set(row["card_counts"])
            if any(card_id not in self.cards for card_id in used_cards):
                raise ValueError("record_search_scope_changed")
        self.rows = restored

    def _merged_card(self, card_ids, *, expected_id=None, register=True):
        identities = list(dict.fromkeys(card_ids))
        if any(identity not in self.cards for identity in identities):
            raise ValueError("record_search_source_card_missing")
        card = merge_record_schema_cards([self.cards[identity] for identity in identities])
        if expected_id is not None and card.schema_card_id != expected_id:
            raise ValueError("record_search_merged_card_changed")
        if register:
            self.cards.setdefault(card.schema_card_id, card)
        return card

    def _projected_card(self, card_ids, class_iris, *, expected_id=None, register=True):
        """Rebuild a merged card and retain only the requested ontology classes."""
        merged = self._merged_card(card_ids, register=register)
        requested = set(class_iris)
        if requested - set(merged.class_iris):
            raise ValueError("record_schema_projection_outside_source")
        class_cards = [
            item for item in merged.class_cards if item.class_iri in requested
        ]
        if not class_cards:
            raise ValueError("record_schema_projection_empty")
        payload = {
            "ontology_snapshot_id": merged.ontology_snapshot_id,
            "analysis_scope_ref": merged.analysis_scope_ref,
            "class_cards": class_cards,
        }
        card = merged.__class__(schema_card_id=evidence_hash(payload), **payload)
        if expected_id is not None and card.schema_card_id != expected_id:
            raise ValueError("record_search_projected_card_changed")
        if register:
            self.cards.setdefault(card.schema_card_id, card)
        return card

    @staticmethod
    def _lexical_scores(query, candidates):
        text = query.casefold()
        scores = {}
        for identity, candidate in candidates.items():
            terms = {line.strip().casefold() for line in candidate.splitlines() if line.strip()}
            matches = sum(term in text for term in terms)
            scores[identity] = matches / (matches + 1)
        return scores

    def prepare(self, ranking, checkpoint):
        pending = [(key, row) for key, row in self.rows.items() if not row["ranked"]]
        if not pending:
            return
        card_views = {key: card_text(self.cards[key], self.ontology)
                      for key in self.ordinary_cards}
        record_views = {key: record_text(row["source_record_ids"], self.index)
                        for key, row in pending}
        route_views = {
            key: routing_card_text(card) for key, card in self.routing_cards.items()
        }
        regions = {}
        metadata_views = {}
        metadata_nodes = {}
        if self.routing_enabled:
            route_policy = self.policy.schema_region_routing
            for node in self.metadata.node_summaries:
                resolved = resolve_metadata_region(node.node_id, self.metadata, self.index)
                view = metadata_node_text(node)
                if (resolved is not None and view and metadata_region_is_bounded(
                        node, resolved, max_records=route_policy.max_region_records)):
                    regions[node.node_id] = resolved
                    metadata_views[node.node_id] = view
                    metadata_nodes[node.node_id] = node
        if ranking.policy.mode == "semantic":
            views = {
                **{f"card:{key}": value for key, value in card_views.items()},
                **{f"record:{key}": value for key, value in record_views.items()},
                **{f"route:{key}": value for key, value in route_views.items()},
                **{f"metadata:{key}": value for key, value in metadata_views.items()},
            }
            vectors = ranking.discovery_embeddings(
                views, permission_scope=self.scope_hash, checkpoint=checkpoint,
            )
            card_vectors = {key: vectors[f"card:{key}"] for key in card_views}

        routed_records = {}
        route_selections = {}
        if self.routing_enabled:
            metadata_order = {
                node.node_id: position
                for position, node in enumerate(self.metadata.node_summaries)
            }
            for route_id, view in route_views.items():
                if ranking.policy.mode == "semantic":
                    scores = cosine_scores(
                        vectors[f"route:{route_id}"],
                        {key: vectors[f"metadata:{key}"] for key in metadata_views},
                    )
                else:
                    scores = {
                        identity: self._lexical_scores(
                            metadata_view, {route_id: view},
                        )[route_id]
                        for identity, metadata_view in metadata_views.items()
                    }
                heading_matches = {
                    identity: routing_heading_match(
                        self.routing_cards[route_id], metadata_nodes[identity],
                    )
                    for identity in metadata_views
                }
                resolution_priority = {"direct": 2, "adjacent": 1, "descendant": 0}
                candidates = [
                    identity
                    for identity in sorted(scores, key=lambda key: (
                        -heading_matches[key],
                        -resolution_priority[regions[key][1]],
                        -scores[key],
                        key,
                    ))
                    if (heading_matches[identity]
                        or scores[identity] >= route_policy.minimum_similarity)
                    and (ranking.policy.mode == "semantic" or scores[identity] > 0)
                ]
                explicit = [identity for identity in candidates if heading_matches[identity]]
                selected = explicit[:route_policy.max_regions_per_card]
                if selected and len(selected) < route_policy.max_regions_per_card:
                    primary = selected[0]
                    parent_id = self.index.nodes_by_id.get(primary, {}).get("parent_id")
                    preceding_siblings = sorted(
                        (
                            identity for identity in candidates
                            if identity not in selected
                            and metadata_order[identity] < metadata_order[primary]
                            and self.index.nodes_by_id.get(identity, {}).get("parent_id")
                            == parent_id
                        ),
                        key=lambda identity: metadata_order[identity],
                        reverse=True,
                    )
                    if preceding_siblings:
                        selected.append(preceding_siblings[0])
                selected.extend(
                    identity for identity in candidates
                    if identity not in selected
                )
                selected = selected[:route_policy.max_regions_per_card]
                route_selections[route_id] = [
                    {
                        "node_id": identity,
                        "score": scores[identity],
                        "resolution": regions[identity][1],
                    }
                    for identity in selected
                ]
                routed_records[route_id] = {
                    record_id for identity in selected for record_id in regions[identity][0]
                }
        for key, row in pending:
            eligible_routes = [
                route_id for route_id, records in routed_records.items()
                if records.intersection(row["source_record_ids"])
            ]
            candidate_cards = (
                sorted({
                    card_id
                    for route_id in eligible_routes
                    for card_id in self.routing_cards[route_id].execution_card_ids
                })
                if self.routing_enabled else list(card_views)
            )
            if not candidate_cards:
                self.rows[key] = {
                    **row,
                    "ranked": True,
                    "routing_status": "unrouted" if self.routing_enabled else "no_candidates",
                    "route_card_ids": eligible_routes,
                    "route_selections": {
                        route_id: route_selections[route_id] for route_id in eligible_routes
                    },
                }
                continue
            if ranking.policy.mode == "semantic":
                scores = cosine_scores(
                    vectors[f"record:{key}"],
                    {identity: card_vectors[identity] for identity in candidate_cards},
                )
            else:
                # Explicit deterministic mode is useful for isolated tests and
                # configured offline operation; never a failure fallback.
                scores = self._lexical_scores(
                    record_views[key],
                    {identity: card_views[identity] for identity in candidate_cards},
                )
            ordered = sorted(scores, key=lambda identity: (-scores[identity], identity))
            selected = [identity for identity in ordered
                        if scores[identity] >= self.policy.minimum_similarity
                        and (ranking.policy.mode == "semantic" or scores[identity] > 0)
                        ][:self.policy.candidate_cards_per_record]
            source_card_ids = (
                list(dict.fromkeys([*selected, *row["attached_card_ids"]])) if selected else []
            )
            if (selected and self.routing_enabled
                    and self.policy.schema_region_routing.execution_mode == "region_batch"):
                card = self._merged_card(source_card_ids)
                remaining = [{
                    "card_id": card.schema_card_id,
                    "score": max(scores[identity] for identity in selected),
                }]
                execution_card_id = card.schema_card_id
            else:
                remaining = [
                    {"card_id": identity, "score": scores.get(identity, 1.0)}
                    for identity in source_card_ids
                ]
                execution_card_id = None
            self.rows[key] = {
                **row, "ranked": True, "unselected": len(scores) - len(selected),
                "remaining": remaining,
                "source_card_ids": source_card_ids,
                "execution_card_id": execution_card_id,
                **({
                    "routing_status": "routed" if selected else "routed_no_execution_card",
                    "route_card_ids": eligible_routes,
                    "route_selections": {
                        route_id: route_selections[route_id] for route_id in eligible_routes
                    },
                } if self.routing_enabled else {}),
            }

    def take(self):
        ordinary = [(key, row) for key, row in self.rows.items()
                    if not row["field_id"] and row["remaining"]]
        fields = [(key, row) for key, row in self.rows.items()
                  if row["field_id"] and row["remaining"]]
        admitted = [row for row in self.rows.values() if not row["field_id"] and row["admitted"]]
        # A field's owner candidates are produced by ordinary discovery. Run the
        # one bounded disambiguation only after that pool is complete; releasing
        # it after the first entity forces the same field and source text to run
        # again whenever a later candidate appears.
        def field_ready(row):
            return not ordinary

        ready_fields = [(key, row) for key, row in fields if field_ready(row)]
        section_counts, card_counts = Counter(), Counter()

        def section(row):
            return self.index.by_id[row["source_record_ids"][0]].section_node_id

        for row in admitted:
            section_counts[section(row)] += row["admitted"]
            card_counts.update(row["card_counts"])

        def priority(item):
            row = item[1]
            choice = row["remaining"][0]
            return (section_counts[section(row)], card_counts[choice["card_id"]],
                    row["admitted"], -choice["score"], row["position"])

        urgent = [(key, row) for key, row in ordinary if row["remaining"][0].get("priority")]
        if urgent:
            key, row = min(urgent, key=priority)
        elif ready_fields:
            key, row = min(ready_fields, key=lambda item: item[1]["position"])
        elif ordinary:
            key, row = min(ordinary, key=priority)
        else:
            return None
        choice, *remaining = row["remaining"]
        counts = Counter(row["card_counts"])
        counts[choice["card_id"]] += 1
        self.rows[key] = {**row, "remaining": remaining, "admitted": row["admitted"] + 1,
                          "card_counts": dict(counts)}
        return row["source_record_ids"], choice["card_id"], row["field_id"]

    def prioritize(self, record_id, class_iris):
        """Bring an already selected endpoint card forward; do not expand recall."""
        for key, row in self.rows.items():
            if row["field_id"] or record_id not in row["source_record_ids"]:
                continue
            for choice in row["remaining"]:
                if set(self.cards[choice["card_id"]].class_iris) & class_iris:
                    self.rows[key] = {
                        **row, "remaining": [{**choice, "priority": True},
                                              *(item for item in row["remaining"]
                                                if item["card_id"] != choice["card_id"])],
                    }
                    return True
        return False

    @staticmethod
    def _contentful_source(text):
        """Distinguish a factual value or sentence from an empty form label."""
        value = text.strip()
        if not value:
            return False
        parts = re.split(r"[:：]", value, maxsplit=1)
        return len(parts) == 1 or bool(parts[1].strip())

    def _dependency_priority(self, row, choice, class_iris):
        """Rank endpoint evidence before property-only or empty record groups."""
        terms = {
            self.ontology.classes[class_iri].label.casefold()
            for class_iri in class_iris
            if class_iri in self.ontology.classes
            and self.ontology.classes[class_iri].label.strip()
        }
        for route_id in row.get("route_card_ids", []):
            route = self.routing_cards.get(route_id)
            if route is None or not set(route.range_class_iris).intersection(class_iris):
                continue
            terms.update(label.casefold() for label in route.range_type_labels)
            terms.update(
                term[-2:]
                for term in route.range_type_labels
                if len(term) > 2 and re.fullmatch(r"[\u4e00-\u9fff]+", term)
            )
        sources = [
            self.index.by_id[record_id].text.strip()
            for record_id in row["source_record_ids"]
        ]
        contentful = [text for text in sources if self._contentful_source(text)]
        normalized = [text.casefold() for text in contentful]
        identity_mentions = sum(any(term in text for term in terms) for text in normalized)
        return (
            -identity_mentions,
            -len(contentful),
            -choice["score"],
            row["position"],
            choice["card_id"],
        )

    def admit_dependency(self, record_id, class_iris, attempted_cards):
        """Admit one exact-range card when a planned relation lacks an endpoint.

        Prefer a card that region routing and semantic retrieval already selected.
        A routed run must not force an unselected card onto an unrelated or empty
        relationship record merely because that relationship is waiting.
        """
        # Without region routing the relationship record is the only authorized
        # retrieval anchor. A routed run has a stronger signal: every selected
        # region's record/card score. Compare those groups before choosing one,
        # otherwise the first (often empty) group in the relationship's section
        # wins merely because it contains ``record_id``.
        if not self.routing_enabled and self.prioritize(record_id, class_iris):
            return True
        if self.routing_enabled:
            eligible = []
            for key, row in self.rows.items():
                if row["field_id"] or row.get("routing_status") != "routed":
                    continue
                for choice in row["remaining"]:
                    choice_classes = set(self.cards[choice["card_id"]].class_iris)
                    exact_classes = choice_classes & class_iris
                    if not exact_classes:
                        continue
                    source_card_ids = row.get("source_card_ids", [choice["card_id"]])
                    # Comparing candidates must not freeze unused cache entries.
                    # Only the selected row owns a recoverable projection recipe.
                    exact = self._projected_card(source_card_ids, exact_classes, register=False)
                    if exact.schema_card_id in attempted_cards:
                        continue
                    eligible.append((key, row, choice, exact, choice_classes - exact_classes))
            if not eligible:
                return False
            key, row, choice, exact, remainder_classes = min(
                eligible,
                key=lambda item: self._dependency_priority(item[1], item[2], class_iris),
            )
            projection = {
                "source_card_ids": row.get("source_card_ids", [choice["card_id"]]),
                "class_iris": sorted(exact.class_iris),
            }
            self._projected_card(
                projection["source_card_ids"], projection["class_iris"],
                expected_id=exact.schema_card_id,
            )
            projections = {**row["card_projections"], exact.schema_card_id: projection}
            exact_choice = {
                **choice,
                "card_id": exact.schema_card_id,
                "priority": True,
                "dependency_admission": True,
            }
            remainder = []
            routed_range_classes = {
                class_iri
                for route_id in row.get("route_card_ids", [])
                for class_iri in (
                    self.routing_cards[route_id].class_iris
                    if self.policy.graph_phase in {"candidate_graph", "evidence_review"}
                    else self.routing_cards[route_id].range_class_iris
                )
            }
            remainder_classes &= routed_range_classes
            if remainder_classes:
                card = self._projected_card(projection["source_card_ids"], remainder_classes)
                projections[card.schema_card_id] = {
                    "source_card_ids": projection["source_card_ids"],
                    "class_iris": sorted(card.class_iris),
                }
                remainder = [{
                    **choice,
                    "card_id": card.schema_card_id,
                }]
            self.rows[key] = {
                **row,
                # Keep adopted recipes after take() consumes their queue items.
                "card_projections": projections,
                "remaining": [
                    exact_choice,
                    *remainder,
                    *(item for item in row["remaining"]
                      if item["card_id"] != choice["card_id"]),
                ],
            }
            return True
        attempted_classes = {
            class_iri
            for card_id in attempted_cards
            if card_id in self.cards
            for class_iri in self.cards[card_id].class_iris
        }
        needed_classes = class_iris - attempted_classes
        if not needed_classes:
            return False
        for key, row in self.rows.items():
            if row["field_id"] or record_id not in row["source_record_ids"]:
                continue
            remaining = {item["card_id"] for item in row["remaining"]}
            eligible = [
                card_id
                for card_id in self.ordinary_cards
                if card_id not in attempted_cards and card_id not in remaining
                and set(self.cards[card_id].class_iris) & needed_classes
            ]
            if not eligible:
                return False

            def ancestors(class_iri):
                pending = [class_iri]
                visited = set()
                while pending:
                    identity = pending.pop()
                    if identity in visited:
                        continue
                    visited.add(identity)
                    definition = self.ontology.classes.get(identity)
                    if definition is not None:
                        pending.extend(definition.parent_iris)
                return visited

            ancestry = {identity: ancestors(identity) for identity in needed_classes}
            lexical = self._lexical_scores(
                record_text(row["source_record_ids"], self.index),
                {identity: card_text(self.cards[identity], self.ontology)
                 for identity in eligible},
            )

            def priority(card_id):
                covered = max(
                    (sum(candidate in ancestry[target] for target in needed_classes)
                     for candidate in set(self.cards[card_id].class_iris) & needed_classes),
                    default=0,
                )
                return (-covered, -lexical[card_id], card_id)

            selected = min(eligible, key=priority)
            self.rows[key] = {
                **row,
                "remaining": [{
                    "card_id": selected,
                    "score": lexical[selected],
                    "priority": True,
                    "dependency_admission": True,
                }, *row["remaining"]],
                "source_card_ids": list(dict.fromkeys([
                    *row.get("source_card_ids", []), selected,
                ])),
                "routing_status": (
                    "routed_dependency"
                    if row.get("routing_status") == "unrouted"
                    else row.get("routing_status", "dependency_admitted")
                ),
                "dependency_admissions": row.get("dependency_admissions", 0) + 1,
            }
            return True
        return False

    def admit_feedback(self, record_id, class_iris, attempted_cards):
        """An explicit source gap may nominate one missing relevant card."""
        if self.prioritize(record_id, class_iris):
            return
        for key, row in self.rows.items():
            if row["field_id"] or record_id not in row["source_record_ids"]:
                continue
            if row.get("feedback_admitted"):
                return
            selected = {item["card_id"] for item in row["remaining"]}
            if any(set(self.cards[identity].class_iris) & class_iris
                   for identity in selected | attempted_cards):
                return
            for card_id in self.ordinary_cards:
                if card_id not in selected and set(self.cards[card_id].class_iris) & class_iris:
                    self.rows[key] = {
                        **row, "remaining": [{"card_id": card_id, "score": 1.0,
                                               "priority": True},
                                             *row["remaining"]],
                        "unselected": max(0, row["unselected"] - 1), "feedback_admitted": True,
                    }
                    return

    @property
    def pending(self):
        return sum(len(row["remaining"]) if row["ranked"] else 1
                   for row in self.rows.values())

    @property
    def unselected(self):
        return sum(row["unselected"] for row in self.rows.values())

    @property
    def unrouted(self):
        return sum(row.get("routing_status") == "unrouted" for row in self.rows.values()
                   if not row["field_id"])

    def coverage_counts(self, discovery_rows):
        """Account for every frozen group exactly once in graph progress."""
        rows = list(discovery_rows)
        return {
            "planned": len(rows) + self.pending + self.unrouted,
            "examined": sum(row["status"] == "examined" for row in rows),
            "incomplete": sum(
                row["status"] in {"active", "incomplete", "pending"} for row in rows
            ),
            "unattempted": self.pending + self.unrouted,
        }

    def diagnostics(self, mode):
        ordinary = [row for row in self.rows.values() if not row["field_id"]]
        return {
            "policy": RECORD_SEARCH_VERSION, "mode": mode,
            "reading_groups": len(ordinary),
            "ranked_groups": sum(row["ranked"] for row in ordinary),
            "remaining_pairs": sum(len(row["remaining"]) for row in ordinary),
            "admitted_pairs": sum(row["admitted"] for row in ordinary),
            "unselected_pairs": self.unselected,
            "unselected_groups": sum(row["ranked"] and not row["admitted"]
                                     and not row["remaining"]
                                     and row.get("routing_status") != "unrouted"
                                     for row in ordinary),
            **({
                "routing_cards": len(self.routing_cards),
                "metadata_nodes": len(self.metadata.node_summaries),
                "routed_groups": sum(row.get("routing_status", "").startswith("routed")
                                     for row in ordinary),
                "unrouted_groups": sum(row.get("routing_status") == "unrouted"
                                       for row in ordinary),
                "selected_regions": len({
                    item["node_id"] for row in ordinary
                    for selections in row.get("route_selections", {}).values()
                    for item in selections
                }),
                "execution_mode": self.policy.schema_region_routing.execution_mode,
                "property_field_mode": self.policy.schema_region_routing.property_field_mode,
            } if self.routing_enabled else {}),
        }
