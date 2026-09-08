"""Versioned deterministic PDE verification over explicitly bound evidence.

The method is an application calculation contract, not a toxicologist's approval.
No label/title matching, LLM arithmetic, or cross-study parameter enrichment.
"""

import re
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext

from app.schemas.evidence import Candidate
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.literal_normalizer import unit_definition
from app.services.ontology_instance_writer import instance_iri

DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
PDE_METHOD_REF = "urn:calculation:pde-check:1.0.0"


def calculation_contract():
    """A bundled immutable contract; changing semantics requires a new version."""
    definition = {
        "executor": "pde-check-v1",
        "version": "1.0.0",
        "title": "PDE 计算校验",
        "root_class_iri": DEV + "CMCReport",
        "subject_class_iri": DEV + "SharedLineAssessmentData",
        "predicate_path": [DEV + "hasSharedLineData"],
        "formula": "PDE (mg/day) = NOAEL (mg/kg/day) × BW (kg) / (F1 × F2 × F3 × F4 × F5 × MF)",
        "parameters": {
            "noael": {"predicate_iri": DEV + "noael_mg_per_kg_per_day", "unit": "mg/kg/day"},
            "asserted_pde": {"predicate_iri": DEV + "pde_mg_per_day", "unit": "mg/day"},
            "species": {"predicate_iri": DEV + "noaelSpecies", "type": "species"},
            "duration": {"predicate_iri": DEV + "noaelDuration", "type": "duration"},
            **{f"f{i}": {"predicate_iri": DEV + f"f{i}", "unit": None} for i in range(1, 6)},
            "asserted_oeb": {"predicate_iri": DEV + "oebBand", "type": "oeb"},
        },
        "defaults": {"bw": "50", "f2": "10", "f4": "1", "f5": "1", "mf": "1"},
        "species_factors": {
            "human": "1",
            "monkey": "2",
            "dog": "2",
            "rabbit": "2.5",
            "rat": "5",
            "mouse": "12",
        },
        "duration_factors": [[28, "10"], [90, "5"], [180, "2"]],
        "chronic_factor": "1",
        "breathing_volume_m3": "10",
        "oel_cutoffs": [["1000", 1], ["100", 2], ["10", 3], ["1", 4]],
        "ratio_threshold": "2",
        "limitations": (
            "OEB 为按 PDE 换算的数值分档，特殊危害须单独评估。F4=1 不代表遗传、生殖或致癌毒性阴性。"
        ),
        "output_type": {
            "kind": "list",
            "item_identity": "entity_id",
            "item_type": {
                "kind": "record",
                "fields": {
                    **{
                        name: {
                            "type": {
                                "kind": "quantity",
                                "unit": "mg/day",
                                "dimension": "mass/time",
                            },
                            "semantic_ref": PDE_METHOD_REF + "/" + name,
                        }
                        for name in ("asserted_pde", "derived_pde", "effective_pde")
                    },
                    "status": {
                        "type": {"kind": "string"},
                        "semantic_ref": PDE_METHOD_REF + "/status",
                    },
                },
            },
        },
    }
    return {
        "contract_id": PDE_METHOD_REF,
        "family_id": "urn:calculation:pde-check",
        "kind": "calculation",
        "revision_no": 1,
        "status": "published",
        "is_disabled": False,
        "definition": definition,
        "definition_hash": evidence_hash(definition),
        "decision_refs": [],
        "origin": "bundled_executor",
    }


def builtin_contract(ref):
    return calculation_contract() if ref == PDE_METHOD_REF else None


def default_checks(slots):
    from app.services.reasoning.rule_service import required_checks

    return required_checks(slots)


def _number(literal, unit):
    if literal.kind != "number" or literal.operator != "eq":
        raise ValueError("需要确定数值，不能使用占位符、范围或约数")
    value = Decimal(literal.normalized_value)
    if not value.is_finite() or value <= 0:
        raise ValueError("参数必须是大于零的有限数值")
    if abs(value.adjusted()) > 100 or len(value.as_tuple().digits) > 100:
        raise ValueError("参数超过当前计算方法支持的数值精度或范围")
    actual = literal.canonical_unit
    if unit:
        if not actual:
            raise ValueError("缺少明确单位")
        dimension, scale = unit_definition(actual)
        target_dimension, target_scale = unit_definition(unit)
        if dimension != target_dimension:
            raise ValueError("单位量纲不匹配")
        ratio = scale / target_scale
        value = value * Decimal(ratio.numerator) / Decimal(ratio.denominator)
    elif actual:
        raise ValueError("修正因子必须无量纲")
    return value


def _text_value(literal, kind):
    raw = str(literal.normalized_value or literal.raw_value).strip()
    if kind == "species":
        aliases = {
            "rat": "rat",
            "大鼠": "rat",
            "SD大鼠": "rat",
            "SD 大鼠": "rat",
            "Wistar大鼠": "rat",
            "mouse": "mouse",
            "小鼠": "mouse",
            "dog": "dog",
            "犬": "dog",
            "比格犬": "dog",
            "Beagle犬": "dog",
            "rabbit": "rabbit",
            "兔": "rabbit",
            "家兔": "rabbit",
            "monkey": "monkey",
            "猴": "monkey",
            "猕猴": "monkey",
            "食蟹猴": "monkey",
            "human": "human",
            "人": "human",
        }
        match = {k.casefold(): v for k, v in aliases.items()}.get(raw.casefold())
        if not match:
            raise ValueError("种属不在当前方法的明确映射中")
        return match
    if kind == "duration":
        match = re.fullmatch(r"(\d+)\s*(?:天|日|days?|d)(?:重复给药)?", raw, re.I)
        if not match or int(match[1]) <= 0:
            raise ValueError("试验周期需要明确的天数及单位")
        return int(match[1])
    match = re.fullmatch(r"(?:OEB\s*)?([1-5])", raw, re.I)
    if not match:
        raise ValueError("OEB 需要明确的 1 至 5 级")
    return int(match[1])


def _eligible(candidate):
    return candidate.positive_eligible and candidate.review_status == "confirmed"


def evaluate_candidates(candidates, *, contract=None, root_entity_id=None):
    """Historical public entry point, using the shared rule catalog."""
    from app.services.reasoning.rule_service import evaluate

    return evaluate(candidates, contract=contract, root_entity_id=root_entity_id)


def _evaluate_candidates(candidates, *, contract=None, root_entity_id=None):
    contract = deepcopy(contract or calculation_contract())
    if contract != calculation_contract():
        raise ValueError("计算方法内容或版本不匹配")
    method = contract["definition"]
    candidates = [
        c if isinstance(c, Candidate) else Candidate.model_validate(c) for c in candidates
    ]
    entities = {c.candidate_id: c for c in candidates if c.kind == "entity"}
    roots = {
        c.candidate_id
        for c in entities.values()
        if c.class_iri == method["root_class_iri"]
        and _eligible(c)
        and (root_entity_id is None or instance_iri(c) == root_entity_id)
    }
    relations = [
        c
        for c in candidates
        if c.kind == "relationship"
        and _eligible(c)
        and c.predicate_iri == method["predicate_path"][0]
        and c.subject.candidate_id in roots
        and c.subject.revision == entities[c.subject.candidate_id].revision
    ]
    linked = {
        c.object.candidate_id
        for c in relations
        if c.object.candidate_id in entities
        and c.object.revision == entities[c.object.candidate_id].revision
    }
    properties = defaultdict(list)
    for candidate in candidates:
        if candidate.kind == "property":
            properties[candidate.subject.candidate_id].append(candidate)
    instances = {c.candidate_id: instance_iri(c) for c in entities.values()}
    instance_counts = Counter(instances[c.candidate_id] for c in entities.values() if _eligible(c))
    results = []
    for entity in sorted(entities.values(), key=lambda c: c.candidate_id):
        if entity.class_iri != method["subject_class_iri"] or not _eligible(entity):
            continue
        # Each candidate identity is a study. Duplicate source labels are never joined.
        props = properties[entity.candidate_id]
        with localcontext() as context:
            context.prec = 40
            from app.services.reasoning.rule_service import entity_result

            result = entity_result(entity, props, method, _evaluate_entity)
        result.update(
            contract_ref=contract["contract_id"],
            method_hash=contract["definition_hash"],
            method_version=method["version"],
            formula=method["formula"],
            limitations=method["limitations"],
            subject_candidate_id=entity.candidate_id,
            subject_revision=entity.revision,
            subject_iri=instances[entity.candidate_id],
            subject_label=entity.text,
        )
        if instance_counts[instances[entity.candidate_id]] > 1:
            result["status"] = "incomplete"
            result["issues"].append(
                {"parameter": "subject", "message": "同一实例存在重复实体记录，请先消除歧义"}
            )
        result["calculation_id"] = evidence_hash(result)
        result["linked_to_source"] = entity.candidate_id in linked
        result["relation_refs"] = [
            stable_id("assertion", [c.candidate_id, c.revision])
            for c in relations
            if c.object.candidate_id == entity.candidate_id
        ]
        results.append(result)
    return results


def _evaluate_entity(entity, props, method):
    inputs, evidence, issues, factors = {}, {}, [], {}
    for name, parameter in method["parameters"].items():
        matching = [p for p in props if p.predicate_iri == parameter["predicate_iri"]]
        selected = [p for p in matching if p.review_status != "rejected"]
        evidence[name] = [
            {
                "candidate_id": p.candidate_id,
                "revision": p.revision,
                "fact_ref": stable_id("assertion", [p.candidate_id, p.revision]),
                "literal": p.literal.model_dump(mode="json"),
                "provenance": [v.model_dump(mode="json") for v in p.provenance],
                "bindings": [v.model_dump(mode="json") for v in p.bindings],
            }
            for p in sorted(selected or matching, key=lambda c: c.candidate_id)
        ]
        if not selected:
            if matching:
                issues.append(
                    {
                        "parameter": name,
                        "message": "该参数已被拒绝，不能回退方法默认值；请补充有效证据",
                    }
                )
            continue
        values = []
        try:
            for p in selected:
                if not _eligible(p) or p.subject.revision != entity.revision:
                    raise ValueError("参数未通过审核、绑定已过期或断言不是确定事实")
                values.append(
                    _text_value(p.literal, parameter["type"])
                    if "type" in parameter
                    else _number(p.literal, parameter["unit"])
                )
            if len(set(values)) != 1:
                raise ValueError("同一实体存在多个不同参数值")
            inputs[name] = values[0]
        except (ValueError, InvalidOperation) as exc:
            issues.append({"parameter": name, "message": str(exc)})
    for name in ("noael", "asserted_pde"):
        if name not in inputs:
            issues.append({"parameter": name, "message": "缺少可用于校验的数值及单位"})
    for name, value in method["defaults"].items():
        factors[name] = {
            "value": str(inputs.get(name, value)),
            "source": "document" if name in inputs else "method_default",
        }
    for name in ("f1", "f3"):
        if name in inputs:
            factors[name] = {"value": str(inputs[name]), "source": "document"}
        elif name == "f1" and "species" in inputs:
            factors[name] = {
                "value": method["species_factors"][inputs["species"]],
                "source": "species_table",
            }
        elif name == "f3" and "duration" in inputs:
            factors[name] = {
                "value": next(
                    (v for d, v in method["duration_factors"] if inputs["duration"] <= d),
                    method["chronic_factor"],
                ),
                "source": "duration_table",
            }
        else:
            issues.append({"parameter": name, "message": "缺少修正因子及推导它所需的种属或周期"})
    result = {
        "status": "incomplete" if issues else "passed",
        "issues": issues,
        "inputs": {k: str(v) for k, v in inputs.items()},
        "input_evidence": evidence,
        "factors": factors,
        "asserted_pde_mg_day": str(inputs["asserted_pde"]) if "asserted_pde" in inputs else None,
        "derived_pde_mg_day": None,
        "asserted_band": None,
        "derived_band": None,
        "ratio": None,
        "ratio_threshold": method["ratio_threshold"],
        "differences": [],
    }
    if issues:
        return result
    denominator = Decimal(1)
    for name in ("f1", "f2", "f3", "f4", "f5", "mf"):
        denominator *= Decimal(factors[name]["value"])
    calculated = inputs["noael"] * Decimal(factors["bw"]["value"]) / denominator
    asserted = inputs["asserted_pde"]

    def band(pde):
        oel = pde * 1000 / Decimal(method["breathing_volume_m3"])
        return next((b for threshold, b in method["oel_cutoffs"] if oel > Decimal(threshold)), 5)

    ratio = max(asserted, calculated) / min(asserted, calculated)
    differences = []
    if ratio > Decimal(method["ratio_threshold"]):
        differences.append("pde_ratio")
    asserted_band = band(asserted)
    if asserted_band != band(calculated):
        differences.append("oeb_band")
    if "asserted_oeb" in inputs and inputs["asserted_oeb"] != band(calculated):
        differences.append("oeb_assertion")
    result.update(
        status="conflict" if differences else "passed",
        derived_pde_mg_day=str(calculated),
        asserted_band=asserted_band,
        derived_band=band(calculated),
        ratio=str(ratio),
        differences=differences,
        denominator=str(denominator),
    )
    return result


def apply_decisions(results, decisions):
    """Only a decision for the exact inputs, subject and method may select a value."""
    output = deepcopy(results)
    for result in output:
        history = [
            d for d in decisions if d["subject_candidate_id"] == result["subject_candidate_id"]
        ]
        latest = max(history, key=lambda d: d["revision"], default=None)
        valid = latest and latest["calculation_id"] == result["calculation_id"]
        result["decision"] = latest if valid else None
        result["decision_revision"] = latest["revision"] if latest else 0
        result["stale_decision"] = bool(latest and not valid)
        result["effective_pde_mg_day"] = (
            result["derived_pde_mg_day"] if result["status"] == "passed" else None
        )
        result["effective_band"] = result["derived_band"] if result["status"] == "passed" else None
        result["review_status"] = "automatic" if result["status"] == "passed" else "pending"
        if valid:
            choice = latest["choice"]
            result["review_status"] = choice
            result["effective_pde_mg_day"] = (
                result.get(choice + "_pde_mg_day") if choice in {"derived", "asserted"} else None
            )
            result["effective_band"] = (
                result.get(choice + "_band") if choice in {"derived", "asserted"} else None
            )
        elif latest:
            result["review_status"] = "pending"
            result["effective_pde_mg_day"] = None
            result["effective_band"] = None
        result["blocks_conclusion"] = (
            result["effective_pde_mg_day"] is None or not result["linked_to_source"]
        )
    return output
