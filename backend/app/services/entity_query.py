"""Bounded, read-only entity lookup over current mapped Mock rows."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import selectinload

from app.models.ontology_meta import OntologyClass, OntologyClassMapping
from app.schemas.entity_query import EntityQueryRequest, MockQueryConfig
from app.services.entity_query_mapping import issue, mapping_issues, mapping_revision
from app.services.entity_query_schema import EntityQuerySchema, transform_value, valid_value
from app.services.integration.mock_entity_reader import (
    DATASETS,
    SOURCE_SYSTEM,
    MockEntityReader,
    is_iri,
    select_values,
)


class EntityQueryService:
    def __init__(self, db, engine):
        self.db, self.engine = db, engine
        self.reader = MockEntityReader(db)

    def _context(self):
        schema = EntityQuerySchema(self.db, self.engine)
        rows = (
            self.db.query(OntologyClassMapping, OntologyClass.slpra_iri)
            .join(
                OntologyClass,
                OntologyClass.id == OntologyClassMapping.class_id,
            )
            .filter(OntologyClassMapping.mapping_type == "mock_dataset")
            .options(
                selectinload(OntologyClassMapping.property_bindings),
            )
            .populate_existing()
            .all()
        )
        mappings = {}
        for mapping, class_iri in rows:
            bindings = list(mapping.property_bindings)
            mappings[mapping.id] = {
                "mapping": mapping,
                "class_iri": class_iri,
                "bindings": bindings,
                "issues": mapping_issues(schema, class_iri, mapping, bindings),
                "revision": mapping_revision(mapping, bindings),
            }
        return schema, mappings

    def _read(self, dataset: str, cache: dict):
        if dataset not in cache:
            try:
                rows, truncated = self.reader.read(dataset)
                cache[dataset] = (rows, truncated, [])
            except Exception:  # Source failures are deliberately opaque to API callers.
                cache[dataset] = ([], False, [issue("source_unavailable", "来源读取失败")])
        return cache[dataset]

    def capabilities(self, class_iris: list[str]) -> list[dict]:
        """Mapped query metadata only: no instance scanning or field catalogue reads."""
        with self.db.no_autoflush:
            schema, mappings = self._context()
            result = []
            for cls in sorted(set(class_iris)):
                if not schema.class_valid(cls):
                    continue
                for ctx in sorted(mappings.values(), key=lambda c: str(c["mapping"].id)):
                    m = ctx["mapping"]
                    if ctx["issues"] or not (
                        ctx["class_iri"] == cls
                        or (m.query_config.get("class_path")
                            and schema.subclass(cls, ctx["class_iri"]))
                    ):
                        continue
                    bound = {b.property_iri for b in ctx["bindings"]}
                    config = MockQueryConfig.model_validate(m.query_config)
                    result.append({
                        "id": f"L{len(result) + 1}", "mapping_id": str(m.id),
                        "mapping_revision": ctx["revision"], "class_iri": cls,
                        "class_label": schema.label(cls),
                        "identifier_namespace": config.identifier_namespace,
                        "lookup_key_groups": [g.model_dump() for g in config.lookup_key_groups],
                        "properties": [p for p in schema.properties(cls)
                                       if p["property_iri"] in bound and
                                       "http://www.w3.org/2001/XMLSchema#string"
                                       in p["datatype_iris"]],
                        **schema.identity_guidance(cls, bound),
                    })
            return result

    def sources(self) -> dict:
        with self.db.no_autoflush:
            schema, mappings = self._context()
            cache, sources = {}, []
            for dataset, spec in DATASETS.items():
                rows, truncated, errors = self._read(dataset, cache)
                source_mappings = []
                for ctx in mappings.values():
                    m = ctx["mapping"]
                    if m.target != dataset or m.source_system != SOURCE_SYSTEM:
                        continue
                    bound = {b.property_iri for b in ctx["bindings"]}
                    source_mappings.append(
                        {
                            "id": str(m.id),
                            "class_iri": ctx["class_iri"],
                            "class_label": schema.label(ctx["class_iri"]),
                            "mapping_revision": ctx["revision"],
                            "query_config": m.query_config,
                            "queryable": not ctx["issues"] and not errors,
                            "issues": ctx["issues"] + errors,
                            "properties": schema.properties(ctx["class_iri"]),
                            "mapped_property_iris": sorted(bound),
                            **schema.identity_guidance(ctx["class_iri"], bound),
                        }
                    )
                sources.append(
                    {
                        "source_system": SOURCE_SYSTEM,
                        "dataset": dataset,
                        "label": spec.label,
                        "source_kind": "mock",
                        "fields": self.reader.fields(dataset, rows),
                        "field_catalog_complete": not errors and not truncated,
                        "issues": errors
                        + ([issue("scan_limit", "字段目录扫描达到上限")] if truncated else []),
                        "mappings": sorted(source_mappings, key=lambda m: m["id"]),
                    }
                )
            templates = json.loads(
                (Path(__file__).parents[1] / "resources" / "mock_entity_mappings.json").read_text()
            )
            for template in templates:
                class_iri = template["class_iri"]
                template["available"] = schema.class_valid(class_iri) and all(
                    schema.property(class_iri, b["property_iri"])
                    for b in template["property_bindings"]
                )
            return {"sources": sources, "initial_mappings": templates}

    @staticmethod
    def _applicable(schema, q, ctx):
        cls, m = ctx["class_iri"], ctx["mapping"]
        dynamic = isinstance(m.query_config, dict) and m.query_config.get("class_path")
        return (
            cls == q.class_iri
            or (q.include_subclasses and schema.subclass(cls, q.class_iri))
            or (dynamic and schema.subclass(q.class_iri, cls))
        )

    def query(self, request: EntityQueryRequest) -> dict:
        with self.db.no_autoflush:
            schema, mappings = self._context()
            # Validate the whole request before any source query; never silently drop explicit IDs.
            selected = []
            for q in request.queries:
                if not schema.class_valid(q.class_iri):
                    raise HTTPException(422, detail="查询类型不存在或已停用")
                for f in q.property_filters:
                    p = schema.property(q.class_iri, f.property_iri)
                    if (
                        not p
                        or f.datatype_iri not in p["datatype_iris"]
                        or not valid_value(f.value, f.datatype_iri)
                    ):
                        raise HTTPException(422, detail="查询属性、定义域或数据类型非法")
                pool = []
                for mid in q.mapping_ids:
                    if mid not in mappings or (
                        not mappings[mid]["issues"]
                        and not self._applicable(schema, q, mappings[mid])
                    ):
                        raise HTTPException(422, detail="指定映射不存在或类型不相容")
                    pool.append(mappings[mid])
                if not q.mapping_ids:
                    pool = [c for c in mappings.values() if self._applicable(schema, q, c)]
                selected.append(pool)
            cache = {}
            results = [
                self._query_one(schema, q, pool, cache)
                for q, pool in zip(request.queries, selected, strict=True)
            ]
            return {"results": results}

    def _query_one(self, schema, q, pool, cache):
        candidates, sources, issues = [], [], []
        complete, truncated = bool(pool), False
        if not pool:
            issues.append(issue("no_queryable_mapping", "当前类型没有可查询映射"))
        # Count distinct source records, not mapping projections, for IRI collisions.
        iri_records = defaultdict(set)
        for ctx in sorted(pool, key=lambda c: str(c["mapping"].id)):
            m = ctx["mapping"]
            source = {"mapping_id": str(m.id), "status": "complete", "issues": []}
            sources.append(source)
            if ctx["issues"]:
                source.update(status="invalid_mapping", issues=ctx["issues"])
                complete = False
                continue
            bound = {b.property_iri for b in ctx["bindings"]}
            unsupported = [
                f.property_iri for f in q.property_filters if f.property_iri not in bound
            ]
            if unsupported:
                source.update(
                    status="unsupported_filter",
                    issues=[
                        issue("unsupported_filter", "来源尚未映射请求属性", property_iri=p)
                        for p in unsupported
                    ],
                )
                complete = False
                continue
            rows, scan_truncated, failures = self._read(m.target, cache)
            if failures:
                source.update(status="source_unavailable", issues=failures)
                complete = False
                continue
            config = MockQueryConfig.model_validate(m.query_config)
            if scan_truncated:
                source["issues"].append(issue("scan_limit", "来源扫描达到 5,000 行上限"))
                complete, truncated = False, True
            for row in rows:
                if config.entity_iri_path:
                    refs = select_values(row, config.entity_iri_path)
                    if len(refs) == 1 and isinstance(refs[0][0], str) and is_iri(refs[0][0]):
                        iri_records[refs[0][0]].add((m.target, str(row["id"])))
                candidate, row_issues, uncertain = self._row(schema, q, ctx, config, row)
                source["issues"].extend(row_issues)
                if uncertain:
                    complete = False
                    source["status"] = "incomplete"
                if candidate:
                    candidates.append(candidate)
            if scan_truncated:
                source["status"] = "incomplete"
        if pool and all(source["status"] == "invalid_mapping" for source in sources):
            issues.append(issue("no_queryable_mapping", "适用来源的映射均不可查询"))
        for candidate in candidates:
            if len(iri_records[candidate["source_entity_iri"]]) > 1:
                candidate["issues"].append(issue("source_iri_conflict", "多个来源记录声明相同 IRI"))
        candidates.sort(
            key=lambda c: (
                c["record_ref"]["source_system"],
                c["record_ref"]["dataset"],
                c["record_ref"]["record_id"],
                c["mapping_id"],
            )
        )
        count = len(candidates)
        total = count if complete else None
        outcome = "matches" if count else "no_match" if complete else "unresolved"
        page = candidates[q.offset : q.offset + q.limit]
        next_offset = q.offset + q.limit if q.offset + q.limit < count else None
        if len(page) < count:
            complete, truncated = False, True
            issues.append(issue("result_limit", "当前页未包含全部匹配结果"))
        return {
            "query_id": q.query_id,
            "outcome": outcome,
            "complete": complete,
            "truncated": truncated,
            "total": total,
            "next_offset": next_offset,
            "sources": sources,
            "candidates": page,
            "issues": issues,
        }

    def _row(self, schema, q, ctx, config, row):
        m, class_iri = ctx["mapping"], ctx["class_iri"]
        row_id = str(row["id"])
        diagnostics, uncertain = [], False

        def problem(code, message, prop=None, affects=False):
            nonlocal uncertain
            diagnostics.append(issue(code, message, record_id=row_id, property_iri=prop))
            uncertain |= affects

        if config.class_path:
            types = select_values(row, config.class_path)
            if (
                len(types) != 1
                or not isinstance(types[0][0], str)
                or not schema.class_valid(types[0][0])
                or not schema.subclass(types[0][0], class_iri)
            ):
                problem("invalid_record_type", "记录类型缺失、多值或不属于映射类", affects=True)
                return None, diagnostics, uncertain
            class_iri = types[0][0]
        if not (
            class_iri == q.class_iri
            or (q.include_subclasses and schema.subclass(class_iri, q.class_iri))
        ):
            return None, [], False
        labels = select_values(row, config.label_path)
        label = ""
        if len(labels) == 1 and isinstance(labels[0][0], str):
            label = labels[0][0]
        else:
            problem(
                "label_missing_or_multivalued", "记录名称缺失或多值", affects=q.name is not None
            )
        matches = []
        if q.name:
            hit = label == q.name.value if q.name.match == "exact" else q.name.value in label
            if not hit:
                return None, diagnostics, uncertain
            matches.append({"kind": f"name_{q.name.match}"})
        properties = {}
        queried = {f.property_iri for f in q.property_filters}
        for b in sorted(ctx["bindings"], key=lambda b: b.property_iri):
            p = schema.property(class_iri, b.property_iri)
            values = []
            raw_values = select_values(row, b.source_path)
            if not raw_values:
                problem(
                    "value_missing", "映射字段没有值", b.property_iri, b.property_iri in queried
                )
            for raw, index in raw_values:
                try:
                    value = transform_value(raw, b.transform_type, b.transform_config)
                    datatypes = p["datatype_iris"] if p else []
                    if b.transform_type == "cast":
                        datatypes = ["http://www.w3.org/2001/XMLSchema#" + b.transform_config["to"]]
                    datatype = next((dt for dt in datatypes if valid_value(value, dt)), None)
                    if datatype is None or type(raw) not in {str, bool, int, float}:
                        raise ValueError("invalid_data_value")
                except (ValueError, TypeError, KeyError, OverflowError):
                    problem(
                        "conversion_failed",
                        "字段值转换或数据类型核验失败",
                        b.property_iri,
                        b.property_iri in queried,
                    )
                    continue
                values.append(
                    {
                        "value": value,
                        "datatype_iri": datatype,
                        "raw_value": raw,
                        "source_path": b.source_path,
                        "source_index": index,
                    }
                )
            properties[b.property_iri] = values
        for f in q.property_filters:
            # JSON booleans are not numbers; a string identifier is never coerced.
            if not any(
                v["value"] == f.value
                and valid_value(v["value"], f.datatype_iri)
                and (
                    v["datatype_iri"] == f.datatype_iri
                    or {v["datatype_iri"], f.datatype_iri}
                    <= {
                        "http://www.w3.org/2001/XMLSchema#integer",
                        "http://www.w3.org/2001/XMLSchema#decimal",
                    }
                )
                for v in properties[f.property_iri]
            ):
                return None, diagnostics, uncertain
            matches.append({"kind": "property_exact", "property_iri": f.property_iri})
        failed_properties = {
            i["property_iri"]
            for i in diagnostics
            if i["code"] in {"conversion_failed", "value_missing"}
        }
        matched_groups = []
        for i, group in enumerate(config.lookup_key_groups):
            components = group.property_iris + group.scope_property_iris
            if all(
                p in queried and p not in failed_properties and len(properties.get(p, [])) == 1
                for p in components
            ):
                matched_groups.append(i)
        scope_provided = any(
            config.lookup_key_groups[i].scope_property_iris for i in matched_groups
        )
        source_iri = None
        if config.entity_iri_path:
            refs = select_values(row, config.entity_iri_path)
            if len(refs) == 1 and isinstance(refs[0][0], str) and is_iri(refs[0][0]):
                source_iri = refs[0][0]
            else:
                problem("invalid_source_iri", "来源实体 IRI 缺失、非法或多值")
        return (
            {
                "record_ref": {
                    "source_system": m.source_system,
                    "dataset": m.target,
                    "record_id": row_id,
                },
                "record_version": row["updated_at"].isoformat(),
                "mapping_id": str(m.id),
                "mapping_revision": ctx["revision"],
                "source_kind": "mock",
                "source_entity_iri": source_iri,
                "class_iri": class_iri,
                "label": label,
                "properties": [{"property_iri": p, "values": v} for p, v in properties.items()],
                "matches": matches,
                "matched_lookup_groups": matched_groups,
                "identifier_namespace": config.identifier_namespace,
                "business_scope_status": "provided" if scope_provided else "unspecified",
                "identity_status": "not_checked",
                "issues": list(diagnostics),
            },
            diagnostics,
            uncertain,
        )
