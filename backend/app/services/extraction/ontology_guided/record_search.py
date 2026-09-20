"""Sparse record/card retrieval. Similarity admits work, never proves a fact."""

from __future__ import annotations

from collections import Counter

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.current_work import WorkMap
from app.services.extraction.ontology_guided.reading_groups import heading_context
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

    def __init__(self, slots, *, cards, ordinary_cards, ontology, index, policy):
        self.cards, self.ordinary_cards = cards, ordinary_cards
        self.ontology, self.index, self.policy = ontology, index, policy
        self.scope_hash = evidence_hash([
            RECORD_SEARCH_VERSION, slots, ordinary_cards, policy, index.ir.document_hash,
        ])
        self.rows = WorkMap()
        preceding_group = None
        for position, (sources, card_ids, field_id) in enumerate(slots):
            key = evidence_hash([sources, field_id])
            self.rows[key] = {
                "source_record_ids": sources, "field_id": field_id, "position": position,
                "release_after": preceding_group if field_id else None,
                "ranked": bool(field_id), "admitted": 0, "unselected": 0,
                "card_counts": {},
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
            ))
                    or any(item["card_id"] not in self.cards for item in row["remaining"])):
                raise ValueError("record_search_scope_changed")
        self.rows = restored

    def prepare(self, ranking, checkpoint):
        pending = [(key, row) for key, row in self.rows.items() if not row["ranked"]]
        if not pending:
            return
        card_views = {key: card_text(self.cards[key], self.ontology)
                      for key in self.ordinary_cards}
        record_views = {key: record_text(row["source_record_ids"], self.index)
                        for key, row in pending}
        if ranking.policy.mode == "semantic":
            views = {**card_views, **record_views}
            vectors = ranking.discovery_embeddings(
                views, permission_scope=self.scope_hash, checkpoint=checkpoint,
            )
            card_vectors = {key: vectors[key] for key in card_views}
        for key, row in pending:
            if ranking.policy.mode == "semantic":
                scores = cosine_scores(vectors[key], card_vectors)
            else:
                # Explicit deterministic mode is useful for isolated tests and
                # configured offline operation; never a failure fallback.
                text = record_views[key].casefold()
                scores = {}
                for card_id in card_views:
                    terms = {cls.label.casefold() for cls in self.cards[card_id].class_cards}
                    terms.update(prop.label.casefold() for cls in self.cards[card_id].class_cards
                                 for prop in cls.properties)
                    matches = sum(bool(term) and term in text for term in terms)
                    scores[card_id] = matches / (matches + 1)
            ordered = sorted(scores, key=lambda identity: (-scores[identity], identity))
            selected = [identity for identity in ordered
                        if scores[identity] >= self.policy.minimum_similarity
                        and (ranking.policy.mode == "semantic" or scores[identity] > 0)
                        ][:self.policy.candidate_cards_per_record]
            self.rows[key] = {
                **row, "ranked": True, "unselected": len(scores) - len(selected),
                "remaining": [{"card_id": identity, "score": scores[identity]}
                              for identity in selected],
            }

    def take(self):
        ordinary = [(key, row) for key, row in self.rows.items()
                    if not row["field_id"] and row["remaining"]]
        fields = [(key, row) for key, row in self.rows.items()
                  if row["field_id"] and row["remaining"]]
        admitted = [row for row in self.rows.values() if not row["field_id"] and row["admitted"]]
        # Fields follow the first discovered group at/after their source, as in
        # contextual reading. Do not delay them until every candidate card ran.
        def field_ready(row):
            owner = self.rows.get(row["release_after"])
            return not ordinary or bool(
                owner and owner["admitted"] or admitted and (
                    owner is None or owner["ranked"] and not owner["remaining"]
                )
            )

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
                                     and not row["remaining"] for row in ordinary),
        }
