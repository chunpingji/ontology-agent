# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema==4.23.0"]
# ///
"""Check 027 design artifacts; this does not execute the extraction engine or Qwen.

Run with a Python 3.11+ environment that already has jsonschema 4.23.0 installed:
    python specs/027-ontology-extraction-engine-v2/check_design.py --self-test

The PEP 723 metadata describes this standalone design tool's dependency. It does
not change application dependencies or download anything when run with Python.
All mutations used by --self-test are in memory; repository files are not edited.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.exceptions import NoSuchResource
    from referencing.jsonschema import DRAFT202012
except ImportError:
    sys.exit(
        "Missing design-check dependency: jsonschema==4.23.0. Use an existing Python "
        "3.11+ environment with that version; see this script's PEP 723 metadata. "
        "No application dependencies have been changed."
    )


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
SPEC_PATH = BASE.relative_to(ROOT).as_posix()
EXAMPLES = f"{SPEC_PATH}/contracts/examples.json"
STAGES = f"{SPEC_PATH}/contracts/stage-schemas.json"
TOOLS = f"{SPEC_PATH}/contracts/tools.json"
CONTEXT = f"{SPEC_PATH}/contracts/context-schemas.json"
DESIGN = "docs/文档抽取引擎2.0设计方案.md"


class DesignError(Exception):
    """A failed artifact invariant with a source location and stable check code."""


def require(condition: bool, location: str, code: str, message: str) -> None:
    if not condition:
        raise DesignError(f"[{code}] {location}: {message}")


def pointer(parts: Any) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def canonical_hash(value: Any) -> str:
    # JSON-only specialization of extraction/evidence_identity.canonical_json.
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decode_json(value: str, location: str) -> Any:
    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            require(key not in result, location, "DUPLICATE_KEY", f"duplicate JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(token: str) -> Any:
        raise DesignError(f"[JSON_PARSE] {location}: {token} is not a standard JSON value")

    try:
        return json.loads(value, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError) as error:
        raise DesignError(f"[JSON_PARSE] {location}: {error}") from error


@dataclass
class Artifacts:
    json_files: dict[str, Any]
    markdown: dict[str, str]

    @classmethod
    def read(cls) -> Artifacts:
        json_files = {
            path.relative_to(ROOT).as_posix(): decode_json(
                path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()
            )
            for path in sorted((BASE / "contracts").glob("*.json"))
        }
        paths = sorted(BASE.rglob("*.md")) + [
            ROOT / DESIGN,
            ROOT / "backend/app/evaluation/README.md",
        ]
        markdown = {
            path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8") for path in paths
        }
        return cls(json_files, markdown)


class DesignChecker:
    def __init__(self, artifacts: Artifacts):
        self.artifacts = artifacts
        self.examples = artifacts.json_files[EXAMPLES]
        self.stages = artifacts.json_files[STAGES]
        self.tools = artifacts.json_files[TOOLS]
        self.context_schemas = artifacts.json_files[CONTEXT]
        self.schemas = {tool["name"]: tool["parameters"] for tool in self.tools}
        self.counts: Counter[str] = Counter()
        self.registry = Registry(retrieve=self.reject_external_resource).with_resources(
            [
                (
                    "context-schemas.json",
                    Resource.from_contents(self.context_schemas, default_specification=DRAFT202012),
                ),
                (
                    "urn:ontology-tool-extraction:discovery",
                    Resource.from_contents(
                        self.stages["discovery"], default_specification=DRAFT202012
                    ),
                ),
            ]
        )

    @staticmethod
    def reject_external_resource(uri: str) -> Any:
        raise NoSuchResource(ref=uri)

    def schema_value(self, schema: Any, value: Any, location: str) -> None:
        try:
            errors = sorted(
                Draft202012Validator(schema, registry=self.registry).iter_errors(value),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
        except Exception as error:
            raise DesignError(f"[SCHEMA_REFERENCE] {location}: {error}") from error
        if errors:
            error = errors[0]
            raise DesignError(
                f"[JSON_SCHEMA] {location}{pointer(error.absolute_path)}: {error.message}"
            )

    def closed_objects(self, schema: Any, location: str) -> None:
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                if location == f"{CONTEXT}#/verification_input/properties/local_ref_map":
                    require(
                        schema.get("additionalProperties")
                        == {"$ref": "context-schemas.json#/$defs/VersionedRef"},
                        location,
                        "REFERENCE_MAP",
                        "dynamic local IDs may only map to exact VersionedRef values",
                    )
                    self.counts["typed_reference_maps"] += 1
                    return
                properties = schema.get("properties", {})
                required = schema.get("required", [])
                # Persisted v1 claims retain their exact pre-extension hash. Only
                # these two capability-gated fields may be absent in old claims;
                # compile_stage_schema requires them for new reference-enabled runs.
                optional = {
                    f"{STAGES}#/discovery": {"reference_bindings"},
                    f"{STAGES}#/discovery/$defs/RelationProposal": {"source_assertion"},
                }.get(location, set())
                require(
                    schema.get("additionalProperties") is False,
                    location,
                    "CLOSED_OBJECT",
                    "object schema must reject additional properties",
                )
                require(
                    set(required) == set(properties) - optional
                    and optional <= set(properties) and len(required) == len(set(required)),
                    location,
                    "REQUIRED_FIELDS",
                    "all properties must be required exactly once; nullable values stay explicit",
                )
                self.counts["closed_object_schemas"] += 1
            for key, child in schema.items():
                self.closed_objects(child, f"{location}/{key}")
        elif isinstance(schema, list):
            for index, child in enumerate(schema):
                self.closed_objects(child, f"{location}/{index}")

    def check_schemas(self) -> None:
        require(len(self.schemas) == len(self.tools), TOOLS, "TOOL_NAMES", "duplicate tool names")
        require(
            len(self.tools) == 11,
            TOOLS,
            "TOOL_SCOPE",
            "the approved design contains eleven standard functions",
        )
        for index, tool in enumerate(self.tools):
            require(
                set(tool) == {"type", "name", "description", "parameters", "strict"}
                and tool["type"] == "function"
                and tool["strict"] is False,
                f"{TOOLS}#/{index}",
                "TOOL_WIRE",
                "baseline Responses tools must be flat function definitions with strict=false",
            )
        require(
            set(self.examples["tool_arguments"]) == set(self.schemas),
            f"{EXAMPLES}#/tool_arguments",
            "TOOL_EXAMPLES",
            "each registered tool needs exactly one valid argument example",
        )
        schemas = [(name, schema, "tool_arguments") for name, schema in self.schemas.items()]
        schemas += [(stage, self.stages[stage], "") for stage in ("discovery", "verification")]
        for name, schema, parent in schemas:
            location = f"{TOOLS}#/{name}" if parent else f"{STAGES}#/{name}"
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as error:
                raise DesignError(f"[SCHEMA_DEFINITION] {location}: {error}") from error
            self.closed_objects(schema, location)
            value = self.examples[parent][name] if parent else self.examples[name]
            value_path = f"{EXAMPLES}#/{parent}/{name}" if parent else f"{EXAMPLES}#/{name}"
            self.schema_value(schema, value, value_path)
            self.counts["valid_payloads"] += 1
            required = schema["required"][0]
            missing = copy.deepcopy(value)
            del missing[required]
            extra = {**copy.deepcopy(value), "unpermitted_field": True}
            wrong = {**copy.deepcopy(value), required: 42}
            for label, mutation in (("missing", missing), ("extra", extra), ("type", wrong)):
                require(
                    not Draft202012Validator(schema).is_valid(mutation),
                    value_path,
                    "NEGATIVE_SCHEMA",
                    f"schema unexpectedly accepts {label} mutation of {required}",
                )
                self.counts["rejected_invalid_payloads"] += 1
        self.counts["json_files"] = len(self.artifacts.json_files)
        self.counts["tool_schemas"] = len(self.tools)
        self.counts["stage_schemas"] = len(schemas) - len(self.tools)
        require(
            self.context_schemas["protocol_version"] == self.stages["protocol_version"],
            CONTEXT,
            "PROTOCOL",
            "control inputs must use the same frozen extraction protocol",
        )
        for name in (
            "verification_input",
            "context_authorization",
            "tool_protocol_error_observation",
        ):
            Draft202012Validator.check_schema(self.context_schemas[name])
            self.closed_objects(self.context_schemas[name], f"{CONTEXT}#/{name}")
            self.counts["control_input_schemas"] += 1
        self.closed_objects(self.context_schemas["$defs"], f"{CONTEXT}#/$defs")

    @staticmethod
    def semantic_content(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Explicit claim identity projection from data-model §4, excluding added proof quotes."""
        if kind in {"entity", "reference_binding"}:
            return payload
        if kind == "external_link":
            return {
                key: payload[key] for key in ("local_id", "subject_id", "external_candidate_id")
            }
        result = {
            key: payload[key]
            for key in ("local_id", "subject_id", "predicate_iri", "bridge_kind", "bridge_ref_ids")
        }
        qualifiers = payload["qualifiers"]
        result["qualifiers"] = {
            "polarity": qualifiers["polarity"],
            "modality": qualifiers["modality"],
            "condition_texts": [quote["text"] for quote in qualifiers["condition_support"]],
            "scope_qualifiers": [
                {"predicate_iri": item["predicate_iri"], "text": item["quote"]["text"]}
                for item in qualifiers["scope_qualifiers"]
            ],
        }
        if kind == "property":
            result["raw_value"] = payload["value_quote"]["text"]
            result["source_unit_texts"] = sorted(
                {quote["text"] for quote in payload["unit_support"]}
            )
        else:
            result.update(object_ids=payload["object_ids"], selection=payload["selection"])
            if "source_assertion" in payload:
                result["source_assertion"] = payload["source_assertion"]
        return result

    def check_verification_input(self) -> None:
        exchange = self.examples["verification_exchange"]
        value = exchange["verification_input"]
        location = f"{EXAMPLES}#/verification_exchange/verification_input"
        self.schema_value(self.context_schemas["verification_input"], value, location)
        targets = {target["target_id"]: target for target in value["targets"]}
        require(
            len(value["targets"]) == len(targets),
            location,
            "VERIFICATION_CONTENT",
            "every target must occur exactly once with its frozen claim payload",
        )
        collections = {
            "entity": "entities",
            "reference_binding": "reference_bindings",
            "property": "properties",
            "relation": "relations",
            "external_link": "external_links",
        }
        expected_claims = {
            (kind, proposal["local_id"])
            for kind, collection in collections.items()
            for proposal in self.examples["discovery"].get(collection, [])
        }
        actual_claims = [
            (target["target_kind"], target["payload"]["local_id"])
            for target in value["targets"]
        ]
        require(
            len(actual_claims) == len(set(actual_claims))
            and set(actual_claims) == expected_claims,
            location + "/targets",
            "VERIFICATION_COVERAGE",
            "this fixture rejects no frozen claims; each discovery proposal needs one target",
        )

        def ref_key(ref: dict[str, Any]) -> tuple[str, int]:
            return ref["id"], ref["revision"]

        entities = {
            ref_key(item["claim_ref"]): item
            for item in value["targets"]
            if item["target_kind"] == "entity"
        }
        context_entities = {
            ref_key(item["entity_ref"]): item for item in value["entity_dependencies"]
        }
        require(
            len(context_entities) == len(value["entity_dependencies"])
            and not entities.keys() & context_entities.keys(),
            location + "/entity_dependencies",
            "REFERENCE_CLOSURE",
            "context dependencies must be unique and exclude entities already present in targets",
        )
        entities.update(context_entities)
        for index, entity in enumerate(value["entity_dependencies"]):
            if entity["grounding_kind"] == "document_root":
                require(
                    entity["proposal"] is None and bool(entity["root_origin"]),
                    f"{location}/entity_dependencies/{index}",
                    "REFERENCE_CLOSURE",
                    "document root needs explicit configured origin, not a synthesized proposal",
                )
                continue
            proposal = entity["proposal"]
            require(
                proposal is not None
                and proposal["class_iri"] == entity["class_iri"]
                and entity["entity_ref"] in value["local_ref_map"].values(),
                f"{location}/entity_dependencies/{index}",
                "REFERENCE_CLOSURE",
                "registered context entity must include its frozen proposal and exact reference",
            )
        for index, claim in enumerate(value["targets"]):
            claim_path = f"{location}/targets/{index}"
            kind, payload = claim["target_kind"], claim["payload"]
            endpoint_ids = []
            if kind == "reference_binding":
                endpoint_ids.extend([payload["source_id"], payload["target_id"]])
            if kind in ("property", "relation", "external_link"):
                endpoint_ids.append(payload["subject_id"])
            if kind == "relation":
                endpoint_ids.extend(payload["object_ids"])
            dependency_refs = {ref_key(ref) for ref in claim["dependency_refs"]}
            for local_id in endpoint_ids:
                ref = value["local_ref_map"].get(local_id)
                require(
                    ref is not None
                    and ref_key(ref) in entities
                    and ref_key(ref) in dependency_refs,
                    f"{claim_path}/payload/{local_id}",
                    "REFERENCE_CLOSURE",
                    "endpoint must resolve to an entity target or registered context entity",
                )
            expected_hash = canonical_hash(
                {
                    "target_kind": kind,
                    "semantic_content": self.semantic_content(kind, payload),
                    "scope": claim["scope"],
                    "dependency_refs": claim["dependency_refs"],
                }
            )
            require(
                claim["content_hash"] == expected_hash,
                f"{claim_path}/content_hash",
                "CLAIM_HASH",
                "claim hash does not cover semantic content, scope and dependencies",
            )
            require(
                payload in self.examples["discovery"][collections[kind]]
                and value["local_ref_map"].get(payload["local_id"]) == claim["claim_ref"],
                claim_path,
                "FROZEN_PAYLOAD",
                "verification payload must resolve to the submitted discovery result and exact ref",
            )
        for collection in ("entity_dependencies", "bridge_dependencies"):
            for index, item in enumerate(value[collection]):
                require(
                    item["content_hash"]
                    == canonical_hash(
                        {key: child for key, child in item.items() if key != "content_hash"}
                    ),
                    f"{location}/{collection}/{index}/content_hash",
                    "DEPENDENCY_HASH",
                    "dependency content does not match its frozen hash",
                )
        request = exchange["request"]
        self.check_request(request, f"{EXAMPLES}#/verification_exchange/request")
        require(
            len(request["input"]) == 1
            and request["input"][0].get("role") == "user"
            and len(request["input"][0]["content"]) == 1
            and request["input"][0]["content"][0]["type"] == "input_text",
            f"{EXAMPLES}#/verification_exchange/request/input",
            "VERIFICATION_ISOLATION",
            "verification starts from a fresh context, without discovery protocol items",
        )
        context = decode_json(
            request["input"][0]["content"][0]["text"], location + "/request_context"
        )
        require(
            context["stage"] == "verification"
            and context["verification_input"] == value
            and context["tool_observations"] == []
            and bool(context["evidence_units"])
            and bool(context["schema_card"]),
            f"{EXAMPLES}#/verification_exchange/request/input/0/content/0/text",
            "VERIFICATION_ISOLATION",
            "fresh context needs original evidence, ontology card and the complete frozen input",
        )
        self.check_stage_answer(request, exchange["response"], "verification", location)
        self.counts["verification_requests"] += 1

    def check_protocol_errors(self) -> None:
        cases = self.examples["tool_protocol_error_examples"]
        require(
            {case["case"] for case in cases} >= {"unknown_function", "invalid_json"},
            f"{EXAMPLES}#/tool_protocol_error_examples",
            "ERROR_CASES",
            "both unknown function and unparseable arguments need restorable examples",
        )
        for index, case in enumerate(cases):
            path = f"{EXAMPLES}#/tool_protocol_error_examples/{index}"
            self.check_pairs(case["response_output"], [case["tool_output"]], path)
            before = case["observation_before_pause"]
            after = case["observation_after_resume"]
            for name, observation in (
                ("observation_before_pause", before),
                ("observation_after_resume", after),
            ):
                self.schema_value(
                    self.context_schemas["tool_protocol_error_observation"],
                    observation,
                    f"{path}/{name}",
                )
            call = case["response_output"][0]
            raw_call = {
                "call_id": call["call_id"],
                "name": call["name"],
                "arguments_json": call["arguments"],
            }
            require(
                before == after
                and after["call"] == raw_call
                and after["result"]
                == decode_json(case["tool_output"]["output"], path + "/tool_output")
                and case["dispatch_count"] == 0
                and case["tool_calls_used_before_pause"]
                == case["tool_calls_used_after_resume"]
                > 0,
                path,
                "ERROR_RESUME",
                "error recovery must preserve raw call/result and spent budget without dispatch",
            )
            if call["name"] not in self.schemas:
                expected_code = "unknown_tool"
            else:
                try:
                    args = decode_json(call["arguments"], path + "/arguments")
                    self.schema_value(self.schemas[call["name"]], args, path + "/arguments")
                except DesignError:
                    expected_code = "invalid_tool_arguments"
                else:
                    raise DesignError(f"[ERROR_CASE] {path}: supposed invalid call is valid")
            require(
                after["result"]["issues"][0]["code"] == expected_code,
                path,
                "ERROR_CODE",
                "protocol error result must describe the actual invalid call",
            )
            correction = case["correction"]
            self.check_pairs(
                correction["response_output"], [correction["tool_output"]], path + "/correction"
            )
            retry_call = correction["response_output"][0]
            retry_args = decode_json(retry_call["arguments"], path + "/correction/arguments")
            self.schema_value(
                self.schemas[retry_call["name"]], retry_args, path + "/correction/arguments"
            )
            retry = correction["observation"]
            require(
                retry["request_attempt"] > after["request_attempt"]
                and retry_call["call_id"] != call["call_id"]
                and retry["call"]
                == {
                    "call_id": retry_call["call_id"],
                    "name": retry_call["name"],
                    "arguments_json": retry_call["arguments"],
                }
                and retry["parsed_arguments"] == retry_args
                and retry["result"]
                == decode_json(correction["tool_output"]["output"], path + "/correction/output")
                and retry["result"]["status"] == "ok"
                and correction["dispatch_count"] == 1
                and correction["tool_calls_used"] == case["tool_calls_used_after_resume"] + 1
                and 1 < correction["model_calls_used"] <= 4,
                path + "/correction",
                "ERROR_CORRECTION",
                "corrected call needs new identity, valid args, successful result and spent budget",
            )
            self.counts["protocol_error_resume_cases"] += 1
            self.counts["corrected_tool_calls"] += 1

    def check_authorization_resume(self) -> None:
        example = self.examples["context_authorization_resume_example"]
        path = f"{EXAMPLES}#/context_authorization_resume_example"
        protocol = example["current_protocol"]
        auth = protocol["context_authorization"]
        self.schema_value(
            self.context_schemas["context_authorization"],
            auth,
            path + "/current_protocol/context_authorization",
        )
        ir = example["frozen_ir"]
        require(
            auth["ir_identity"] == ir["identity"]
            and auth["context_policy_hash"] == canonical_hash(example["context_policy"]),
            path,
            "AUTHORIZATION_IDENTITY",
            "authorization must bind frozen IR and the current task/scope/context policy",
        )
        expected_evidence = canonical_hash(
            {key: auth[key] for key in ("ir_identity", "fragments", "bindings")}
        )
        assembled = example["assembled_context_payload"]
        expected_context = canonical_hash(assembled)
        require(
            protocol["evidence_hash"] == expected_evidence
            and protocol["context_hash"] == expected_context,
            path + "/current_protocol/context_authorization",
            "AUTHORIZATION_HASH",
            "evidence/context hashes must include authorization roles, permissions and bindings",
        )
        units = {unit["evidence_id"]: unit["text"] for unit in ir["units"]}
        records = {record["record_id"]: set(record["evidence_ids"]) for record in ir["records"]}
        reconstructed = []
        for index, fragment in enumerate(auth["fragments"]):
            evidence_id, record_id = fragment["evidence_id"], fragment["record_id"]
            require(
                record_id in auth["record_ids"]
                and evidence_id in records.get(record_id, set())
                and evidence_id in units
                and 0 <= fragment["span_start"] < fragment["span_end"] <= len(units[evidence_id]),
                f"{path}/current_protocol/context_authorization/fragments/{index}",
                "AUTHORIZATION_FRAGMENT",
                "authorized record/evidence span must exist in the frozen IR",
            )
            reconstructed.append(
                {
                    **fragment,
                    "text": units[evidence_id][fragment["span_start"] : fragment["span_end"]],
                }
            )
        expected = {
            "task_id": auth["task_id"],
            "bindings": auth["bindings"],
            **{
                key: protocol[key]
                for key in ("evidence_revision", "evidence_hash", "context_hash")
            },
            "fragments": reconstructed,
        }
        seed = example["context_policy"]["base_target"]
        bound_target = {
            **seed,
            "context_hash": protocol["context_hash"],
            "source_scope_hash": canonical_hash(
                [
                    [item["anchor"], item["fact_eligible"], item["purpose"]]
                    for item in assembled["fragments"]
                ]
            ),
        }
        identity = {
            "run_fingerprint": seed["target_id"],
            **{
                key: bound_target[key]
                for key in (
                    "claim_ref",
                    "check_kind",
                    "subject_ref",
                    "predicate_iri",
                    "object_ref",
                    "literal_hash",
                    "assertion_polarity",
                    "applicability",
                    "source_scope_hash",
                    "context_hash",
                )
            },
        }
        bound_target["target_id"] = canonical_hash(
            {"namespace": "verification-target", "value": identity}
        )
        expected["target"] = bound_target
        actual_fragments = [
            {
                "evidence_id": item["anchor"]["evidence_id"],
                "span_start": item["anchor"]["span_start"],
                "span_end": item["anchor"]["span_end"],
                "text": item["text"],
                "role": item["purpose"],
                "fact_eligible": item["fact_eligible"],
            }
            for item in assembled["fragments"]
        ]
        require(
            actual_fragments
            == [
                {key: value for key, value in item.items() if key != "record_id"}
                for item in reconstructed
            ]
            and assembled["target"] == example["context_policy"]["base_target"]
            and all(assembled[key] == value for key, value in auth["bindings"].items())
            and all(
                assembled[key] == auth[key]
                for key in ("task_id", "context_policy_hash", "record_ids")
            )
            and assembled["evidence_revision"] == protocol["evidence_revision"],
            path + "/assembled_context_payload",
            "CONTEXT_PAYLOAD",
            "context hash payload must use frozen task seed, rebuilt text and exact authorization",
        )
        require(
            example["expected_restored_context"] == expected,
            path,
            "AUTHORIZATION_RECONSTRUCTION",
            "restored context must be derived from the current authorization and frozen IR",
        )
        require(
            example["confirmed_result_ref"] in protocol["completed_tool_results"],
            path + "/current_protocol",
            "AUTHORIZATION_CONFIRMATION",
            "current permission update must be confirmed together with its tool result",
        )
        require(
            not {"context_authorization_ref", "pending_call_ids"}.intersection(protocol)
            and not {"evidence_revision", "evidence_hash", "context_hash"}.intersection(auth),
            path + "/current_protocol",
            "SINGLE_AUTHORITY",
            "permission metadata and derived pending calls must not acquire a second authority",
        )
        call = example["inspect_call"]
        args = decode_json(call["arguments_json"], path + "/inspect_call/arguments_json")
        self.schema_value(
            self.schemas["inspect_evidence"], args, path + "/inspect_call/arguments_json"
        )
        selected = [
            {**fragment, "table": None}
            for fragment in reconstructed
            if fragment["evidence_id"] in args["evidence_ids"]
        ]
        result = example["inspect_result"]
        require(
            len(selected) == len(args["evidence_ids"])
            and result["status"] == "ok"
            and result["data"]["units"] == selected
            and result["data"]["omitted_ids"] == []
            and result["data"]["coverage_complete"] is True,
            path + "/inspect_result",
            "RESUMED_INSPECTION",
            "resumed inspection must preserve source text, span, role and fact eligibility",
        )
        self.counts["authorization_resume_cases"] += 1

    def check_tool_visibility(self) -> None:
        value = self.examples["tool_visibility"]
        path = f"{EXAMPLES}#/tool_visibility"
        registered = value["registered_tool_names"]
        controller = value["controller_only_tool_names"]
        stages = value["stage_allowed_tool_names"]
        require(
            len(registered) == len(set(registered))
            and set(registered) == set(self.schemas)
            and len(controller) == len(set(controller))
            and set(controller) == {"validate_metric"}
            and "validate_graph" in stages["verification"]
            and "validate_graph" not in stages["discovery"]
            and set(stages) == {"discovery", "verification", "finalize"}
            and stages["finalize"] == [],
            path,
            "TOOL_VISIBILITY",
            "catalog, controller-only calibration functions and model stages must be explicit",
        )
        model_tools: set[str] = set()
        for stage, names in stages.items():
            require(
                len(names) == len(set(names)) and set(names) <= set(registered) - set(controller),
                f"{path}/stage_allowed_tool_names/{stage}",
                "TOOL_VISIBILITY",
                "model stage tools must be a unique subset excluding controller-only functions",
            )
            model_tools.update(names)
        require(
            model_tools | set(controller) == set(registered),
            path,
            "TOOL_VISIBILITY",
            "every registered function must have an explicit reachable owner",
        )
        require(
            value["conditional_tool_names"] == [
                "propose_repair", "validate_graph", "find_referent_candidates",
            ]
            and all(
                {"propose_repair", "find_referent_candidates"} <= set(stages[stage])
                for stage in ("discovery", "verification")
            ),
            path,
            "TOOL_VISIBILITY",
            "repair and referent recall require the corresponding recovery or reference policy",
        )
        self.counts["model_visible_tools"] = len(model_tools)
        self.counts["controller_only_tools"] = len(controller)

    def check_request(self, request: dict[str, Any], location: str) -> None:
        require(
            request.get("store") is False
            and isinstance(request.get("instructions"), str)
            and bool(request["instructions"])
            and isinstance(request.get("input"), list),
            location,
            "RESPONSE_REQUEST",
            "request must have store=false, explicit instructions and complete input items",
        )
        forbidden = {
            "previous_response_id",
            "conversation",
            "messages",
            "response_format",
            "max_tokens",
        }
        require(
            not forbidden.intersection(request),
            location,
            "RESPONSE_REQUEST",
            f"forbidden Chat/session fields: {sorted(forbidden.intersection(request))}",
        )
        definitions = {tool["name"]: tool for tool in self.tools}
        for index, tool in enumerate(request["tools"]):
            require(
                tool == definitions.get(tool.get("name")),
                f"{location}/tools/{index}",
                "TOOL_WIRE",
                "request tool must equal its registered Responses definition",
            )

    def check_pairs(
        self, output_items: list[dict[str, Any]], results: list[dict[str, Any]], location: str
    ) -> None:
        calls = [item for item in output_items if item.get("type") == "function_call"]
        ids = [call["call_id"] for call in calls]
        result_ids = [result.get("call_id") for result in results]
        require(
            len(ids) == len(set(ids))
            and len(result_ids) == len(set(result_ids))
            and set(ids) == set(result_ids),
            location,
            "CALL_PAIRING",
            f"call_id must pair exactly once: calls={ids!r}, results={result_ids!r}",
        )
        for index, result in enumerate(results):
            require(
                result.get("type") == "function_call_output"
                and isinstance(result.get("output"), str),
                f"{location}/{index}",
                "TOOL_OUTPUT",
                "function_call_output.output must be a JSON string",
            )
            payload = decode_json(result["output"], f"{location}/{index}/output")
            require(
                set(payload) == {"status", "data", "evidence_refs", "issues"},
                f"{location}/{index}/output",
                "TOOL_ENVELOPE",
                "tool result must use the common typed envelope",
            )

    def derive_protocol_input(
        self, exchange: dict[str, Any], location: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Reconstruct this one-response fixture, with call IDs scoped to its single attempt."""
        protocol = exchange["current_protocol"]
        require(
            set(protocol) == {"stage_input_items", "turn_refs", "completed_tool_results"},
            location,
            "SINGLE_AUTHORITY",
            "persist only stage initial input and refs, not accumulated input or pending IDs",
        )
        require(
            len(protocol["turn_refs"]) == len(set(protocol["turn_refs"]))
            and len(protocol["completed_tool_results"])
            == len(set(protocol["completed_tool_results"])),
            location,
            "CALL_PAIRING",
            "confirmed artifact refs must occur once",
        )
        require(
            protocol["turn_refs"] == ["response"],
            location,
            "FIXTURE_SCOPE",
            "this example has one model attempt; production pairs by (request_attempt, call_id)",
        )
        results = {}
        for ref in protocol["completed_tool_results"]:
            name, index = ref.split("/")
            result = exchange[name][int(index)]
            require(
                result["call_id"] not in results,
                location,
                "CALL_PAIRING",
                "a call cannot acquire two confirmed results",
            )
            results[result["call_id"]] = result
        items = list(protocol["stage_input_items"])
        seen, pending = set(), []
        output = exchange[protocol["turn_refs"][0]]["output"]
        items.extend(output)
        calls = [item for item in output if item.get("type") == "function_call"]
        for call in calls:
            call_id = call["call_id"]
            require(
                call_id not in seen,
                location,
                "CALL_PAIRING",
                "call_id must be unique within this response batch",
            )
            seen.add(call_id)
            if call_id in results:
                self.check_pairs([call], [results[call_id]], location)
                items.append(results[call_id])
            else:
                pending.append(call_id)
        require(
            results.keys() <= seen,
            location,
            "CALL_PAIRING",
            "confirmed tool results must bind existing calls",
        )
        return items, pending

    def check_responses(self) -> None:
        fixture = self.examples["fixture_context"]
        require(
            self.stages["protocol_version"] == fixture["protocol_version"]
            and self.stages["api_protocol"] == fixture["api_protocol"] == "responses",
            f"{EXAMPLES}#/fixture_context",
            "PROTOCOL",
            "stage schemas and fixture must freeze the same Responses protocol",
        )
        exchange = self.examples["responses_exchange"]
        path = f"{EXAMPLES}#/responses_exchange"
        first, follow = exchange["request"], exchange["continuation_request"]
        response, final = exchange["response"], exchange["continuation_response"]
        for key in ("request", "continuation_request"):
            self.check_request(exchange[key], f"{path}/{key}")
        require(
            response["status"] == final["status"] == "completed",
            path,
            "RESPONSE_STATUS",
            "successful exchange must contain completed responses",
        )
        self.check_pairs(response["output"], exchange["tool_outputs"], path)
        derived_input, pending_call_ids = self.derive_protocol_input(exchange, path)
        require(
            first["instructions"] == follow["instructions"]
            and first["input"] == exchange["current_protocol"]["stage_input_items"]
            and not pending_call_ids
            and follow["input"] == derived_input,
            f"{path}/continuation_request/input",
            "COMPLETE_ITEMS",
            "continuation must preserve input, every ordered output item and all tool results",
        )
        for index, call in enumerate(response["output"]):
            if call["type"] != "function_call":
                continue
            location = f"{path}/response/output/{index}"
            require(
                call["id"] != call["call_id"], location, "CALL_ID", "fixture must distinguish IDs"
            )
            args = decode_json(call["arguments"], f"{location}/arguments")
            self.schema_value(self.schemas[call["name"]], args, f"{location}/arguments")
            require(
                args == self.examples["tool_arguments"][call["name"]],
                location,
                "CALL_ARGUMENTS",
                "wire call must match the valid argument fixture",
            )
        self.check_stage_answer(follow, final, "discovery", path)
        result = decode_json(exchange["tool_outputs"][0]["output"], f"{path}/tool_outputs/0/output")
        require(result["status"] == "ok", path, "INSPECT_RESULT", "inspect example must succeed")
        for index, unit in enumerate(result["data"]["units"]):
            require(
                unit["text"] == self.examples["source"][unit["span_start"] : unit["span_end"]],
                f"{path}/tool_outputs/0/output/data/units/{index}",
                "SOURCE_SPAN",
                "inspection span must reproduce source text",
            )
        error = self.examples["tool_error_example"]
        path = f"{EXAMPLES}#/tool_error_example"
        self.check_pairs(error["response_output"], [error["tool_output"]], path)
        call = error["response_output"][0]
        args = decode_json(call["arguments"], f"{path}/response_output/0/arguments")
        self.schema_value(self.schemas[call["name"]], args, f"{path}/response_output/0/arguments")
        payload = decode_json(error["tool_output"]["output"], f"{path}/tool_output/output")
        require(
            args["quote"] not in self.examples["source"]
            and payload["status"] == "blocked"
            and payload["data"] is None
            and payload["issues"][0]["field_path"] == "/quote"
            and payload["issues"][0]["code"] == "citation_quote_not_in_source",
            path,
            "QUOTE_ERROR",
            "invalid source quote must produce the documented blocked field error",
        )
        self.counts["responses_roundtrips"] += 1
        self.counts["error_pairs"] += 1

    def check_stage_answer(
        self, request: dict[str, Any], response: dict[str, Any], stage: str, location: str
    ) -> None:
        form = request["text"]["format"]
        require(
            request["tools"] == []
            and form["type"] == "json_schema"
            and form["schema"] == self.stages[stage]
            and form["strict"] is False,
            location,
            "STAGE_FORMAT",
            "answer request must use the exact stage schema, strict=false and no tools",
        )
        messages = [item for item in response["output"] if item.get("type") == "message"]
        require(
            len(messages) == 1, location, "STAGE_MESSAGE", "expected one final assistant message"
        )
        message = messages[0]
        require(message["role"] == "assistant", location, "STAGE_MESSAGE", "wrong message role")
        texts = [part["text"] for part in message["content"] if part["type"] == "output_text"]
        require(len(texts) == 1, location, "STAGE_MESSAGE", "expected one stage JSON text")
        payload = decode_json(texts[0], f"{location}/response/output_text")
        self.schema_value(self.stages[stage], payload, f"{location}/response/output_text")
        require(
            payload == self.examples[stage], location, "STAGE_PAYLOAD", "stage examples disagree"
        )

    def check_claims(self) -> None:
        discovery = self.examples["discovery"]
        ids = [
            item["local_id"]
            for collection in ("entities", "properties", "relations", "external_links")
            for item in discovery[collection]
        ]
        entities = {item["local_id"] for item in discovery["entities"]}
        registered = {
            item["entity_id"] for item in self.examples["fixture_context"]["registered_entities"]
        }
        require(
            len(ids) == len(set(ids)) and not set(ids).intersection(registered),
            f"{EXAMPLES}#/discovery",
            "CLAIM_IDS",
            "local IDs must be unique and must not collide with registered entities",
        )
        for index, relation in enumerate(discovery["relations"]):
            require(
                relation["subject_id"] in entities | registered
                and set(relation["object_ids"]) <= entities | registered
                and len(relation["object_ids"]) == len(set(relation["object_ids"]))
                and (relation["selection"] == "all" or bool(relation["selection_support"])),
                f"{EXAMPLES}#/discovery/relations/{index}",
                "RELATION_BINDING",
                "relation endpoints must resolve uniquely and selection needs support",
            )
        target_items = self.examples["verification_exchange"]["verification_input"]["targets"]
        targets = {target["target_id"]: target for target in target_items}
        checks = self.examples["verification"]["verifications"]
        verified = {item["target_id"]: item for item in checks}
        require(
            len(targets) == len(target_items)
            and len(verified) == len(checks)
            and set(targets) == set(verified),
            f"{EXAMPLES}#/verification",
            "VERIFICATION_TARGETS",
            "verification targets must occur exactly once and equal the requested set",
        )
        for target_id, target in targets.items():
            facets = [facet["name"] for facet in verified[target_id]["facets"]]
            require(
                target["target_kind"] == target_id.split(":", 1)[0]
                and target["content_hash"] == verified[target_id]["content_hash"]
                and len(facets) == len(set(facets))
                and set(facets) == set(target["required_facets"]),
                f"{EXAMPLES}#/verification_exchange/verification_input/targets/{target_id}",
                "VERIFICATION_TARGETS",
                "target kind/hash/facet set must match the independent verification result",
            )
        self.counts["verification_targets"] = len(targets)

    def check_quotes(self) -> None:
        sources = {
            item["evidence_id"]: self.examples[item["source_key"]]
            for item in self.examples["fixture_context"]["registered_evidence"]
        }

        def walk(value: Any, location: str) -> None:
            if isinstance(value, dict):
                if set(value) == {"evidence_id", "text", "context_text"} and isinstance(
                    value["evidence_id"], str
                ):
                    source = sources.get(value["evidence_id"])
                    locating = value["context_text"] or value["text"]
                    require(
                        source is not None
                        and bool(locating)
                        and source.count(locating) == 1
                        and value["text"] in locating,
                        location,
                        "QUOTE_REPLAY",
                        "quote and context must be uniquely replayable in authorized source",
                    )
                    self.counts["valid_quotes"] += 1
                for key, child in value.items():
                    walk(child, f"{location}/{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{location}/{index}")

        walk(self.examples, f"{EXAMPLES}#")

    def check_documents(self) -> None:
        for name, text in self.artifacts.markdown.items():
            require(text.endswith("\n"), name, "MARKDOWN_STYLE", "missing final newline")
            for number, line in enumerate(text.splitlines(), 1):
                require(
                    line.rstrip() == line, f"{name}:{number}", "MARKDOWN_STYLE", "trailing space"
                )
            headings = re.findall(r"^#{1,6} (.+)$", text, re.M)
            require(
                len(headings) == len(set(headings)), name, "MARKDOWN_HEADING", "duplicate headings"
            )
            for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
                target = unquote(match[1].strip("<>"))
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                    continue
                path = target.partition("#")[0]
                linked = (ROOT / name).parent / path if path else ROOT / name
                number = text.count("\n", 0, match.start()) + 1
                require(
                    linked.exists(),
                    f"{name}:{number}",
                    "LOCAL_LINK",
                    f"local link target does not exist: {target}",
                )
                self.counts["local_links"] += 1
            for match in re.finditer(r"```(json|python)\n(.*?)\n```", text, re.S):
                language, block = match[1], match[2]
                line = text.count("\n", 0, match.start()) + 2
                if language == "json":
                    decode_json(block, f"{name}:{line}")
                    self.counts["json_blocks"] += 1
                else:
                    try:
                        ast.parse(block, filename=name)
                    except SyntaxError as error:
                        raise DesignError(
                            f"[PYTHON_SYNTAX] {name}:{line + (error.lineno or 1) - 1}: {error.msg}"
                        ) from error
                    self.counts["python_blocks"] += 1
        design = self.artifacts.markdown[DESIGN]
        for title, stop, expected in (
            ("### 5.4 ", "## 6.", self.examples["discovery"]),
            (
                "### 7.3 ",
                "### 7.4 ",
                self.examples["responses_exchange"]["response"]["output"]
                + self.examples["responses_exchange"]["tool_outputs"],
            ),
        ):
            require(title in design, DESIGN, "DESIGN_SECTION", f"missing section {title}")
            section = design.split(title, 1)[1].split(stop, 1)[0]
            block = re.search(r"```json\n(.*?)\n```", section, re.S)
            require(block is not None, DESIGN, "DESIGN_EXAMPLE", f"missing JSON in {title}")
            require(
                decode_json(block[1], f"{DESIGN}#{title.strip()}") == expected,
                DESIGN,
                "DESIGN_EXAMPLE",
                f"{title.strip()} JSON diverges from the contract example",
            )

    def check_traceability(self) -> None:
        spec_name, plan_name, tasks_name = (
            f"{SPEC_PATH}/{name}.md" for name in ("spec", "plan", "tasks")
        )
        requirements = re.findall(r"^\| (FR-\d+) \|", self.artifacts.markdown[spec_name], re.M)
        require(
            set(requirements) == {f"FR-{index:02}" for index in range(1, 21)}
            and len(requirements) == len(set(requirements)),
            spec_name,
            "REQUIREMENTS",
            "the approved FR-01 through FR-20 must remain unique and present",
        )
        modules: dict[str, set[str]] = {}
        for line in self.artifacts.markdown[plan_name].splitlines():
            match = re.match(r"\| (M\d+) ", line)
            if not match:
                continue
            module = match[1]
            require(module not in modules, plan_name, "MODULES", f"duplicate module {module}")
            raw = line.rstrip("|").split("|")[-1]
            modules[module] = {f"FR-{part}" for part in re.findall(r"\d{2}", raw)}
        require(
            set(modules) == {f"M{index:02}" for index in range(1, 14)}
            and set().union(*modules.values()) == set(requirements),
            plan_name,
            "MODULES",
            "M01 through M13 must cover all and only the approved requirements",
        )
        deps: dict[str, set[str]] = {}
        for number, line in enumerate(self.artifacts.markdown[tasks_name].splitlines(), 1):
            match = re.match(r"- \[[ xX]\] \*\*(T\d+)", line)
            if not match:
                continue
            task = match[1]
            require(task not in deps, f"{tasks_name}:{number}", "TASKS", f"duplicate task {task}")
            require(
                "依赖：" in line, f"{tasks_name}:{number}", "TASKS", f"missing dependencies: {task}"
            )
            raw = line.split("依赖：", 1)[1].split("。", 1)[0]
            raw = re.sub(
                r"T(\d+)—T(\d+)",
                lambda m: "/".join(f"T{i:02}" for i in range(int(m[1]), int(m[2]) + 1)),
                raw,
            )
            deps[task] = set(re.findall(r"T\d+", raw))
        require(
            set(deps) == {f"T{index:02}" for index in range(1, 38)},
            tasks_name,
            "TASKS",
            "the approved T01 through T37 must remain present without additions",
        )
        visited: set[str] = set()
        visiting: list[str] = []

        def visit(task: str) -> None:
            require(task in deps, tasks_name, "TASK_DEPENDENCY", f"unknown dependency {task}")
            require(task not in visiting, tasks_name, "TASK_CYCLE", " -> ".join([*visiting, task]))
            if task in visited:
                return
            visiting.append(task)
            for dependency in sorted(deps[task]):
                visit(dependency)
            visiting.pop()
            visited.add(task)

        for task in deps:
            visit(task)
        self.counts.update(
            requirements=len(requirements), modules=len(modules), acyclic_tasks=len(deps)
        )

    def run(self) -> dict[str, int]:
        self.check_schemas()
        self.check_responses()
        self.check_claims()
        self.check_verification_input()
        self.check_protocol_errors()
        self.check_authorization_resume()
        self.check_tool_visibility()
        self.check_quotes()
        self.check_documents()
        self.check_traceability()
        return dict(self.counts)


def self_test(artifacts: Artifacts) -> dict[str, int]:
    """Reject deliberately broken design artifacts through the same public checker."""
    mutations: list[tuple[str, str, Callable[[Artifacts], None]]] = []

    def add(name: str, code: str, mutate: Callable[[Artifacts], None]) -> None:
        mutations.append((name, code, mutate))

    add(
        "legacy source assertion compatibility does not make relation objects optional",
        "REQUIRED_FIELDS",
        lambda data: data.json_files[STAGES]["discovery"]["$defs"]["RelationProposal"][
            "required"
        ].remove("object_ids"),
    )
    add(
        "legacy reference compatibility does not make entities optional",
        "REQUIRED_FIELDS",
        lambda data: data.json_files[STAGES]["discovery"]["required"].remove("entities"),
    )
    add(
        "compatibility fields do not open arbitrary relation properties",
        "CLOSED_OBJECT",
        lambda data: data.json_files[STAGES]["discovery"]["$defs"]["RelationProposal"].update(
            additionalProperties=True,
        ),
    )
    add(
        "invalid tool argument",
        "JSON_SCHEMA",
        lambda data: data.json_files[EXAMPLES]["tool_arguments"]["inspect_evidence"].update(
            evidence_ids=42
        ),
    )
    add(
        "mismatched call_id",
        "CALL_PAIRING",
        lambda data: data.json_files[EXAMPLES]["responses_exchange"]["tool_outputs"][0].update(
            call_id="not-the-requested-call"
        ),
    )
    add(
        "dropped Responses item",
        "COMPLETE_ITEMS",
        lambda data: data.json_files[EXAMPLES]["responses_exchange"]["continuation_request"][
            "input"
        ].pop(0),
    )
    add(
        "accumulated input stored as a second authority",
        "SINGLE_AUTHORITY",
        lambda data: data.json_files[EXAMPLES]["responses_exchange"]["current_protocol"].update(
            active_input_items=[]
        ),
    )
    add(
        "pending calls stored instead of derived",
        "SINGLE_AUTHORITY",
        lambda data: data.json_files[EXAMPLES]["responses_exchange"]["current_protocol"].update(
            pending_call_ids=[]
        ),
    )
    reasoning_item = {
        "type": "reasoning",
        "id": "reasoning-example",
        "summary": [],
        "encrypted_content": "synthetic-encrypted-reasoning",
    }
    add(
        "dropped reasoning output on resume",
        "COMPLETE_ITEMS",
        lambda data: data.json_files[EXAMPLES]["responses_exchange"]["response"]["output"].insert(
            0, reasoning_item
        ),
    )
    add(
        "confirmed result referenced twice",
        "CALL_PAIRING",
        lambda data: data.json_files[EXAMPLES]["responses_exchange"]["current_protocol"][
            "completed_tool_results"
        ].append("tool_outputs/0"),
    )
    add(
        "dangling local link",
        "LOCAL_LINK",
        lambda data: data.markdown.update(
            {
                f"{SPEC_PATH}/spec.md": data.markdown[f"{SPEC_PATH}/spec.md"]
                + "\n[x](missing-027.md)\n"
            }
        ),
    )
    add(
        "cyclic task dependency",
        "TASK_CYCLE",
        lambda data: data.markdown.update(
            {
                f"{SPEC_PATH}/tasks.md": data.markdown[f"{SPEC_PATH}/tasks.md"].replace(
                    "依赖：无。", "依赖：T26。", 1
                )
            }
        ),
    )
    add(
        "missing approved reference-resolution acceptance task",
        "TASKS",
        lambda data: data.markdown.update({
            f"{SPEC_PATH}/tasks.md": re.sub(
                r"^- \[[ xX]\] \*\*T37[^\n]*\n?", "",
                data.markdown[f"{SPEC_PATH}/tasks.md"], flags=re.M,
            ),
        }),
    )
    add(
        "unapproved extra task",
        "TASKS",
        lambda data: data.markdown.update({
            f"{SPEC_PATH}/tasks.md": data.markdown[f"{SPEC_PATH}/tasks.md"]
            + "\n- [ ] **T38**：未授权扩展。依赖：T37。\n",
        }),
    )
    add(
        "missing approved request-budget requirement",
        "REQUIREMENTS",
        lambda data: data.markdown.update({
            f"{SPEC_PATH}/spec.md": re.sub(
                r"^\| FR-08 \|[^\n]*\n?", "",
                data.markdown[f"{SPEC_PATH}/spec.md"], flags=re.M,
            ),
        }),
    )
    add(
        "unapproved extra requirement",
        "REQUIREMENTS",
        lambda data: data.markdown.update({
            f"{SPEC_PATH}/spec.md": data.markdown[f"{SPEC_PATH}/spec.md"]
            + "\n| FR-21 | 未授权扩展 | 不得通过 |\n",
        }),
    )
    add(
        "missing verification payload",
        "JSON_SCHEMA",
        lambda data: data.json_files[EXAMPLES]["verification_exchange"]["verification_input"][
            "targets"
        ][0].pop("payload"),
    )
    add(
        "changed relation selection with stale hash",
        "CLAIM_HASH",
        lambda data: data.json_files[EXAMPLES]["verification_exchange"]["verification_input"][
            "targets"
        ][-1]["payload"].update(selection="all"),
    )
    add(
        "redundant verification input hash restored",
        "JSON_SCHEMA",
        lambda data: data.json_files[EXAMPLES]["verification_exchange"][
            "verification_input"
        ].update(input_hash="0" * 64),
    )
    add(
        "invalid call acquired parsed arguments during resume",
        "JSON_SCHEMA",
        lambda data: data.json_files[EXAMPLES]["tool_protocol_error_examples"][0][
            "observation_after_resume"
        ].update(parsed_arguments={}),
    )
    add(
        "error feedback without issues",
        "JSON_SCHEMA",
        lambda data: data.json_files[EXAMPLES]["tool_protocol_error_examples"][0][
            "observation_before_pause"
        ]["result"].update(issues=[]),
    )
    add(
        "unconfirmed authorization result",
        "AUTHORIZATION_CONFIRMATION",
        lambda data: data.json_files[EXAMPLES]["context_authorization_resume_example"][
            "current_protocol"
        ].update(completed_tool_results=[]),
    )
    add(
        "restored permission escalation",
        "AUTHORIZATION_RECONSTRUCTION",
        lambda data: data.json_files[EXAMPLES]["context_authorization_resume_example"][
            "expected_restored_context"
        ]["fragments"][-1].update(fact_eligible=True),
    )
    add(
        "authorization hash stored twice",
        "JSON_SCHEMA",
        lambda data: data.json_files[EXAMPLES]["context_authorization_resume_example"][
            "current_protocol"
        ]["context_authorization"].update(context_hash="0" * 64),
    )
    add(
        "controller calibration tool exposed to model",
        "TOOL_VISIBILITY",
        lambda data: data.json_files[EXAMPLES]["tool_visibility"]["stage_allowed_tool_names"][
            "verification"
        ].append("validate_metric"),
    )
    add(
        "unregistered schema resource",
        "SCHEMA_REFERENCE",
        lambda data: data.json_files[CONTEXT]["verification_input"]["properties"]["targets"].update(
            items={"$ref": "https://unregistered.invalid/schema.json"}
        ),
    )

    add(
        "dangling registered root",
        "REFERENCE_CLOSURE",
        lambda data: data.json_files[EXAMPLES]["verification_exchange"]["verification_input"][
            "local_ref_map"
        ].update(s={"id": "unregistered-root", "revision": 1}),
    )

    def duplicate_entity(data: Artifacts) -> None:
        value = data.json_files[EXAMPLES]["verification_exchange"]["verification_input"]
        claim = value["targets"][0]
        redundant = copy.deepcopy(value["entity_dependencies"][0])
        redundant.update(
            entity_ref=claim["claim_ref"],
            class_iri=claim["payload"]["class_iri"],
            grounding_kind="mention",
            root_origin=None,
            proposal=claim["payload"],
        )
        value["entity_dependencies"].append(redundant)

    add("duplicate entity target as dependency", "REFERENCE_CLOSURE", duplicate_entity)

    def omit_target_consistently(data: Artifacts) -> None:
        examples = data.json_files[EXAMPLES]
        exchange = examples["verification_exchange"]
        value = exchange["verification_input"]
        omitted = value["targets"].pop()
        value["local_ref_map"].pop(omitted["payload"]["local_id"])
        examples["verification"]["verifications"] = [
            item
            for item in examples["verification"]["verifications"]
            if item["target_id"] != omitted["target_id"]
        ]
        message = exchange["request"]["input"][0]["content"][0]
        context = decode_json(message["text"], "self-test/omitted-target-context")
        context["verification_input"] = value
        message["text"] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        for output in exchange["response"]["output"]:
            if output["type"] == "message":
                for part in output["content"]:
                    if part["type"] == "output_text":
                        part["text"] = json.dumps(examples["verification"], ensure_ascii=False)

    add(
        "target omitted consistently from input and answer",
        "VERIFICATION_COVERAGE",
        omit_target_consistently,
    )
    for name, code, mutate in mutations:
        broken = copy.deepcopy(artifacts)
        mutate(broken)
        try:
            DesignChecker(broken).run()
        except DesignError as error:
            require(f"[{code}]" in str(error), name, "SELF_TEST", f"wrong rejection: {error}")
        else:
            raise DesignError(f"[SELF_TEST] {name}: invalid artifact was accepted")

    checker = DesignChecker(artifacts)
    exchange = copy.deepcopy(artifacts.json_files[EXAMPLES]["responses_exchange"])
    exchange["response"]["output"].insert(0, reasoning_item)
    protocol = exchange["current_protocol"]
    confirmed_refs = protocol["completed_tool_results"]
    protocol["completed_tool_results"] = []
    incomplete, pending = checker.derive_protocol_input(exchange, "self-test/pending-call")
    require(
        incomplete == protocol["stage_input_items"] + exchange["response"]["output"]
        and pending == [exchange["response"]["output"][1]["call_id"]],
        "self-test/pending-call",
        "SELF_TEST",
        "unconfirmed call must remain pending without losing complete reasoning/output items",
    )
    protocol["completed_tool_results"] = confirmed_refs
    complete, pending = checker.derive_protocol_input(exchange, "self-test/confirmed-call")
    require(
        complete == incomplete + exchange["tool_outputs"] and pending == [],
        "self-test/confirmed-call",
        "SELF_TEST",
        "confirmed result must pair once and clear derived pending calls",
    )

    # A legal property whose numeric quote and table-header unit are separate.
    # This catches the reviewed mg -> g identity failure without relying on a
    # malformed relation or an extra field that JSON Schema would already reject.
    def quote(text: str) -> dict[str, Any]:
        return {"evidence_id": "unit-self-test", "text": text, "context_text": None}

    property_value = {
        "local_id": "p1",
        "subject_id": "s",
        "predicate_iri": "urn:example:quantity",
        "value_quote": quote("5"),
        "field_support": [quote("Quantity")],
        "unit_support": [quote("mg")],
        "qualifiers": {
            "polarity": "affirmed",
            "modality": "asserted",
            "condition_support": [],
            "scope_qualifiers": [],
        },
        "bridge_kind": "role_mapped_table",
        "bridge_ref_ids": [],
    }
    changed_unit = copy.deepcopy(property_value)
    changed_unit["unit_support"] = [quote("g")]
    added_proof = copy.deepcopy(property_value)
    added_proof["unit_support"].append({**quote("mg"), "evidence_id": "second-unit-source"})
    schema = {"$ref": "urn:ontology-tool-extraction:discovery#/$defs/PropertyProposal"}
    for name, value in (
        ("original unit", property_value),
        ("changed unit", changed_unit),
        ("more proof", added_proof),
    ):
        checker.schema_value(schema, value, f"self-test/{name}")
    original = checker.semantic_content("property", property_value)
    hash_checks = 0
    require(
        original["raw_value"] == "5" and original["source_unit_texts"] == ["mg"],
        "self-test/property-unit",
        "SELF_TEST",
        "claim identity lost separate raw unit text",
    )
    hash_checks += 1
    require(
        canonical_hash(original)
        != canonical_hash(checker.semantic_content("property", changed_unit)),
        "self-test/property-unit",
        "SELF_TEST",
        "mg -> g must change semantic content hash",
    )
    hash_checks += 1
    require(
        canonical_hash(original)
        == canonical_hash(checker.semantic_content("property", added_proof)),
        "self-test/property-unit",
        "SELF_TEST",
        "same-unit additional proof must retain claim identity",
    )
    hash_checks += 1
    invalid_json_count = 0
    for token in ("NaN", "Infinity", "-Infinity"):
        try:
            decode_json('{"value":' + token + "}", "self-test/nonstandard-json")
        except DesignError:
            invalid_json_count += 1
        else:
            raise DesignError(f"[SELF_TEST] nonstandard JSON accepted: {token}")
    return {
        "rejected_self_test_mutations": len(mutations),
        "protocol_reconstruction_checks": 2,
        "property_unit_hash_checks": hash_checks,
        "rejected_nonstandard_json_values": invalid_json_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true", help="also reject in-memory corruptions"
    )
    arguments = parser.parse_args()
    try:
        artifacts = Artifacts.read()
        counts = DesignChecker(artifacts).run()
        if arguments.self_test:
            counts.update(self_test(artifacts))
    except (DesignError, OSError, KeyError, TypeError, IndexError) as error:
        print(f"Design artifact check failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"scope": "design_artifacts_only", **counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
