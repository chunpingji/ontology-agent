"""Validation shared by mapping writes, source catalog and query execution."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from app.schemas.entity_query import MockQueryConfig
from app.services.entity_query_schema import DATATYPES, EntityQuerySchema, valid_value
from app.services.integration.mock_entity_reader import (
    DATASETS,
    SOURCE_SYSTEM,
    is_iri,
    validate_path,
)


def issue(code: str, message: str, **details) -> dict:
    return {"code": code, "message": message, **details}


def binding_issues(schema: EntityQuerySchema, class_iri: str, dataset: str, binding) -> list[dict]:
    errors = []
    p = schema.property(class_iri, binding.property_iri)
    if binding.property_kind != "data" or p is None:
        errors.append(
            issue(
                "illegal_property",
                "属性不存在、已停用或定义域/属性种类不兼容",
                property_iri=binding.property_iri,
            )
        )
    elif not p["datatype_iris"]:
        errors.append(
            issue(
                "unsupported_datatype",
                "该属性的数据范围不受查询支持",
                property_iri=binding.property_iri,
            )
        )
    try:
        validate_path(dataset, binding.source_path)
    except ValueError as exc:
        errors.append(issue("invalid_source_path", str(exc)))
    kind, config = binding.transform_type, binding.transform_config or {}
    if kind == "none":
        if config:
            errors.append(issue("invalid_transform", "none 转换不可附带执行配置"))
    elif kind == "cast":
        target = "http://www.w3.org/2001/XMLSchema#" + str(config.get("to", ""))
        if set(config) != {"to"} or target not in DATATYPES:
            errors.append(issue("invalid_transform", "cast 需显式支持的 to 类型"))
        elif p and target not in p["datatype_iris"]:
            errors.append(issue("invalid_transform", "cast 目标与本体数据范围不兼容"))
    elif kind == "controlled_vocab":
        value_map = config.get("map")
        if (
            set(config) != {"map"}
            or not isinstance(value_map, dict)
            or not value_map
            or any(type(v) not in {str, int, float, bool} for v in value_map.values())
        ):
            errors.append(issue("invalid_transform", "仅支持显式、非空的标量 map 配置"))
        elif p and any(
            not any(valid_value(v, dt) for dt in p["datatype_iris"]) for v in value_map.values()
        ):
            errors.append(issue("invalid_transform", "显式 map 的目标值不符合本体数据范围"))
    else:
        errors.append(issue("invalid_transform", "不支持该转换；可用 none/cast/显式 map"))
    if any(
        getattr(binding, key, None)
        for key in (
            "object_resolution",
            "target_class_iri",
            "target_id_path",
            "nested_binding_id",
        )
    ):
        errors.append(issue("unsupported_object_mapping", "Mock 查询首期仅映射数据属性"))
    return errors


def mapping_issues(schema, class_iri: str, mapping, bindings) -> list[dict]:
    errors = []
    if not schema.class_valid(class_iri):
        errors.append(issue("illegal_class", "本体类不存在或已停用"))
    if mapping.source_system != SOURCE_SYSTEM or mapping.target not in DATASETS:
        return errors + [issue("invalid_source", "只允许目录内的 builtin_mock 数据集")]
    raw = mapping.query_config
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    try:
        config = MockQueryConfig.model_validate(raw or {})
    except ValidationError:
        return errors + [issue("invalid_query_config", "查询配置结构非法")]
    if not config.label_path:
        errors.append(issue("draft_label_path", "尚未配置显示名称字段"))
    for path in (config.label_path, config.entity_iri_path, config.class_path):
        if path is not None:
            try:
                validate_path(mapping.target, path)
            except ValueError as exc:
                errors.append(issue("invalid_source_path", str(exc)))
    if config.class_path and config.class_path in (config.label_path, config.entity_iri_path):
        errors.append(issue("conflicting_class_path", "类型字段不能同时用作名称或实体 IRI"))
    if config.identifier_namespace and not is_iri(config.identifier_namespace):
        errors.append(issue("invalid_namespace", "编号命名空间需为完整 IRI"))
    if config.lookup_key_groups and not config.identifier_namespace:
        errors.append(issue("draft_namespace", "来源查询键尚缺编号命名空间"))
    bound = set()
    for binding in bindings:
        errors.extend(binding_issues(schema, class_iri, mapping.target, binding))
        if binding.property_iri in bound:
            errors.append(issue("duplicate_property", "同一本体属性只能有一个字段绑定"))
        bound.add(binding.property_iri)
    for group in config.lookup_key_groups:
        for iri in group.property_iris + group.scope_property_iris:
            p = schema.property(class_iri, iri)
            if not p or not p["datatype_iris"]:
                errors.append(
                    issue(
                        "illegal_key_property", "键组属性非法或数据类型不受支持", property_iri=iri
                    )
                )
            elif iri not in bound:
                errors.append(
                    issue("draft_key_binding", "键组组件尚未绑定来源字段", property_iri=iri)
                )
    return errors


def mapping_revision(mapping, bindings) -> str:
    def fields(obj, names):
        return {name: getattr(obj, name, None) for name in names}

    state = fields(
        mapping,
        (
            "id",
            "class_id",
            "mapping_type",
            "source_system",
            "target",
            "query_config",
            "version",
            "status",
        ),
    )
    state["bindings"] = [
        fields(
            b,
            (
                "id",
                "property_iri",
                "property_kind",
                "source_path",
                "transform_type",
                "transform_config",
                "is_identifier",
                "is_label",
                "object_resolution",
                "target_class_iri",
                "target_id_path",
                "nested_binding_id",
                "version",
                "status",
            ),
        )
        for b in sorted(bindings, key=lambda b: str(b.id))
    ]
    return hashlib.sha256(
        json.dumps(
            state, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()
