"""Closed atomic citation transport for the shared executor's TaskContext."""

import json
from copy import deepcopy

from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

TASK_CITATION_VERSION = "ontology-task-citations-v1"
_ENDPOINTS = {"object_quote", "value_quote"}
_SUPPORTS = {"type_support", "predicate_support", "subject_support",
             "condition_support", "counterevidence_support"}
_PROOFS = {"type_support", "predicate_support", "subject_support", "counterevidence_support"}
INSTRUCTION = (
    "本次使用原子引用：名称和属性值必须给evidence_id及唯一逐字text；"
    "类型/谓词/主体/反证的整来源证明只给evidence_id，程序回放原文。"
    "条件仍用精确text。禁止坐标、省略号、改写、跨段/跨格拼接。"
    "FactQuote只允许当前target，BindingProof只允许当前可见绑定背景；背景不能"
    "用来提出新对象或值。E编号是原文来源，候选和target的冻结ID不能当原文引用。"
)


class TaskCitationProtocol:
    def __init__(self, context, request, response_type, system):
        self.context = context
        self.references = {
            "evidence_id": {f"E{i + 1}": identity for i, identity in enumerate(dict.fromkeys(
                fragment.anchor.evidence_id for fragment in context.fragments))},
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
        if request["stage"] == "discovery":
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
        self.user = json.dumps(self._walk(request, encode=True), ensure_ascii=False,
                               separators=(",", ":"))

    def _walk(self, value, field="", *, encode=False):
        if isinstance(value, dict):
            return {key: self._walk(item, key, encode=encode) for key, item in value.items()}
        if isinstance(value, list):
            return [self._walk(item, field, encode=encode) for item in value]
        mapping = self.reverse if encode else self.references
        return mapping.get(field, {}).get(value, value) if isinstance(value, str) else value

    def decode(self, raw):
        decoded = self._walk(raw)

        def walk(value, field=""):
            if isinstance(value, list):
                return [walk(item, field) for item in value]
            if isinstance(value, dict):
                if field in _ENDPOINTS | _SUPPORTS:
                    if set(value) - {"evidence_id", "text"}:
                        raise ValueError("invalid_atomic_citation_fields")
                    if field not in _PROOFS and not value.get("text"):
                        raise ValueError("precise_endpoint_quote_required")
                    _, text = resolve_fragment_quote(
                        value.get("evidence_id"), value.get("text"), self.context.fragments,
                        fact_required=field in _ENDPOINTS)
                    return {"evidence_id": value["evidence_id"], "text": text}
                return {key: walk(item, key) for key, item in value.items()}
            return value

        return walk(decoded)
