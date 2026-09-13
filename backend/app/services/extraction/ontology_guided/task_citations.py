"""Closed atomic citation transport for the shared executor's TaskContext."""

import json
import re
from copy import deepcopy

from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

TASK_CITATION_VERSION = "ontology-task-citations-v1"
_ENDPOINTS = {"object_quote", "value_quote"}
_SUPPORTS = {"type_support", "predicate_support", "subject_support",
             "condition_support", "condition_quote", "counterevidence_support",
             "field_role_support", "bridge_support", "source_unit_quote", "unit_binding_support"}
_PROOFS = {"type_support", "predicate_support", "subject_support", "counterevidence_support",
           "field_role_support", "bridge_support", "unit_binding_support"}
INSTRUCTION = (
    "本次使用原子引用：名称和属性值必须给evidence_id及唯一逐字text；"
    "类型/谓词/主体/反证的整来源证明只给evidence_id，程序回放原文。"
    "条件仍用精确text。禁止坐标、省略号、改写、跨段/跨格拼接。"
    "FactQuote只允许当前target，BindingProof只允许当前可见绑定背景；背景不能"
    "用来提出新对象或值。E编号是原文来源，候选和target的冻结ID不能当原文引用。"
)


def _integer_quote_alternatives(context, aliases):
    """Bind lexical integers to a source and an actually resolvable locating quote."""
    grouped = {}
    for fragment in context.fragments:
        if not fragment.fact_eligible:
            continue
        identity = fragment.anchor.evidence_id
        numbers = dict.fromkeys(
            match.group() for match in re.finditer(r"(?<![\d.])[+-]?\d+(?![\d.])", fragment.text)
        )
        phrases = sorted(
            set(re.split(r"[，。；;\n]", fragment.text)) - {""}, key=lambda s: (len(s), s),
        )
        for number in numbers:
            positions = set()
            for phrase in [None, *phrases]:
                try:
                    anchor, _ = resolve_fragment_quote(
                        identity, number, context.fragments,
                        fact_required=True, context_text=phrase,
                    )
                except ValueError:
                    continue
                position = (anchor.span_start, anchor.span_end)
                if position not in positions:
                    positions.add(position)
                    values = grouped.setdefault((aliases[identity], phrase), [])
                    if number not in values:
                        values.append(number)
                if phrase is None:
                    break  # Already unique in the authorized source.
    return [{
        "type": "object", "additionalProperties": False,
        "properties": {
            "evidence_id": {"type": "string", "const": alias},
            "text": {"type": "string", "enum": values},
            "context_text": {"type": "null" if phrase is None else "string", "const": phrase},
        },
        "required": ["evidence_id", "text", "context_text"],
    } for (alias, phrase), values in grouped.items()]


class TaskCitationProtocol:
    def __init__(self, context, request, response_type, system):
        self.context = context
        self.repair = request.get("protocol_version") == "evidence-repair-v1"
        self.references = {
            "evidence_id": {f"E{i + 1}": identity for i, identity in enumerate(dict.fromkeys(
                fragment.anchor.evidence_id for fragment in context.fragments))},
            "field_binding_id": {f"B{i + 1}": binding.field_binding_id
                                 for i, binding in enumerate(context.field_bindings)},
            "candidate_id": {item["candidate_id"]: item["candidate_id"]
                             for item in request.get("candidates", [])},
            "target_id": {item["target_id"]: item["target_id"]
                          for item in request.get("candidates", [])},
        }
        self.reverse = {field: {value: key for key, value in values.items()}
                        for field, values in self.references.items()}
        self.schema = deepcopy(response_type.model_json_schema())
        definitions = self.schema.get("$defs", {})
        fact_ids = {fragment.anchor.evidence_id for fragment in context.fragments
                    if fragment.fact_eligible}
        quote = deepcopy(definitions.get("Quote", {}))
        for name, ids, proof in (
            ("FactQuote", fact_ids, False),
            ("BindingQuote", set(self.reverse["evidence_id"]), False),
            ("BindingProof", set(self.reverse["evidence_id"]), True),
        ):
            definition = deepcopy(quote)
            definition["properties"]["evidence_id"]["enum"] = [
                alias for alias, identity in self.references["evidence_id"].items()
                if identity in ids]
            if proof:
                definition["properties"].pop("text", None)
                definition["required"] = ["evidence_id"]
                # Fragmented authorizations cannot use an ID-only proof.
                try:
                    for identity in ids:
                        resolve_fragment_quote(identity, None, context.fragments)
                except ValueError:
                    definition = deepcopy(definitions["BindingQuote"])
            definitions[name] = definition

        if "LocatedValueQuote" in definitions:
            definitions["LocatedValueQuote"]["properties"]["evidence_id"]["enum"] = [
                alias for alias, identity in self.references["evidence_id"].items()
                if identity in fact_ids
            ]
            # Context is a locating quote, not a column label. Closed verbatim
            # sentence/clause choices prevent the model from copying a header
            # from a different cell into the value's contextual quote.
            phrases = list(dict.fromkeys(
                text for fragment in context.fragments if fragment.fact_eligible
                for text in [fragment.text, *re.split(r"[，。；;\n]", fragment.text)] if text
            ))
            definitions["LocatedValueQuote"]["properties"]["context_text"] = {
                "anyOf": [{"type": "string", "enum": phrases}, {"type": "null"}],
                "default": None,
                "description": "原文值所在的同一来源短语，仅用于消除重复值歧义；无需定位时null。",
            }
            predicate = request["predicate"]
            integer_types = {
                "http://www.w3.org/2001/XMLSchema#" + name
                for name in ("integer", "int", "nonNegativeInteger", "positiveInteger")
            }
            types = set(predicate.get("datatype_iris", []))
            if (self.repair and len(types) == 1 and types <= integer_types
                    and predicate.get("constraint_status") == "resolved"
                    and not predicate.get("canonical_unit")):
                # Source-only lexical choices; the later proof still establishes
                # which number has this field's role and belongs to this subject.
                alternatives = _integer_quote_alternatives(context, self.reverse["evidence_id"])
                if alternatives:
                    definitions["LocatedValueQuote"] = {"oneOf": alternatives}
                elif request["stage"] == "discovery":
                    self.schema["properties"]["proposals"]["maxItems"] = 0

        if "UnitQuote" in definitions:
            definitions["UnitQuote"]["properties"]["evidence_id"]["enum"] = list(
                self.references["evidence_id"],
            )

        def schema_walk(value, field=""):
            if isinstance(value, dict):
                if value.get("$ref") == "#/$defs/Quote":
                    value["$ref"] = "#/$defs/" + (
                        "FactQuote" if field in _ENDPOINTS else
                        "BindingProof" if field in _PROOFS else "BindingQuote")
                for key, item in value.items():
                    schema_walk(item, field if key in {"items", "anyOf", "oneOf"} else key)
            elif isinstance(value, list):
                for item in value:
                    schema_walk(item, field)

        schema_walk(self.schema)
        # The nested owner verdict cannot claim support without its own source.
        # Only the frozen document root has an explicit programmatic exemption.
        owner_ids = {r.evidence_id for r in context.subject_evidence_refs}
        local_ids = {f.anchor.evidence_id for f in context.fragments if f.fact_eligible}
        for definition, identities in (("OriginalOwnerProof", owner_ids),
                                       ("LocalOwnerProof", local_ids)):
            if self.repair and identities and "SupportedSubjectBinding" in definitions:
                definitions[definition] = deepcopy(definitions["BindingProof"])
                definitions[definition]["properties"]["evidence_id"]["enum"] = [
                    alias for alias, identity in self.references["evidence_id"].items()
                    if identity in identities
                ]
        for name in ("SupportedSubjectBinding", "UnprovenSubjectBinding"):
            fields = definitions.get(name, {}).get("properties", {})
            for field, definition in (("support", "OriginalOwnerProof"),
                                      ("local_support", "LocalOwnerProof")):
                if field not in fields:
                    continue
                fields[field]["items"] = {"$ref": "#/$defs/" + (
                    definition if definition in definitions else "BindingProof"
                )}
                required = definitions[name].setdefault("required", [])
                if field not in required:
                    required.append(field)
                if name == "SupportedSubjectBinding":
                    fields[field]["minItems"] = 1
                if context.target.subject_ref.is_document_root:
                    fields[field].pop("minItems", None)
                    fields[field]["maxItems"] = 0
        repair = request.get("protocol_version") == "evidence-repair-v1"
        if repair:
            selected_bindings = {item["claim"].get("field_binding_id")
                                 for item in request.get("candidates", [])}
            header_ids = {ref.evidence_id for binding in context.field_bindings
                          if binding.field_binding_id in selected_bindings
                          for ref in binding.label_refs}
            if header_ids:
                definitions["FieldRoleProof"] = deepcopy(definitions["BindingProof"])
                definitions["FieldRoleProof"]["properties"]["evidence_id"]["enum"] = [
                    alias for alias, identity in self.references["evidence_id"].items()
                    if identity in header_ids
                ]

            def constrain(value):
                if isinstance(value, dict):
                    fields = value.get("properties", {})
                    if "field_binding_id" in fields and context.field_bindings:
                        fields["field_binding_id"] = {
                            "type": "string", "enum": list(self.references["field_binding_id"]),
                        }
                        value.setdefault("required", []).append("field_binding_id")
                    if "field_role_support" in fields and header_ids:
                        fields["field_role_support"]["items"] = {"$ref": "#/$defs/FieldRoleProof"}
                    if "unit_verdict" in fields and request["predicate"].get("canonical_unit"):
                        value.setdefault("required", []).extend(
                            ["unit_verdict", "source_unit_quote", "unit_binding_support"],
                        )
                    if "bridge_kind" in fields:
                        fields["bridge_kind"]["enum"] = request["proof_menu"]["allowed_bridges"]
                    if "object_class_iri" in fields:
                        fields["object_class_iri"]["enum"] = request["allowed_object_classes"]
                    for field in ("candidate_id", "target_id"):
                        if field in fields:
                            fields[field]["enum"] = list(self.references[field])
                    for item in value.values():
                        constrain(item)
                elif isinstance(value, list):
                    for item in value:
                        constrain(item)
            constrain(self.schema)
        elif request["stage"] == "discovery":
            kind = request["predicate"]["kind"]
            selected = "RelationshipProposal" if kind == "relationship" else "PropertyProposal"
            self.schema["properties"]["proposals"]["items"] = {"$ref": f"#/$defs/{selected}"}
            if kind == "relationship":
                definitions[selected]["properties"]["object_class_iri"]["enum"] = (
                    request["allowed_object_classes"])
        else:
            for name in ("ModelVerification", "RootModelVerification"):
                fields = definitions.get(name, {}).get("properties", {})
                for field in ("candidate_id", "target_id"):
                    if field in fields:
                        fields[field]["enum"] = list(self.references[field])
        self.system = system + INSTRUCTION
        self.allowed_bridges = request.get("proof_menu", {}).get("allowed_bridges")
        self.binding_ids = {b.field_binding_id for b in context.field_bindings} if repair else None
        self.user = json.dumps(self._walk(request, encode=True), ensure_ascii=False,
                               separators=(",", ":"))

    def _walk(self, value, field="", *, encode=False):
        if isinstance(value, dict):
            if (encode and self.repair
                    and {"evidence_id", "document_hash", "structure_hash"} <= value.keys()):
                # Keep exact source span text while omitting repeated private
                # document/page/parser identities from the model transport.
                for fragment in self.context.fragments:
                    if fragment.anchor.evidence_id != value["evidence_id"]:
                        continue
                    start = value.get("span_start")
                    end = value.get("span_end")
                    offset = fragment.anchor.span_start or 0
                    text = fragment.text[
                        max(0, (start or 0) - offset):
                        None if end is None else end - offset
                    ]
                    return {"evidence_id": self.reverse["evidence_id"][value["evidence_id"]],
                            "text": text}
                raise ValueError("binding_reference_outside_context")
            return {key: self._walk(item, key, encode=encode) for key, item in value.items()}
        if isinstance(value, list):
            return [self._walk(item, field, encode=encode) for item in value]
        mapping = self.reverse if encode else self.references
        return mapping.get(field, {}).get(value, value) if isinstance(value, str) else value

    def decode(self, raw):
        decoded = self._walk(raw)
        if self.allowed_bridges is not None:
            for proposal in decoded.get("proposals", []):
                dimensions = [q.get("dimension") for q in proposal.get("scope_qualifiers", [])]
                if len(set(dimensions)) != len(dimensions):
                    raise ValueError("duplicate_scope_dimension")
                if proposal.get("bridge_kind") not in self.allowed_bridges:
                    raise ValueError("bridge_kind_not_in_task_menu")
                if proposal.get("field_binding_id") and proposal["field_binding_id"] not in (
                    self.binding_ids or set()
                ):
                    raise ValueError("field_binding_outside_context")

        if self.repair:
            for review in decoded.get("verifications", []):
                owner = review.get("subject_binding")
                if owner is not None:
                    owner["support"] = [self._subject_proof(q) for q in owner.get("support", [])]
                    owner["local_support"] = [
                        self._subject_proof(q) for q in owner.get("local_support", [])
                    ]

        def walk(value, field=""):
            if isinstance(value, list):
                return [walk(item, field) for item in value]
            if isinstance(value, dict):
                if field in _ENDPOINTS | _SUPPORTS:
                    allowed = {"evidence_id", "text"}
                    if self.repair and field in {"value_quote", "source_unit_quote"}:
                        allowed.add("context_text")
                    if set(value) - allowed:
                        raise ValueError("invalid_atomic_citation_fields")
                    if field not in _PROOFS and not value.get("text"):
                        raise ValueError("precise_endpoint_quote_required")
                    _, text = resolve_fragment_quote(
                        value.get("evidence_id"), value.get("text"), self.context.fragments,
                        fact_required=field in _ENDPOINTS,
                        context_text=value.get("context_text"))
                    return {"evidence_id": value["evidence_id"], "text": text,
                            **({"context_text": value["context_text"]}
                               if "context_text" in value else {})}
                return {key: walk(item, key) for key, item in value.items()}
            return value

        return walk(decoded)

    def _subject_proof(self, quote):
        if not isinstance(quote, dict) or set(quote) - {"evidence_id", "text"}:
            raise ValueError("invalid_atomic_citation_fields")
        _, text = resolve_fragment_quote(
            quote.get("evidence_id"), quote.get("text"), self.context.fragments,
        )
        return {"evidence_id": quote["evidence_id"], "text": text}
