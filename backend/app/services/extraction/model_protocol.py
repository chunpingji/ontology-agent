"""Closed, request-local reference transport; never a semantic identity resolver."""

import json
from copy import deepcopy

from app.services.extraction.evidence_identity import canonical_json

PROTOCOL_VERSION = "closed-model-references-v5-context-quotes"
# Transport changes do not invalidate validated semantic checkpoints or repack tasks.
TRANSPORT_VERSION = "stable-prefix-local-verification-v1"
_CANDIDATE_FIELDS = {
    "candidate_id", "subject_candidate_id", "object_candidate_id", "subject_id",
    "supported_subject_ids", "shared_subject_ids",
}


class ModelProtocol:
    def __init__(self, system: str, user: str, schema: dict, *, planning=False):
        request = json.loads(user)
        context = json.loads(request["context"])
        definitions = context["task"].get("predicate_definition", {}).get("classes", {})
        evidence = {f["anchor"]["evidence_id"] for f in context["fragments"]}
        candidates = set(context["subjects"])

        def collect(value, field=""):
            if isinstance(value, str) and field in _CANDIDATE_FIELDS:
                candidates.add(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    collect(item, key)
            elif isinstance(value, list):
                for item in value:
                    collect(item, field)

        collect(context)
        collect(request.get("candidate"))
        self.references = {
            "evidence": {f"e{i}": key for i, key in enumerate(sorted(evidence))},
            "class": {f"t{i}": key for i, key in enumerate(sorted(definitions))},
            "candidate": {f"c{i}": key for i, key in enumerate(sorted(candidates))},
        }
        self._encode = {
            kind: {value: alias for alias, value in mapping.items()}
            for kind, mapping in self.references.items()
        }
        context = self._walk(context, encode=True)
        request["context"] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        request["candidate"] = self._walk(request.get("candidate"), encode=True)
        self.user = canonical_json(request)
        self.schema = planning_schema(schema) if planning else deepcopy(schema)
        definitions = self.schema.get("$defs", {})
        if "SpanProposal" in definitions:
            definitions["SpanProposal"]["properties"]["evidence_id"]["enum"] = list(
                self.references["evidence"]
            )
            # Models can quote accurately but are unreliable at counting Unicode
            # offsets. Select the exact-quote protocol at the decoder, before
            # generation; ambiguous quotes still fail closed in _anchor.
            for coordinate in ("start", "end"):
                definitions["SpanProposal"]["properties"][coordinate] = {"type": "null"}
        if "EntityProposal" in definitions:
            definitions["EntityProposal"]["properties"]["class_iri"]["enum"] = list(
                self.references["class"]
            )
        if "EntityTypeDecision" in definitions:
            proposed = (request.get("candidate") or {}).get("proposed_entities", [])
            definitions["EntityTypeDecision"]["properties"]["candidate_id"]["enum"] = [
                item["candidate_id"] for item in proposed
            ]
            self.schema["properties"]["decisions"].update(
                minItems=len(proposed), maxItems=len(proposed),
            )
        task = context["task"]
        kind = task.get("task_kind")
        objects = [ref["candidate_id"] for ref in task.get("object_candidates", [])]
        if "AssertionProposal" in definitions:
            proposal = definitions["AssertionProposal"]
            fields = proposal["properties"]
            self._require(self.schema, "assertions")
            self._require(proposal, "assertion_status")
            if kind == "relationship":
                fields["object_candidate_id"] = self._object_reference(objects)
                fields["value"] = {"type": "null"}
                fields["assertion_spans"]["minItems"] = 1
                self._require(proposal, "object_candidate_id", "assertion_spans")
                if not objects:
                    self.schema["properties"]["assertions"]["maxItems"] = 0
            elif kind == "property":
                fields["value"] = {"$ref": "#/$defs/SpanProposal"}
                fields["object_candidate_id"] = {"type": "null"}
                self._require(proposal, "value")
        properties = self.schema.get("properties", {})
        if "subject_candidate_id" in properties and task.get("subject"):
            properties["subject_candidate_id"]["enum"] = [task["subject"]["candidate_id"]]
            # A binding verdict must identify the source it checked, including
            # when that source does not support the proposal. Never accept a
            # bare supported=true with no independently replayable quotation.
            properties["assertion_spans"]["minItems"] = 1
            self._require(self.schema, "assertion_spans")
            if "method" in properties and not any(
                f.get("fact_eligible") and f["anchor"].get("table_path")
                for f in context["fragments"]
            ):
                properties["method"]["enum"] = [
                    method for method in properties["method"]["enum"]
                    if method != "table_record"
                ]
            if "record_mapping" in properties:
                # Structural record IDs, like character offsets, come from
                # replayed anchors. Guessing the heading's section here can
                # incorrectly invalidate a cross-section property binding.
                properties["record_mapping"] = {
                    "type": "object", "properties": {}, "additionalProperties": False,
                }
        if "object_candidate_id" in properties:
            proposed = ((request.get("candidate") or {}).get("object") or {}).get("candidate_id")
            if kind == "relationship":
                properties["object_candidate_id"] = self._object_reference(
                    [proposed] if proposed in objects else objects
                )
                self._require(self.schema, "object_candidate_id")
            else:
                properties["object_candidate_id"] = {"type": "null"}
        if "supported_subject_ids" in properties:
            properties["supported_subject_ids"]["items"]["enum"] = list(
                self.references["candidate"]
            )
        self.system = (
            system + "\n引用字段只能返回本次输入中登记的 e/t/c 短编号，"
            "不能返回完整 IRI 或自造编号。"
            "编号仅代表身份，不代表实体存在或绑定成立；原文 text 不作替换。输出契约：\n"
            + canonical_json(self.schema)
        )
        if not planning:
            self.system = (
                system + "\n引用字段只能返回本次输入中登记的 e/t/c 短编号，"
                "不能返回完整 IRI 或自造编号。编号仅代表身份，不代表实体存在或绑定成立；"
                "原文 text 不作替换。具体输出契约位于 user 的 output_contract 字段。"
                "实体召回逐提案表达 supported 和简短 reason；不支持只影响自身，"
                "仍须返回其余有证据的提案。身份唯一性与类型支持分别判断。"
                "复核时根据 property_roles、relationship_roles 及子类定义区分实体、"
                "属性值和关联对象；仅有该类某个属性的值，不能据此认定为该类实体。"
                "refusal_reason 仅说明本次整体无法处理，不能代替逐项结论。"
            )
            request["output_contract"] = self.schema
            self.user = json.dumps({key: request[key] for key in (
                "stage", "context", "candidate", "focus", "output_contract",
            ) if key in request}, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _object_reference(values):
        return {"type": "string", "enum": values} if values else {"type": "null"}

    @staticmethod
    def _require(schema, *fields):
        schema["required"] = list(dict.fromkeys([*schema.get("required", []), *fields]))

    def _walk(self, value, field="", *, encode=False):
        kind = (
            "evidence" if field == "evidence_id" else
            "class" if field in {"class_iri", "parents"} else
            "candidate" if field in _CANDIDATE_FIELDS else None
        )
        if isinstance(value, str) and kind:
            mapping = self._encode[kind] if encode else self.references[kind]
            if value in mapping:
                return mapping[value]
            if encode:
                return value  # e.g. an ancestor class outside this task's output menu
            raise ValueError("unknown_model_reference")
        if isinstance(value, list):
            return [self._walk(item, field, encode=encode) for item in value]
        if isinstance(value, dict):
            if encode and field == "classes":
                return {
                    self._encode["class"][key]: {
                        **self._walk({k: v for k, v in item.items() if k != "iri"}, encode=True),
                        "name": key.rsplit("/", 1)[-1].rsplit("#", 1)[-1],
                    }
                    for key, item in value.items()
                }
            return {
                self._encode["candidate"][key] if encode and field == "subjects" else key:
                self._walk(item, key, encode=encode)
                for key, item in value.items()
            }
        return value

    def decode(self, response):
        # Decoder constraints help generation, but providers/callbacks may still
        # omit fields. Never let Pydantic defaults turn a missing model decision
        # (especially polarity) into an affirmative assertion.
        self._check_required(response, self.schema)
        proposal = self.schema.get("$defs", {}).get("AssertionProposal")
        if proposal:
            assertions = response.get("assertions")
            if not isinstance(assertions, list):
                raise ValueError("invalid_model_response")
            for assertion in assertions:
                self._check_required(assertion, proposal)
        return self._walk(response)

    @staticmethod
    def _check_required(value, schema):
        if not isinstance(value, dict) or any(
            key not in value for key in schema.get("required", [])
        ):
            raise ValueError("invalid_model_response")


def planning_schema(schema):
    """Freeze pre-P1 packing costs so resumed tasks keep their original identity."""
    schema = deepcopy(schema)
    proposal = schema.get("$defs", {}).get("EntityProposal", {})
    for key in ("supported", "reason"):
        proposal.get("properties", {}).pop(key, None)
    return schema
