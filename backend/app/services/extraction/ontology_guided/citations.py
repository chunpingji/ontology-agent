"""Shared atomic source citations and conservative verification projection.

``CitationProtocol`` has the same system/user/schema/decode interface as
``ModelProtocol``. Construct it in a runner's ``_invoke`` after the normal type
verification ontology projection, and use its decoded dict with the existing
response model and fact verifier. It never calls a model or changes source IR.

Quotes identify ONE original evidence unit. A table assertion involving several
cells is a LIST of citations, never a synthetic cross-cell ``context`` string.
"""

from __future__ import annotations

import json
from copy import deepcopy

from app.schemas.evidence import EvidenceAnchor, EvidenceScope
from app.services.extraction.evidence_identity import canonical_json, evidence_hash
from app.services.extraction.evidence_scope import scope_contains
from app.services.extraction.hierarchical_context import model_anchor, model_request
from app.services.extraction.model_protocol import ModelProtocol
from app.services.extraction.ontology_guided.source_citations import (
    SpanProposal,
    resolve_source_anchor,
)
from app.services.extraction.table_records import table_records
from app.services.extraction.verification_context import verification_payload

PROTOCOL_VERSION = "atomic-citations-v8-condition-review"
_SPAN_FIELDS = {
    "mention",
    "value",
    "assertion_spans",
    "conditions",
    "source_unit",
    "boolean_legend",
}
_ATOMIC_FIELDS = {"evidence_id", "text"}
_INSTRUCTION = (
    "\n本次使用原子原文引用协议，覆盖旧的任何坐标和 context 引用说明。每个引用只允许 "
    "evidence_id 和可选 text；禁止输出 start、end、context（即使为 null），"
    "所有最终坐标均由程序回放原文后计算。禁止把多个单元格/段落拼成一个引用。"
    "多个来源必须分别放入 assertion_spans/conditions 引用列表，每项 text 都须与该 "
    "evidence_id 的原文逐字相同。子串引用仅接受该来源允许区域内唯一的逐字匹配；"
    "有多个位置的重复子串保守拒绝，不猜位置，不能用模型坐标消歧。"
    "完整单元引用优先只输出 evidence_id，text 省略或 null，不必复制整格/整段原文；"
    "程序仅回放该字段权限内"
    "唯一连续的完整源片段，不补入权限外文字；同 ID 有多个不连续允许片段时必须"
    "提供唯一逐字 text，否则拒答。若实体名称、身份键或属性值只是复合单元中的子串，"
    "请保留精确子串 text；括号、前缀、多值须按原文选准确子段，不静默规范化。"
    "不要用完整段落代替名称、身份键或单个数值。FactSpan 仅允许事实目标，"
    "BindingSpan 仅允许绑定证据；"
    "subject_evidence 等背景来源不能代替本次事实目标。"
    "引用编号、表头和记录邻接都不证明归属；独立检查竞争主体、单位、图例、否定和条件。"
    "表格的 table_context 按原始网格给出逻辑行列及 column_headers；"
    "必须按 covered_columns 将值与表头对应，不能把同行其他列的 N/A/数值移到当前列。"
    "合并单元格覆盖多个逻辑行列，不代表这些行的主体或属性可以合并。"
    "FactProof/BindingProof 是整来源证明引用，仅输出 evidence_id；服务端回放完整允许"
    "原文，不必重写证明文字。名称、属性值、单位及条件仍按各自的精确子串引用契约。"
    "verify_binding 时，candidate.condition_anchors 是召回阶段提出的待核对条件，"
    "不保证分类正确。condition_reviews 必须按从0开始的 condition_index 逐项独立判定"
    "is_condition 并说明 reason；文档标题、普通列头、主体名称和记录归属背景通常不是"
    "使断言成立受到限制的前提，但其中明示的适用范围、否定和真实条件必须保留。"
    "不能因为条件妨碍肯定结论就移除它；conditions 返回最终识别的真实限制原文。"
)
_REFERENCE_INSTRUCTION = (
    "\n本次是独立跨记录归属复核。reference_verification 将已有原主体构造证据、"
    "当前候选事实原文和当前记录绑定证据分组；这些是待核对来源，不是归属已成立的结论。"
    "supported=true 时，assertion_spans 必须分别引用原主体身份证据和当前记录中"
    "实际建立同一主体身份/归属桥接的原文，同时覆盖当前候选事实。仅引用属性值不足以通过。"
    "比较设备编号等原文身份和竞争主体；同名、同行/邻接、父报告及摘要不能代替身份桥接。"
    "field_groups_to_check 是完整连续键值表单的原文分组提示。局部字段归属须结合"
    "表单中明确的主体角色字段（如项目名称）、当前字段标签、共同章节语义和完整组内"
    "其他主体/限定条件独立判断；不能把这种表单简单当作两个无关孤立段落，也不能"
    "只凭组号或相邻就通过。接受时引用原主体、表单主体角色字段和当前事实的原文。"
    "局部表单归属不授予全局唯一身份，不允许覆盖其他产品、中间体、设备或试验的同名字段。"
    "找不到可回放的双端身份/归属证据时 supported=false；不得根据候选或摘要补造证据。"
)


def _role_values(fragment):
    values = []
    for key in ("purpose", "role", "record_role", "roles"):
        value = fragment.get(key, [])
        values.extend(value if isinstance(value, list) else [value])
    return list(dict.fromkeys(str(value) for value in values if value))


def _table_headers(records, unit):
    return [
        header
        for header in records.metadata([unit])
        if records.is_header(header)
        and header.table_path == unit.table_path
        and records.columns(header) & records.columns(unit)
    ]


def _complete_table_headers(fragments, original_fragments, ir):
    """Retain headers for EVERY displayed column, not just the initial seed.

    Missing source headers may be displayed, but are never new fact targets.
    The caller still intersects all citation permissions with its input envelope.
    """
    records = table_records(ir)
    required = {}
    for fragment in fragments:
        unit = ir.unit(fragment["anchor"]["evidence_id"])
        if unit.table_path:
            required.update(
                (header.evidence_id, header) for header in _table_headers(records, unit)
            )
    present = {fragment["anchor"]["evidence_id"] for fragment in fragments}
    result = list(fragments)
    for identity, header in required.items():
        if identity in present:
            continue
        originals = [f for f in original_fragments if f["anchor"]["evidence_id"] == identity]
        result.extend(
            deepcopy(originals)
            if originals
            else [
                {
                    "anchor": ir.anchor(identity).model_dump(mode="json"),
                    "text": header.text,
                    "purpose": "table_column_header_context",
                    "fact_eligible": False,
                    "binding_permitted": False,
                }
            ]
        )
        present.add(identity)
    return result


def _table_context(fragment, records, ir):
    unit = ir.unit(fragment["anchor"]["evidence_id"])
    if not unit.table_path:
        return
    fragment["table_context"] = {
        "source_cell_id": unit.source_cell_id,
        "table_path": list(unit.table_path),
        "logical_rows": sorted(records.rows(unit, data_only=False)),
        "logical_data_rows": sorted(records.rows(unit)),
        "logical_columns": sorted(records.columns(unit)),
        "is_header": records.is_header(unit),
        "column_headers": [
            {
                "evidence_id": header.evidence_id,
                "logical_rows": sorted(records.rows(header, data_only=False)),
                "logical_columns": sorted(records.columns(header)),
                "covered_columns": sorted(records.columns(header) & records.columns(unit)),
            }
            for header in _table_headers(records, unit)
        ],
    }


def _deduplicate_fragments(fragments, ir):
    """One exact source interval, with all original role/subject assignments."""
    grouped = {}
    for fragment in fragments:
        anchor = EvidenceAnchor.model_validate(fragment["anchor"])
        identity = (anchor.evidence_id, *_bounds(anchor, ir))
        grouped.setdefault(identity, []).append(fragment)
    result = []
    for group in grouped.values():
        fragment = deepcopy(group[0])
        fragment["fact_eligible"] = any(f.get("fact_eligible") for f in group)
        fragment["binding_eligible"] = any(f.get("binding_eligible") for f in group)
        if any("binding_permitted" in f for f in group):
            fragment["binding_permitted"] = fragment["binding_eligible"]
        assignments = {
            evidence_hash(
                {key: value for key, value in source.items() if key not in {"anchor", "text"}}
            ): {key: value for key, value in source.items() if key not in {"anchor", "text"}}
            for source in group
        }
        if len(assignments) > 1:
            fragment["source_roles"] = list(assignments.values())
            fragment["roles"] = list(dict.fromkeys(role for f in group for role in _role_values(f)))
            subjects = {}
            for source in group:
                if source.get("subject_id"):
                    subjects.setdefault(source["subject_id"], []).extend(_role_values(source))
            fragment["subject_roles"] = [
                {"subject_id": subject, "roles": list(dict.fromkeys(roles))}
                for subject, roles in subjects.items()
            ]
            if len(subjects) > 1:
                fragment.pop("subject_id", None)
            if any(f.get("purpose") == "target" and f.get("fact_eligible") for f in group):
                fragment["purpose"] = "target"
        result.append(fragment)
    return result


def _reference_groups(payload, candidate):
    """Source-role hints only; production verification still checks both ends."""

    def anchors(values):
        return [model_anchor(value) for value in values]

    candidate = candidate or {}
    groups = {}
    for fragment in payload["fragments"]:
        if fragment.get("field_group_id"):
            groups.setdefault(fragment["field_group_id"], []).append(
                model_anchor(fragment["anchor"]),
            )
    return {
        "field_groups_to_check": [
            {"source_structure": "consecutive_labelled_fields", "sources": sources}
            for sources in groups.values()
        ],
        "subject_construction_sources": anchors(
            (payload["task"].get("scope") or {}).get("construction_evidence", [])
        ),
        "current_fact_sources": anchors(
            anchor
            for source in candidate.get("provenance", [])
            if source.get("kind") == "document"
            for anchor in source.get("anchors", [])
        ),
        "current_record_binding_sources_to_check": anchors(
            anchor
            for binding in candidate.get("bindings", [])
            for anchor in binding.get("anchors", [])
        ),
        "instruction": _REFERENCE_INSTRUCTION.strip(),
    }


def _anchors(value):
    """Read existing candidate anchors, never interpret free text as a citation."""
    if isinstance(value, dict):
        if "evidence_id" in value and "block_id" in value:
            yield value
        else:
            for key, item in value.items():
                if key not in {"scope", "predicate_definition"}:
                    yield from _anchors(item)
    elif isinstance(value, list):
        for item in value:
            yield from _anchors(item)


def _candidate_ids(value):
    if isinstance(value, dict):
        if value.get("candidate_id"):
            yield value["candidate_id"]
        for key, item in value.items():
            if key not in {"scope", "predicate_definition", "provenance", "bindings"}:
                yield from _candidate_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _candidate_ids(item)


def project_verification_payload(payload, candidate, ir):
    """Keep candidate records and every named competitor, without token truncation.

    For prose, retain all visible prose in the involved sections (including
    uncited denial/condition statements). For tables, retain the complete logical
    records, their column headers/notes, and all competitor records. Explicitly
    tagged counterevidence, units and legends are always retained. Selection is
    structural: a source's polarity or relevance is never guessed from its text.
    """
    result = deepcopy(payload)
    task = result["task"]
    retained_ids = set(_candidate_ids(candidate))
    for key in ("subject", "competing_subjects", "path_root", "dependency_refs"):
        retained_ids.update(_candidate_ids(task.get(key)))
    objects = {
        ref
        for item in [candidate]
        if isinstance(item, dict)
        for ref in _candidate_ids(item.get("object"))
    }
    if isinstance(candidate, dict) and "shared_candidates" in candidate:
        objects.update(_candidate_ids(candidate["shared_candidates"]))
    task["object_candidates"] = [
        ref for ref in task.get("object_candidates", []) if ref["candidate_id"] in objects
    ]
    retained_ids.update(_candidate_ids(task["object_candidates"]))
    result["subjects"] = {
        key: value for key, value in result["subjects"].items() if key in retained_ids
    }
    seeds = [
        *list(_anchors(candidate)),
        *list(_anchors(result["subjects"])),
        *(task.get("scope") or {}).get("construction_evidence", []),
        *(anchor for expansion in (task.get("scope") or {}).get("expansion_history", [])
          for anchor in expansion.get("evidence", [])),
    ]
    if not seeds:
        # A missing structural link is not permission to hide possible refutation.
        result["fragments"] = _complete_table_headers(result["fragments"], payload["fragments"], ir)
        return result
    seed_units = {}
    for raw in seeds:
        anchor = EvidenceAnchor.model_validate(raw)
        ir.resolve(anchor)
        seed_units[anchor.evidence_id] = ir.unit(anchor.evidence_id)
    records = table_records(ir)
    table_units = [unit for unit in seed_units.values() if unit.table_path]
    related_ids = set(seed_units)
    related_ids.update(unit.evidence_id for unit in records.metadata(table_units))
    related_ids.update(unit.evidence_id for unit in records.notes(table_units))
    sections = {unit.section_node_id for unit in seed_units.values()}
    nodes = {node["node_id"]: node for node in ir.nodes}
    ancestors = set(sections)
    for section in list(sections):
        seen = set()
        while section in nodes and section not in seen:
            seen.add(section)
            ancestors.add(section)
            section = nodes[section].get("parent_id")

    def keep(fragment):
        unit = ir.unit(fragment["anchor"]["evidence_id"])
        role = " ".join(_role_values(fragment)).lower()
        if any(
            word in role
            for word in (
                "counter",
                "negation",
                "legend",
                "unit",
                "condition",
                "reference",
                "parent",
                "scope_construction",
            )
        ):
            return True
        return (
            unit.evidence_id in related_ids
            or (not unit.table_path and unit.section_node_id in sections)
            or (unit.kind == "heading" and unit.section_node_id in ancestors)
            or (
                fragment.get("purpose") == "subject_evidence"
                and fragment.get("subject_id") in retained_ids
            )
        )

    result["fragments"] = _complete_table_headers(
        [fragment for fragment in result["fragments"] if keep(fragment)],
        payload["fragments"],
        ir,
    )
    return result


def _bounds(anchor, ir):
    length = len(ir.unit(anchor.evidence_id).text)
    return (anchor.span_start or 0, anchor.span_end if anchor.span_end is not None else length)


def _contains(outer, inner, ir):
    left, right = _bounds(outer, ir)
    start, end = _bounds(inner, ir)
    return outer.evidence_id == inner.evidence_id and left <= start < end <= right


def _intersections(allowed, visible, ir):
    result = {}
    for region in allowed:
        ir.resolve(region)
        left, right = _bounds(region, ir)
        for anchor in visible:
            if anchor.evidence_id != region.evidence_id:
                continue
            start, end = _bounds(anchor, ir)
            start, end = max(start, left), min(end, right)
            if start < end:
                result[(anchor.evidence_id, start, end)] = ir.anchor(anchor.evidence_id, start, end)
    return list(result.values())


def _canonical_enums(schema, references):
    aliases = {key: value for mapping in references.values() for key, value in mapping.items()}

    def walk(value):
        if isinstance(value, dict):
            return {
                key: [aliases.get(item, item) for item in item_value]
                if key == "enum"
                else walk(item_value)
                for key, item_value in value.items()
            }
        return [walk(item) for item in value] if isinstance(value, list) else value

    return walk(schema)


def _citation_schema(schema, references, envelope, stage, task_kind, ir):
    """Separate source permissions in generation, not only after generation."""
    schema = deepcopy(schema)
    definitions = schema.get("$defs", {})
    if "SpanProposal" not in definitions:
        return schema

    def binding_refs(value):
        if isinstance(value, dict):
            return {
                key: "#/$defs/BindingSpan"
                if key == "$ref" and item == "#/$defs/SpanProposal"
                else binding_refs(item)
                for key, item in value.items()
            }
        return [binding_refs(item) for item in value] if isinstance(value, list) else value

    schema = binding_refs(schema)
    definitions = schema["$defs"]
    definitions.pop("SpanProposal")
    atomic = deepcopy(SpanProposal.model_json_schema())
    for field in ("start", "end", "context"):
        atomic["properties"].pop(field, None)
    atomic["properties"]["text"] = {
        "anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
        "default": None,
    }
    atomic["required"] = [key for key in atomic["required"] if key != "text"]
    atomic["additionalProperties"] = False
    for name, regions in (
        ("FactSpan", envelope.allowed_fact_regions),
        ("BindingSpan", envelope.allowed_binding_regions),
    ):
        definition = deepcopy(atomic)
        definition["title"] = name
        permitted = {anchor.evidence_id for anchor in regions}
        definition["properties"]["evidence_id"]["enum"] = [
            alias for alias, canonical in references["evidence"].items() if canonical in permitted
        ]
        definitions[name] = definition
    # A complete, uniquely permitted source needs no model transcription for a
    # proof. Keep precise quotations for values/names/units and fragmented domains.
    proofs = {}
    for name, regions in (("FactSpan", envelope.allowed_fact_regions),
                          ("BindingSpan", envelope.allowed_binding_regions)):
        try:
            for identity in {region.evidence_id for region in regions}:
                left, right = _whole_permitted_interval(identity, regions, ir)
                if not any(region.evidence_id == identity and _bounds(region, ir)[0] <= left
                           and right <= _bounds(region, ir)[1] for region in regions):
                    raise ValueError("non_contiguous_source_permission")
        except ValueError:
            continue
        proof_name = name.replace("Span", "Proof")
        proof = deepcopy(definitions[name])
        proof["title"] = proof_name
        proof["properties"].pop("text")
        definitions[proof_name] = proof
        proofs[name] = proof_name
    if stage == "recall":
        entity = definitions.get("EntityProposal", {}).get("properties", {})
        if "mention" in entity:
            entity["mention"] = {"$ref": "#/$defs/FactSpan"}
        assertion = definitions.get("AssertionProposal", {}).get("properties", {})
        if task_kind == "property" and "value" in assertion:
            assertion["value"] = {"$ref": "#/$defs/FactSpan"}
        if task_kind == "relationship" and "assertion_spans" in assertion:
            assertion["assertion_spans"]["items"] = {"$ref": "#/$defs/FactSpan"}
    for properties in [schema.get("properties", {}),
                       definitions.get("EntityProposal", {}).get("properties", {}),
                       definitions.get("AssertionProposal", {}).get("properties", {})]:
        items = properties.get("assertion_spans", {}).get("items", {})
        name = items.get("$ref", "").rsplit("/", 1)[-1]
        if name in proofs:
            items["$ref"] = "#/$defs/" + proofs[name]
    return schema


def _whole_permitted_interval(evidence_id, allowed, ir):
    intervals = sorted(
        {_bounds(anchor, ir) for anchor in allowed if anchor.evidence_id == evidence_id}
    )
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if len(merged) != 1:
        raise ValueError("ambiguous_allowed_source_fragments")
    return merged[0]


class CitationProtocol:
    """Request adapter: exact atomic citations plus the production closed IDs.

    ``decode`` returns the ORIGINAL response shape with full evidence IDs and
    exact source coordinates. Callers still run their normal response validation
    and semantic verifier. ``envelope`` is a detached, auditable projection; its
    allowed regions can only be intersections of the original allowed regions.
    """

    def __init__(
        self,
        system,
        envelope,
        response_type,
        *,
        ir,
        stage,
        candidate=None,
        ontology_schema=None,
        compact_identifiers=True,
        project_verification=True,
        citation_feedback=None,
    ):
        if envelope.serialized_input is None:
            raise ValueError(envelope.reason or "incomplete_context")
        self.ir, self.stage, self.response_type = ir, stage, response_type
        self.compact_identifiers = compact_identifiers
        payload = json.loads(envelope.serialized_input)
        self.task_kind = payload["task"]["task_kind"]
        original_count = len(payload["fragments"])
        if stage == "verify_entity_types" and ontology_schema is not None:
            payload = verification_payload(
                payload,
                candidate["proposed_entities"],
                ontology_schema,
                candidate.get("competing_class_iris", ()),
            )
        if stage != "recall":
            payload["task"].get("predicate_definition", {}).pop("discovery_relation", None)
            if project_verification:
                payload = project_verification_payload(payload, candidate, ir)
        if stage == "recall" or not project_verification:
            payload["fragments"] = _complete_table_headers(
                payload["fragments"],
                payload["fragments"],
                ir,
            )
        scope_data = payload["task"].get("scope")
        scope = EvidenceScope.model_validate(scope_data) if scope_data else None
        binding_visible, fact_visible, fragments = [], [], []
        for original in payload["fragments"]:
            fragment = deepcopy(original)
            anchor = EvidenceAnchor.model_validate(fragment["anchor"])
            if ir.resolve(anchor) != fragment["text"]:
                raise ValueError("citation_fragment_source_mismatch")
            fact_allowed = any(
                _contains(region, anchor, ir) for region in envelope.allowed_fact_regions
            )
            if fragment.get("fact_eligible") and not fact_allowed:
                raise ValueError("citation_fact_permission_mismatch")
            binding_allowed = fragment.get("binding_permitted") is not False and any(
                _contains(region, anchor, ir) for region in envelope.allowed_binding_regions
            )
            fragment.update(
                binding_eligible=binding_allowed,
                document_role=ir.document_role,
                scope_id=scope.scope_id if scope else None,
                in_subject_scope=scope_contains(scope, anchor, ir) if scope else None,
            )
            fragments.append(fragment)
            if fragment.get("binding_permitted") is not False:
                binding_visible.append(anchor)
            if fragment.get("fact_eligible"):
                fact_visible.append(anchor)
        fragments = _deduplicate_fragments(fragments, ir)
        records = table_records(ir)
        for fragment in fragments:
            _table_context(fragment, records, ir)
        payload["fragments"] = fragments
        if stage == "verify_reference":
            payload["reference_verification"] = _reference_groups(payload, candidate)
        self.envelope = envelope.model_copy(
            update={
                "serialized_input": canonical_json(payload),
                "context_hash": evidence_hash([PROTOCOL_VERSION, payload]),
                "fragments": fragments,
                "allowed_fact_regions": _intersections(
                    envelope.allowed_fact_regions, fact_visible, ir
                ),
                "allowed_binding_regions": _intersections(
                    envelope.allowed_binding_regions, binding_visible, ir
                ),
            },
            deep=True,
        )
        self.projection = {
            "protocol_version": PROTOCOL_VERSION,
            "fragments_before": original_count,
            "fragments_after": len(fragments),
            "source_ir_unchanged": True,
            "selection": "candidate_records_competitors_and_structural_context"
            if stage != "recall" and project_verification
            else "all_input_fragments",
        }
        schema = response_type.model_json_schema()
        user = model_request(payload, stage, candidate)
        if stage == "verify_reference":
            request = json.loads(user)
            context = json.loads(request["context"])
            context["reference_verification"] = payload["reference_verification"]
            request["context"] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            user = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        self._wire = ModelProtocol(system, user, schema)
        wire_schema = _citation_schema(
            self._wire.schema,
            self._wire.references,
            self.envelope,
            stage,
            self.task_kind,
            ir,
        )
        if stage == "verify_binding" and "condition_reviews" in wire_schema.get("properties", {}):
            count = len((candidate or {}).get("condition_anchors", []))
            field = wire_schema["properties"]["condition_reviews"]
            field.update(minItems=count, maxItems=count)
            if count:
                ModelProtocol._require(wire_schema, "condition_reviews")
                wire_schema["$defs"]["ConditionReview"]["properties"]["condition_index"]["enum"] = (
                    list(range(count))
                )
        self.references = deepcopy(self._wire.references)
        self.schema = (
            wire_schema if compact_identifiers else _canonical_enums(wire_schema, self.references)
        )
        self._wire.schema = wire_schema
        self.system = (self._wire.system if compact_identifiers else system) + _INSTRUCTION
        if stage == "verify_reference":
            self.system += _REFERENCE_INSTRUCTION
        request = json.loads(self._wire.user if compact_identifiers else user)
        request["output_contract"] = self.schema
        if citation_feedback:
            request["citation_feedback"] = (
                self._wire._walk(citation_feedback, encode=True)
                if compact_identifiers else deepcopy(citation_feedback)
            )
        self.user = json.dumps(request, ensure_ascii=False, separators=(",", ":"))

    def _span(self, value, path):
        if not isinstance(value, dict) or set(value) - _ATOMIC_FIELDS:
            raise ValueError("non_atomic_source_citation")
        replay_text = value.get("text") is None
        # The model never supplies coordinates, even when they would be correct.
        # Validate the source identity and optional quote before replaying anything.
        proposal = SpanProposal.model_validate(
            {**value, "text": "source-replay"} if replay_text else value,
            strict=True,
        )
        allowed = self.envelope.allowed_binding_regions
        if self.stage == "recall" and (
            (path[-1] in {"mention", "value"} and "identifier" not in path)
            or (self.task_kind == "relationship" and "assertion_spans" in path)
        ):
            allowed = self.envelope.allowed_fact_regions
        if proposal.evidence_id not in {region.evidence_id for region in allowed}:
            raise ValueError("unknown_or_disallowed_citation_source")
        if replay_text:
            start, end = _whole_permitted_interval(proposal.evidence_id, allowed, self.ir)
            source = self.ir.anchor(proposal.evidence_id, start, end)
            proposal = proposal.model_copy(
                update={
                    "start": start,
                    "end": end,
                    "text": self.ir.resolve(source),
                }
            )
        # Keep the production containment check even for an ID-only replay.
        # Contiguous unions that no original region individually authorizes fail closed.
        anchor = resolve_source_anchor(proposal, self.ir, allowed)
        return {
            "evidence_id": anchor.evidence_id,
            "start": anchor.span_start,
            "end": anchor.span_end,
            "text": proposal.text,
        }

    def decode(self, raw, *, on_proposal_error=None):
        """Reject a whole invalid proposal; optionally retain valid recall siblings.

        Verification remains atomic: no support span can be dropped from a proof.
        The runner must persist reported errors and mark the task incomplete.
        """
        value = self._wire.decode(raw) if self.compact_identifiers else deepcopy(raw)
        if not self.compact_identifiers:
            self._wire._check_required(value, self.schema)
            proposal = self.schema.get("$defs", {}).get("AssertionProposal")
            if proposal:
                for item in value.get("assertions", []):
                    self._wire._check_required(item, proposal)

        def walk(item, path=()):
            if isinstance(item, list):
                values = []
                for index, value in enumerate(item):
                    try:
                        values.append(walk(value, path))
                    except ValueError as exc:
                        if (on_proposal_error is None or self.stage != "recall"
                            or path not in {("entities",), ("assertions",)}):
                            raise
                        details = dict(getattr(exc, "quote_details", {}))
                        field = details.get("field_path", path[0])
                        details["field_path"] = field.replace(
                            path[0], f"{path[0]}[{index}]", 1,
                        )
                        exc.quote_details = details
                        on_proposal_error(exc)
                return values
            if isinstance(item, dict):
                if (
                    path
                    and path[-1] in _SPAN_FIELDS
                    and ("evidence_id" in item or path[-1] != "value")
                ):
                    try:
                        return self._span(item, path)
                    except ValueError as exc:
                        text = item.get("text") or ""
                        exc.quote_details = {
                            "evidence_id": item.get("evidence_id"),
                            "quote": text[:240], "quote_truncated": len(text) > 240,
                            **getattr(exc, "quote_details", {}),
                            "field_path": ".".join(path),
                        }
                        raise
                return {key: walk(value, (*path, key)) for key, value in item.items()}
            return item

        value = walk(value)
        self.response_type.model_validate(value, strict=True)
        return value
