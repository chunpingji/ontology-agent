"""Lossless V1 migration drafts. Unknown semantics remain unresolved references."""

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.template_v2 import TemplateV2

BASELINE_ID = "dea037a2-f4b5-478a-8e0e-d5471fc45cbc"
BASELINE_HASH = "78721d33afb40f12e77d2e048b5d613945c3442fafe759256561fbb4bce6b8d1"
EQUIPMENT_SLOT = "grp_1783352185921_0_0.new_1783380221877"
RISK_SLOTS = {
    f"grp_1783352185921_0_2.ai_{i}": name
    for i, name in enumerate(
        (
            "hazard",
            "factors",
            "pre_control",
            "post_control",
            "controls",
            "traceability",
            "decision",
        )
    )
}
TEAM_INPUTS = {
    "grp_1783352185921_0_1.ai_0": "assessment_team_rows",
    "grp_report_teams.assessment": "assessment_team_rows",
    "grp_1783352185921_0_3.ai_3": "approver_team_rows",
    "grp_report_teams.approver": "approver_team_rows",
}
DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
EQUIPMENT = "https://ontology.pharma-gmp.cn/slpra/equipment/"
DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
KNOWN_INPUTS = {
    **{
        f"grp_1783352185921_0_0.ai_{i}": name
        for i, name in enumerate(
            (
                "product_identity",
                "batch_range",
                "batch_range",
                "production_purpose",
                "dosage_form",
                "administration_route",
                "is_highly_active",
                "is_cytotoxic",
                "is_penicillin_type",
                "is_hormonal",
                "process_description",
                "production_area_rows",
                "shared_line_context",
            )
        )
    },
    "grp_1783352185921_0_0.new_1785941569398": "source_document_ref",
    **{
        f"grp_1783352185921_0_3.ai_{i}": name
        for i, name in enumerate(
            ("attachment_inventory", "review_date", "qa_opinion", "approver_team_rows")
        )
    },
    **{
        f"grp_1783352185921_1_0.ai_{i}": name
        for i, name in enumerate(("review_findings", "review_decision", "review_signoff"))
    },
    **TEAM_INPUTS,
}


def migrate_template(
    schema,
    *,
    template_id,
    version,
    revision_no=1,
    family_id=None,
    ontology_ref="unresolved:ontology",
    style_ref="unresolved:style",
    policy_ref="unresolved:policy",
):
    original = deepcopy(schema)
    source_hash = evidence_hash(original)
    identity = evidence_hash([str(template_id), source_hash, "migration-v2.1"])
    known = str(template_id) == BASELINE_ID and source_hash == BASELINE_HASH
    target = {
        "schema_version": 2,
        "template_family_id": family_id or str(template_id),
        "template_revision_id": evidence_hash([identity, "draft"]),
        "revision_no": revision_no + 1,
        "document_revision": "",
        "doc_no": original.get("doc_no", ""),
        "ontology_release_ref": ontology_ref,
        "style_profile_ref": style_ref,
        "publication_policy_ref": policy_ref,
        "source_slots": [],
        "definitions": {"bindings": {}, "inputs": {}},
        "sections": [],
        "legacy": {
            "template_id": str(template_id),
            "version": version,
            "schema_hash": source_hash,
            "migration_plan_ref": identity,
        },
        "migration_issues": [],
    }
    mappings, definitions = [], target["definitions"]

    def problem(old_id, message, code="MIGRATION_SEMANTICS_UNRESOLVED"):
        target["migration_issues"].append({"old_id": old_id, "code": code, "message": message})

    if known:
        target["source_slots"] = [
            {"source_slot_id": "cmc", "kind": "document", "class_iri": DEV + "CMCReport"}
        ]
        definitions["bindings"]["equipment"] = {
            "binding_id": "equipment",
            "kind": "facts",
            "contract_ref": {
                "kind": "ontology",
                "release_ref": ontology_ref,
                "root_class_iri": DEV + "CMCReport",
                "result_class_iri": EQUIPMENT + "Equipment",
            },
            "scope": {
                "source_slot": "cmc",
                "predicate_path": [{"predicate_iri": DEV + "usesEquipment"}],
            },
        }
        for key, predicate, result in (
            ("product", "describes", DRUG + "DrugProduct"),
            ("production_plan", "hasProductionPlan", DEV + "ClinicalSampleProductionPlan"),
        ):
            definitions["bindings"][key] = {
                "binding_id": key,
                "kind": "facts",
                "contract_ref": {
                    "kind": "ontology",
                    "release_ref": ontology_ref,
                    "root_class_iri": DEV + "CMCReport",
                    "result_class_iri": result,
                },
                "scope": {
                    "source_slot": "cmc",
                    "predicate_path": [{"predicate_iri": DEV + predicate}],
                    "cardinality": {"min_count": 1, "max_count": 1},
                },
            }
        definitions["inputs"]["batch_bounds"] = {
            "input_id": "batch_bounds",
            "name": "batch_bounds",
            "label": "同一生产计划的批量上下界",
            "binding_ref": "production_plan",
            "required": True,
            "projection": {
                "kind": "record",
                "fields": {
                    key: {
                        "value": {"kind": "property", "property_iri": DEV + prop},
                        "required": True,
                    }
                    for key, prop in (
                        ("lower", "plannedBatchSizeMin_kg"),
                        ("upper", "plannedBatchSizeMax_kg"),
                    )
                },
            },
        }
        definitions["bindings"]["batch_range_view"] = {
            "binding_id": "batch_range_view",
            "kind": "derived",
            "provider": "view",
            "contract_ref": "unresolved:batch_range_contract",
            "operation": {
                "kind": "range",
                "source": {"input_id": "batch_bounds"},
                "lower_field": "lower",
                "upper_field": "upper",
            },
        }
        problem(
            "batch_range",
            "确认 kg 数量类型契约与上下界开闭语义；不得假定边界包含，或跨计划拼接。",
            "RANGE_BOUNDARY_UNRESOLVED",
        )

    def groups(items):
        output = []
        for group in items:
            target_group = {
                "group_id": group["group_id"],
                "title": group.get("title", ""),
                "origin": deepcopy(group.get("origin")),
                "units": [],
                "groups": groups(group.get("groups", [])),
            }
            risk_unit = None
            for slot in group.get("slots", []):
                old_id, label = slot["slot_id"], slot.get("label", "")
                input_id = (
                    KNOWN_INPUTS.get(old_id, "input:" + old_id) if known else "input:" + old_id
                )
                output_id = "output:" + old_id
                if known and old_id in RISK_SLOTS:
                    input_id, output_id = "risk_rows", "output:risk_matrix"
                binding_id = "unresolved:" + input_id
                projection = {"kind": "identity"}
                if known and input_id == "batch_range":
                    binding_id, output_id = "batch_range_view", "output:batch_range"
                properties = {
                    "dosage_form": "dosageForm",
                    "administration_route": "routeOfAdministration",
                    "is_highly_active": "isHighlyActive",
                    "is_cytotoxic": "isCytotoxic",
                    "is_penicillin_type": "isPenicillinType",
                    "is_hormonal": "isHormonal",
                }
                if known and input_id in properties:
                    binding_id = "product"
                    projection = {"kind": "property", "property_iri": DRUG + properties[input_id]}
                if known and input_id == "production_purpose":
                    binding_id = "production_plan"
                    projection = {"kind": "property", "property_iri": DEV + "productionPurpose"}
                if known and old_id == EQUIPMENT_SLOT:
                    input_id, binding_id = "equipment_rows", "equipment"
                    projection = {
                        "kind": "records",
                        "fields": {
                            name: {
                                "value": {"kind": "property", "property_iri": EQUIPMENT + prop},
                                "required": name != "specification",
                            }
                            for name, prop in (
                                ("equipment_code", "equipmentID"),
                                ("equipment_name", "equipmentName"),
                                ("specification", "modelSpecification"),
                            )
                        },
                    }
                    projection["fields"]["materials"] = {
                        "value": {
                            "kind": "entities",
                            "class_iri": EQUIPMENT + "ConstructionMaterial",
                            "predicate_path": [{"predicate_iri": EQUIPMENT + "constructedOf"}],
                        },
                        "required": True,
                    }
                source = slot.get("source", {})
                if source.get("kind") == "snapshot":
                    binding_id = "snapshot:" + evidence_hash(
                        {
                            k: source[k]
                            for k in ("root_class_iri", "range_class_iri", "predicate_path")
                        }
                    )
                    slot_id = "source:" + evidence_hash(source["root_class_iri"])[:16]
                    if not any(s["source_slot_id"] == slot_id for s in target["source_slots"]):
                        target["source_slots"].append(
                            {
                                "source_slot_id": slot_id,
                                "kind": "document",
                                "class_iri": source["root_class_iri"],
                            }
                        )
                    definitions["bindings"][binding_id] = {
                        "binding_id": binding_id,
                        "kind": "facts",
                        "contract_ref": {
                            "kind": "ontology",
                            "release_ref": ontology_ref,
                            "root_class_iri": source["root_class_iri"],
                            "result_class_iri": source["range_class_iri"],
                        },
                        "scope": {
                            "source_slot": slot_id,
                            "predicate_path": source["predicate_path"],
                        },
                    }
                    props = source.get("data_property_iris", [])
                    if props:
                        projection = {
                            "kind": "records",
                            "fields": {
                                "field:" + evidence_hash(prop)[:16]: {
                                    "value": {"kind": "property", "property_iri": prop},
                                    "required": True,
                                }
                                for prop in props
                            },
                        }
                    else:
                        projection = {"kind": "entities", "class_iri": source["range_class_iri"]}
                    if source.get("text"):
                        problem(
                            old_id,
                            "旧 text 投影需声明显示属性，不能继续拼接自由文本。",
                            "ENTITY_DISPLAY_UNDECLARED",
                        )
                definitions["inputs"].setdefault(
                    input_id,
                    {
                        "input_id": input_id,
                        "name": input_id,
                        "label": label,
                        "binding_ref": binding_id,
                        "projection": projection,
                        "required": True,
                        "origin": deepcopy(slot.get("origin")),
                    },
                )
                unit = {
                    "output_id": output_id,
                    "title": label,
                    "origin": deepcopy(slot.get("origin")),
                    "bindings": [{"binding_ref": binding_id}],
                    "inputs": [{"input_ref": input_id, "alias": input_id, "required": True}],
                    "render": {
                        "kind": "narrative",
                        "mode": "composed",
                        "nodes": [{"kind": "input_ref", "input_id": input_id}],
                    },
                }
                if known and input_id == "batch_range":
                    unit["title"] = "预计批量范围（kg）"
                    unit["bindings"].append({"binding_ref": "production_plan"})
                    unit["inputs"].append(
                        {"input_ref": "batch_bounds", "alias": "batch_bounds", "required": True}
                    )
                if known and old_id in RISK_SLOTS:
                    if risk_unit is None:
                        risk_unit = {
                            **unit,
                            "title": "风险矩阵",
                            "render": {
                                "kind": "table",
                                "rows": {"input_id": "risk_rows"},
                                "columns": [],
                            },
                        }
                        target_group["units"].append(risk_unit)
                    risk_unit["render"]["columns"].append(
                        {
                            "column_id": old_id,
                            "title": label,
                            "field_ref": RISK_SLOTS[old_id],
                        }
                    )
                elif known and (old_id == EQUIPMENT_SLOT or old_id in TEAM_INPUTS):
                    fields = (
                        [
                            ("equipment_code", "设备编号"),
                            ("equipment_name", "设备名称"),
                            ("specification", "规格"),
                            ("materials", "材质"),
                        ]
                        if old_id == EQUIPMENT_SLOT
                        else [("name", "姓名"), ("role", "角色"), ("department", "部门")]
                    )
                    unit["render"] = {
                        "kind": "table",
                        "rows": {"input_id": input_id},
                        "columns": [
                            {
                                "column_id": input_id + ":" + field,
                                "title": title,
                                "field_ref": field,
                            }
                            for field, title in fields
                        ],
                    }
                    target_group["units"].append(unit)
                elif not any(u["output_id"] == output_id for u in target_group["units"]):
                    target_group["units"].append(unit)
                if known and old_id in {"grp_report_teams.assessment", "grp_report_teams.approver"}:
                    region_id = "assessment" if input_id == "assessment_team_rows" else "approval"
                    signature_output = output_id + ":signatures"
                    target_group["units"].append(
                        {
                            "output_id": signature_output,
                            "title": label + "签署",
                            "bindings": [],
                            "inputs": [],
                            "render": {
                                "kind": "form",
                                "fields": [
                                    {
                                        "field_id": region_id,
                                        "label": "签署记录",
                                        "signature_region_id": region_id,
                                    }
                                ],
                            },
                        }
                    )
                mappings.append(
                    {
                        "old_id": old_id,
                        "old_source": deepcopy(source),
                        "old_required": slot.get("required", False),
                        "origin": deepcopy(slot.get("origin")),
                        "input_ids": [input_id],
                        "binding_ids": [binding_id],
                        "output_ids": [output_id]
                        + (
                            [signature_output]
                            if known
                            and old_id
                            in {"grp_report_teams.assessment", "grp_report_teams.approver"}
                            else []
                        ),
                        "field_paths": [[RISK_SLOTS[old_id]]]
                        if known and old_id in RISK_SLOTS
                        else [],
                        "status": "draft_requires_review",
                        "review_ref": None,
                    }
                )
                problem(old_id, "确认精确输入契约、主体/时间/完整性及最低材料要求；原定义已保留。")
                if source.get("prompt"):
                    problem(
                        old_id,
                        "原行文须拆分为授权引用与逐声明前提，不能直接执行旧 Prompt。",
                        "LEGACY_PROMPT_REVIEW_REQUIRED",
                    )
            if group.get("repeat") or group.get("repeat_over"):
                problem(group["group_id"], "重复范围须用稳定输入身份及已证明集合显式配置。")
            output.append(target_group)
        return output

    for section in original.get("sections", []):
        target["sections"].append(
            {
                "section_id": section["section_id"],
                "title": section.get("title", ""),
                "origin": deepcopy(section.get("origin")),
                "groups": groups(section.get("groups", [])),
            }
        )
        if section.get("prompt") or section.get("coverage"):
            problem(
                section["section_id"], "原章节行文/覆盖已存档，需拆分为显式输出引用与完整性要求。"
            )
    from app.services.reasoning.pde_calculation import default_checks

    target["calculation_checks"] = default_checks(target["source_slots"])
    return {
        "migration_plan_id": identity,
        "original_schema": original,
        "original_schema_hash": source_hash,
        "original_template_id": str(template_id),
        "original_version": version,
        "original_schema_revision": original.get("revision"),
        "mapping_count": len(mappings),
        "mappings": mappings,
        "target_schema": TemplateV2.model_validate(target).model_dump(mode="json"),
        "status": "draft",
        "business_reviewed": False,
        "baseline_mapping_applied": known,
    }
