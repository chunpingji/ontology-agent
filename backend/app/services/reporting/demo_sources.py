"""Finite, explicit demonstration sources for the existing V2 report input bundle."""

from copy import deepcopy
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.models.mock_data import (
    MockEquipment,
    MockEquipmentScheduleOverride,
    MockProductionArea,
    MockTeamMember,
)
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.input_resolver import (
    business_value,
    issue,
    summarize,
    typed_value,
    unavailable_value,
)
from app.services.reporting.template_v2 import ReportingError

PROMPT_REF = "urn:report:prompt:referenced:1"
PROVIDERS = {
    "assessment_team": (
        "评估小组（Mock）",
        {"name": "姓名", "role": "角色", "department": "部门", "role_code": "角色编码"},
    ),
    "approver_team": (
        "审批小组名单（Mock，非签署）",
        {"name": "姓名", "role": "角色", "department": "部门", "role_code": "角色编码"},
    ),
    "equipment": (
        "设备档案（Mock）",
        {
            "equipment_id": "设备编号",
            "name": "设备名称",
            "workshop_code": "车间编号",
            "specification": "规格（Mock档案）",
            "material": "材质（Mock档案）",
            "location": "位置（Mock档案）",
        },
    ),
    "production_areas": (
        "生产区域（Mock）",
        {"code": "区域编号", "name": "区域名称", "description": "区域说明"},
    ),
    "equipment_schedules": (
        "设备排产（Mock）",
        {
            "equipment_id": "设备编号",
            "product_code": "产品编号",
            "product_name": "产品名称",
            "batch_no": "批号",
            "start_at": "开始时间",
            "end_at": "结束时间",
            "task_name": "任务",
            "status": "状态",
        },
    ),
}


def contract_ref(provider):
    return "urn:report:mock:" + provider + ":1"


def builtin_contract(ref):
    from app.config import settings
    from app.services.reporting.section_narrative import CUSTOM_PROMPT_REF
    from app.services.reporting.template_preparation import profile

    if ref == CUSTOM_PROMPT_REF:
        contract = deepcopy(builtin_contract(PROMPT_REF))
        contract.update(contract_id=ref, family_id="自定义章节行文（AI 草稿）")
        contract["definition"].update(allow_custom_text=True, draft_only=True)
        contract["definition_hash"] = evidence_hash(contract["definition"])
        return contract
    if ref == PROMPT_REF:
        return profile(
            ref,
            "prompt",
            "引用式报告行文",
            {
                "model": settings.local_llm_model,
                "tokenizer_ref": "local-configured",
                "temperature": 0,
                "max_input_tokens": 16384,
                "max_output_tokens": 2048,
                "timeout_s": 120,
                "require_review": True,
                "allowed_texts": [
                    "",
                    " ",
                    "\n",
                    "，",
                    "。",
                    "；",
                    "：",
                    "、",
                    "原文记载：",
                    "根据已提供的材料，",
                    "设备信息：",
                    "外部演示数据：",
                    "相关信息如下：",
                    "产品：",
                    "剂型：",
                    "给药途径：",
                    "批量下限：",
                    "批量上限：",
                    "生产用途：",
                ],
            },
        )
    for provider, (label, fields) in PROVIDERS.items():
        if ref == contract_ref(provider):
            return profile(
                ref,
                "parameter",
                label,
                {
                    "output_type": {
                        "kind": "list",
                        "item_identity": "entity_id",
                        "item_type": {
                            "kind": "record",
                            "fields": {
                                key: {"type": {"kind": "string"}, "semantic_ref": ref + "/" + key}
                                for key in fields
                            },
                        },
                    },
                    "description": label,
                    "allowed_roles": ["senior_analyst"],
                    "applicable_scope": {"purpose": "demonstration"},
                },
            )
    return None


def validate_record_source(config):
    if config.contract_ref != contract_ref(config.provider):
        raise ReportingError("SOURCE_CONTRACT_MISMATCH")
    allowed = set(PROVIDERS[config.provider][1])
    if config.provider == "equipment":
        allowed -= {"specification", "material", "location"}
    if config.provider == "equipment_schedules":
        allowed |= {"start_date", "end_date"}
    if (set(config.filters) | set(config.input_filters)) - allowed:
        raise ReportingError("MOCK_FILTER_INVALID")
    if set(config.filters) & set(config.input_filters):
        raise ReportingError("MOCK_FILTER_CONFLICT")
    if config.provider == "equipment_schedules":
        if not {"equipment_id", "product_code"} <= (
            set(config.filters) | set(config.input_filters)
        ) or not {"start_date", "end_date"} <= set(config.filters):
            raise ReportingError("MOCK_SCHEDULE_SCOPE_REQUIRED", "排产需明确设备、产品和起止日期")
        try:
            start, end = (
                date.fromisoformat(config.filters["start_date"]),
                date.fromisoformat(config.filters["end_date"]),
            )
        except (ValueError, TypeError) as exc:
            raise ReportingError("MOCK_SCHEDULE_SCOPE_INVALID", "排产日期须为有效日期") from exc
        if not 0 <= (end - start).days <= 62:
            raise ReportingError("MOCK_SCHEDULE_SCOPE_INVALID")


def load_mock(db, config, limit):
    validate_record_source(config)
    provider = config.provider

    def equipment_property(row, name):
        iri = "https://ontology.pharma-gmp.cn/slpra/equipment/" + name
        # Older Mock imports store these source columns without an ontology IRI.
        # This finite mapping belongs to the Mock contract, not graph identity.
        legacy_label = {
            "modelSpecification": "规格型号",
            "constructedOf": "主体材质",
            "locatedIn": "安装位置",
        }[name]
        values = []
        for prop in row.data_properties or []:
            value = prop.get("value")
            matches = prop.get("iri") == iri or (
                not prop.get("iri") and prop.get("label") == legacy_label
            )
            if matches and value is not None and value not in values:
                values.append(value)
        return values[0] if len(values) == 1 else values or None

    def select_rows(model, fields, order, *conditions):
        query = select(model).where(*conditions)
        for key, expected in config.filters.items():
            choices = expected if isinstance(expected, list) else [expected]
            query = query.where(fields[key].in_(choices))
        return db.scalars(query.order_by(order).limit(limit + 1)).all()

    if provider in {"assessment_team", "approver_team"}:
        rows = select_rows(
            MockTeamMember,
            {
                "name": MockTeamMember.name,
                "role": MockTeamMember.role_label,
                "department": MockTeamMember.department,
                "role_code": MockTeamMember.role_code,
            },
            MockTeamMember.role_code,
            MockTeamMember.team_type
            == ("assessment" if provider == "assessment_team" else "approver"),
        )
        values = [
            {
                "entity_id": str(r.id),
                "name": r.name,
                "role": r.role_label,
                "department": r.department,
                "role_code": r.role_code,
            }
            for r in rows
        ]
    elif provider == "production_areas":
        rows = select_rows(
            MockProductionArea,
            {
                "code": MockProductionArea.code,
                "name": MockProductionArea.label,
                "description": MockProductionArea.description,
            },
            MockProductionArea.code,
        )
        values = [
            {"entity_id": str(r.id), "code": r.code, "name": r.label, "description": r.description}
            for r in rows
        ]
    elif provider == "equipment":
        rows = select_rows(
            MockEquipment,
            {
                "equipment_id": MockEquipment.equipment_id,
                "name": MockEquipment.label,
                "workshop_code": MockEquipment.workshop_code,
            },
            MockEquipment.equipment_id,
        )
        values = [
            {
                "entity_id": str(r.id),
                "equipment_id": r.equipment_id,
                "name": r.label,
                "workshop_code": r.workshop_code,
                "specification": equipment_property(r, "modelSpecification"),
                "material": equipment_property(r, "constructedOf"),
                "location": equipment_property(r, "locatedIn"),
            }
            for r in rows
        ]
    else:
        from app.services.extraction.equipment_schedule_source import (
            apply_daily_product_overrides,
            generate_equipment_schedules,
        )

        filters = config.filters
        selected = filters.get("equipment_id", [])
        ids = selected if isinstance(selected, list) else [selected]
        start, end = (
            date.fromisoformat(filters["start_date"]),
            date.fromisoformat(filters["end_date"]),
        )
        query = select(MockEquipment)
        if ids:
            query = query.where(MockEquipment.equipment_id.in_(ids))
        equipment = db.scalars(query.order_by(MockEquipment.equipment_id).limit(limit + 1)).all()
        if len(equipment) > limit:
            raise ReportingError("SOURCE_RECORD_BUDGET_EXCEEDED")
        values = []
        for item in equipment:
            args = {
                "equipment_id": item.equipment_id,
                "equipment_name": item.label,
                "workshop_code": item.workshop_code,
            }
            rows = generate_equipment_schedules(**args, start_date=start, end_date=end)
            overrides = db.scalars(
                select(MockEquipmentScheduleOverride).where(
                    MockEquipmentScheduleOverride.equipment_id == item.equipment_id,
                    MockEquipmentScheduleOverride.schedule_date >= start,
                    MockEquipmentScheduleOverride.schedule_date <= end,
                )
            ).all()
            rows = apply_daily_product_overrides(
                rows,
                **args,
                now=datetime.now(timezone.utc),
                overrides=[(r.schedule_date, r.product_code) for r in overrides],
            )
            for row in rows:
                if row["start_at"].date() <= end and row["end_at"].date() > start:
                    values.append(
                        {
                            "entity_id": row["id"],
                            **{
                                key: (
                                    row[key].isoformat()
                                    if isinstance(row.get(key), datetime)
                                    else row.get(key)
                                )
                                for key in PROVIDERS[provider][1]
                            },
                        }
                    )
    for key, expected in config.filters.items():
        if key not in {"start_date", "end_date"}:
            allowed = expected if isinstance(expected, list) else [expected]
            values = [v for v in values if v.get(key) in allowed]
    if len(values) > limit:
        raise ReportingError("SOURCE_RECORD_BUDGET_EXCEEDED")
    digest = evidence_hash([provider, values, config])
    return {
        "kind": "mock",
        "record_id": "mock:" + digest,
        "contract_id": config.contract_ref,
        "state": "ready",
        "values": values,
        "filters": config.filters,
        "input_filters": {k: v.model_dump(mode="json") for k, v in config.input_filters.items()},
        "provenance": {
            "kind": "mock",
            "provider": provider,
            "record_hash": digest,
            "reviewed": False,
            "filters": config.filters,
        },
    }


def resolve_mock(binding, record, inputs, typ, scope_id):
    values = deepcopy(record["values"])
    if binding.scope.subject_ref or binding.scope.applicable_at:
        return unavailable_value(binding.binding_id, typ, "unavailable", "MOCK_SCOPE_UNSUPPORTED")
    selected_filters = {}
    for key, ref in record.get("input_filters", {}).items():
        source = inputs.get(ref["input_id"])
        if source is None:
            return unavailable_value(
                binding.binding_id, typ, "missing", "MOCK_FILTER_INPUT_MISSING"
            )
        try:
            raw = business_value(source, ref.get("field_path", []))
        except ReportingError:
            return unavailable_value(
                binding.binding_id, typ, "missing", "MOCK_FILTER_INPUT_MISSING"
            )
        choices = raw if isinstance(raw, list) else [raw]
        if any(not isinstance(value, str) for value in choices) or not choices:
            return unavailable_value(
                binding.binding_id, typ, "invalid", "MOCK_FILTER_INPUT_INVALID"
            )
        selected_filters[key] = choices
        values = [v for v in values if v.get(key) in choices]
    result = typed_value(
        binding.binding_id,
        typ,
        values,
        scope=scope_id,
        provenance=[{**record["provenance"], "input_filters": selected_filters}],
    )
    if not values:
        result.issues.append(issue("missing", "MOCK_NO_MATCH", binding.binding_id))
    result.derivation.update(source_kind="mock", reviewed=False)
    return summarize(result)


def load_finder(db, engine, template, job, actor, selected):
    from app.services.template_finder.service import FinderService, source

    source(db, template.id, job.id)
    if (job.source_config or {}).get("document_role") in {
        "template_sample",
        "training_source",
        "training_report",
    }:
        raise ReportingError("SOURCE_ROLE_INVALID")
    service = FinderService(db, engine)
    status = service.status(actor, template.id, job.id)
    execution = status["execution_id"]
    requested = selected.get("finder_execution_id")
    if requested and requested != execution:
        raise ReportingError("SOURCE_VERSION_CHANGED", "本体指引1.0结果已更新，请刷新", status=409)
    result = {
        "kind": "finder_demo",
        "job_id": str(job.id),
        "template_id": str(template.id),
        "root_entity_id": "finder-source:" + str(job.id),
        "root_class_iri": template.iri_pattern,
        "execution_id": execution or "not-started",
        "source_filename": job.source_filename,
        "state": "unavailable",
        "relationships": [],
        "source_hash": None,
        "model_compatibility": [],
    }
    if selected.get("snapshot_id") or selected.get("root_entity_id") not in {
        None,
        result["root_entity_id"],
    }:
        raise ReportingError("SOURCE_REFERENCE_MISMATCH")
    if not status["has_result"] or status["stale"]:
        result["code"] = "FINDER_RESULT_STALE" if status["stale"] else "FINDER_RESULT_UNAVAILABLE"
        return result
    graph = service.result(actor, template.id, job.id, execution, "graph")
    result.update(
        state="ready",
        relationships=graph["relationships"],
        input=graph["input"],
        source_hash=evidence_hash(graph),
        source_role="analysis_source",
    )
    return result
