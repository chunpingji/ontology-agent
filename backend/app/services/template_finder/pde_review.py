"""PDE comparison and current-execution review for the requested HRS-1597 document.

This is a Finder display calculation, not evidence approval or fact submission.
Only parameters located on the same shared-line endpoint are used. OEB comparison
uses the numerical point band: unknown special hazards do not create a PDE conflict.
"""

import math
import re
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.extraction import AnnotationExecution, ExtractionJob
from app.models.pde_conflict import PdeConflictDecision
from app.services.reasoning.derivation import (
    DEFAULT_METHOD,
    ToxStudy,
    _band_from_oel,
    derive_facts,
)
from app.services.reasoning.pde_conflict import CONFLICT_KEY

from .policy import error, resolve

TARGET_FILENAME = "HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx"
REVIEW_VERSION = "hrs1597-pde-comparison-v1"
DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
ROOT = DEV + "CMCReport"
SHARED = DEV + "SharedLineAssessmentData"
_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
_SPECIES = (
    ("小鼠", "mouse"), ("大鼠", "rat"), ("wistar", "rat"), ("rat", "rat"),
    ("比格", "dog"), ("beagle", "dog"), ("犬", "dog"), ("dog", "dog"),
    ("家兔", "rabbit"), ("兔", "rabbit"), ("rabbit", "rabbit"),
    ("猕猴", "monkey"), ("食蟹猴", "monkey"), ("猴", "monkey"), ("monkey", "monkey"),
    ("mouse", "mouse"), ("人", "human"), ("human", "human"),
)


def _raw(prop):
    source = prop.get("source") or {}
    return str(source.get("raw_value") if source.get("raw_value") is not None else
               prop.get("raw_value") if prop.get("raw_value") is not None else
               prop.get("value", "")).strip()


def _number(prop, *, dose=False, pde=False):
    raw = _raw(prop)
    match = re.fullmatch(rf"\s*({_NUMBER})\s*(.*?)\s*", raw)
    if not match:
        raise ValueError("须为明确的正数，不能使用占位符、范围或约数")
    value, unit = float(match[1]), match[2].lower().replace(" ", "")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("须为大于零的有限数值")
    if dose or pde:
        # A bare value may carry its unit in the typed property label, as in the
        # baseline NOAEL column. Record this explicit semantic source in provenance.
        if not unit:
            label = str(prop.get("label") or "")
            unit_match = re.search(
                r"(mg|µg|μg|ug|mcg|ng)\s*/\s*(?:kg\s*/\s*)?(?:天|日|day)",
                label, re.IGNORECASE,
            )
            unit = unit_match[0].lower().replace(" ", "") if unit_match else ""
        unit = unit.replace("天", "day").replace("日", "day")
        suffix = "/kg/day" if dose else "/day"
        units = {mass + suffix: factor for mass, factor in (
            ("mg", 1), ("µg", .001), ("μg", .001), ("ug", .001),
            ("mcg", .001), ("ng", .000001),
        )}
        if unit not in units:
            raise ValueError("缺少或无法确认剂量单位" if dose else "缺少或无法确认 PDE 单位")
        value *= units[unit]
        if not math.isfinite(value) or value <= 0:
            raise ValueError("剂量换算超出可计算范围")
    elif unit:
        raise ValueError("修正因子须为无单位正数")
    return value


def _comparison(node):
    props, issues = {}, []
    for prop in node.get("object_data_properties", []):
        iri = str(prop.get("iri") or "")
        if not iri.startswith(DEV) and iri != (
            "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day"
        ):
            continue
        name = iri.rsplit("/", 1)[-1]
        props.setdefault(name, []).append(prop)

    def one(name, label, required=False):
        values = props.get(name, [])
        if not values:
            if required:
                issues.append(f"缺少{label}")
            return None
        if len({_raw(prop) for prop in values}) != 1:
            issues.append(f"{label}存在多个不同值")
            return None
        prop = values[0]
        if not (prop.get("source") or {}).get("anchors"):
            issues.append(f"{label}未定位到原文")
            return None
        return prop

    noael = one("noael_mg_per_kg_per_day", "NOAEL", True)
    asserted = one("pde_mg_per_day", "原文 PDE", True)
    study = one("studyType", "试验项目")
    species_prop = one("noaelSpecies", "动物种属")
    duration_prop = one("noaelDuration", "试验周期")
    species_text = _raw(species_prop or study) if species_prop or study else ""
    duration_text = _raw(duration_prop or study) if duration_prop or study else ""
    species = next((key for name, key in _SPECIES if (
        re.search(rf"\b{re.escape(name)}\b", species_text, re.IGNORECASE)
        if name.isascii() else name in species_text
    )), None)
    duration_match = re.search(r"(?<![\d.])(\d+)\s*(?:天|日|days?)(?![a-z])", duration_text)
    duration = int(duration_match[1]) if duration_match else None
    if species is None:
        issues.append("缺少可确认的动物种属")
    if not duration:
        issues.append("缺少明确的试验周期及单位")
    values, factor_props = {}, {}
    for name, prop, flags in (("noael", noael, {"dose": True}),
                              ("asserted", asserted, {"pde": True})):
        if prop:
            try:
                values[name] = _number(prop, **flags)
            except ValueError as exc:
                issues.append(f"{prop.get('label') or name}：{exc}")
    for index in range(1, 6):
        prop = one(f"f{index}", f"F{index}")
        if prop:
            try:
                values[f"f{index}"] = _number(prop)
                factor_props[f"f{index}"] = prop
            except ValueError as exc:
                issues.append(f"F{index}：{exc}")
    if issues:
        return None, list(dict.fromkeys(issues))

    result = derive_facts(ToxStudy(
        noael_mg_kg_day=values["noael"], species=species, study_duration_days=duration,
        # F4 is a divisor, not evidence that any special-hazard endpoint is negative.
        genotoxic=None, carcinogenic=None, reproductive_toxicant=None,
    ))
    provenance = deepcopy(result.provenance)
    factors = provenance["factors"]
    keys = ("F1_interspecies", "F2_intraindividual", "F3_duration", "F4_severity", "F5_loael")
    for index, key in enumerate(keys, 1):
        factors[key] = values.get(f"f{index}", factors[key])
    denominator = math.prod(factors[key] for key in keys)
    if not math.isfinite(denominator) or denominator <= 0:
        return None, ["修正因子乘积超出可计算范围"]
    derived_mg = values["noael"] * factors["BW_kg"] / denominator
    derived_ug, asserted_ug = derived_mg * 1000, values["asserted"] * 1000
    derived_oel = derived_ug / DEFAULT_METHOD.breathing_volume_m3
    if not all(math.isfinite(value) and value > 0 for value in (
        derived_mg, derived_ug, asserted_ug, derived_oel,
    )):
        return None, ["PDE 剂量换算超出可计算范围"]
    derived_band = _band_from_oel(derived_oel, DEFAULT_METHOD)
    asserted_band = _band_from_oel(
        asserted_ug / DEFAULT_METHOD.breathing_volume_m3, DEFAULT_METHOD,
    )
    ratio = max(derived_ug, asserted_ug) / min(derived_ug, asserted_ug)
    if not math.isfinite(ratio):
        return None, ["PDE 比值超出可计算范围"]
    if derived_band == asserted_band and ratio <= 2:
        return None, []
    factors["composite_UF"] = denominator
    provenance["intermediate"] = {"pde_ug_day": derived_ug, "oel_ug_m3": derived_oel}
    provenance["bands"] = {"point": derived_band, "hazard_floor": None, "recommended": None}
    provenance["provisional"]["reasons"] = [
        "特殊危害资料尚不完整；本卡仅比较 PDE 计算数值及其 OEB 分档，不作危害等级建议。"
    ]
    provenance["calculation_profile"] = REVIEW_VERSION
    provenance["factor_sources"] = {
        **{key: "document" if f"f{i}" in factor_props else
           "species_table" if i == 1 else "duration_table" if i == 3 else "method_default"
           for i, key in enumerate(keys, 1)},
        "BW_kg": "method_default", "breathing_volume_m3": "method_default",
    }
    provenance["input_evidence"] = {
        name: {"value": _raw(prop), "label": prop.get("label"), "source": prop["source"]}
        for name, prop in {"noael": noael, "asserted_pde": asserted,
                           "species": species_prop or study, "duration": duration_prop or study,
                           **factor_props}.items() if prop
    }
    provenance["limitations"] = "按 PDE 数值分档；特殊危害仍未知，F4=1不代表毒性阴性。"
    delta = abs(derived_band - asserted_band)
    summary = (
        f"推导 PDE {derived_mg:g} mg/日与原文 PDE {values['asserted']:g} mg/日"
        f"相差 {ratio:.1f} 倍（阈值 2），"
        + (f"OEB 相差 {delta} 个等级，" if delta else f"同属 band {asserted_band}，")
        + "计算过程存在差异，需人工复核。"
    )
    return {
        "conflict_key": CONFLICT_KEY,
        "asserted": {"pde_mg_day": values["asserted"], "pde_ug_day": asserted_ug,
                     "band": asserted_band},
        "derived": {"band": derived_band, "band_point": derived_band,
                    "pde_ug_day": derived_ug, "oel_ug_m3": derived_oel,
                    "provisional": result.provisional, "input_source": "extracted",
                    "provenance": provenance},
        "delta_bands": delta, "pde_ratio": ratio, "summary": summary,
    }, []


def attach_comparisons(graph, filename):
    if filename != TARGET_FILENAME or graph.get("doc_class", {}).get("doc_class_iri") != ROOT:
        return
    for node in graph.get("relationships", []):
        if node.get("object_class_iri") != SHARED:
            continue
        conflict, issues = _comparison(node)
        if conflict:
            node["conflict"] = conflict
        if issues:
            node["pde_review_issues"] = issues


def _output(job_id, row, version):
    matches = row is not None and row.version == version
    return {
        "job_id": job_id, "conflict_key": CONFLICT_KEY,
        "chosen": row.chosen if matches else "pending",
        "note": row.note if matches else "", "actor": row.actor if matches else "",
        "version": row.version if row else 0, "decided_at": row.decided_at if matches else None,
    }


def review(db, owner, template_id, source_job_id, execution_id, *, body=None, engine=None):
    """Read cached conflicts or CAS-review them; GET never parses, dispatches or writes."""
    from .runner import freeze_ontology
    from .service import FinderService, _input, private_job_id, source

    template, job = source(db, template_id, source_job_id)
    if job.source_filename != TARGET_FILENAME or template.iri_pattern != ROOT:
        raise error("PDE_CONFLICT_UNAVAILABLE", "此文档未启用 PDE 冲突审核")
    binding = resolve(template)
    if binding is None:
        raise error("RECOGNITION_MODE_MISMATCH", "该模板未启用本体指引1.0")
    private_id = private_job_id(owner, template_id, source_job_id)
    try:
        if body is not None:
            # The same private job lock is taken by FinderService.start: a restart
            # cannot replace this execution between validation and saving a decision.
            db.scalar(select(ExtractionJob).where(ExtractionJob.id == private_id).with_for_update())
        service = FinderService(db, engine)
        head = service._head(owner, template_id, source_job_id)
        try:
            payload = service._cache(head, str(execution_id)) if head else None
        except (OSError, ValueError, KeyError, TypeError):
            payload = None
        if not payload:
            raise error("RESULT_UNAVAILABLE", "执行已更新或尚未完成，请刷新后重试")
        if not any(node.get("conflict") for node in payload["graph"]["relationships"]):
            raise error("PDE_CONFLICT_UNAVAILABLE", "本次识别没有可审核的 PDE 冲突")
        row = db.scalar(select(PdeConflictDecision).where(
            PdeConflictDecision.job_id == source_job_id,
            PdeConflictDecision.conflict_key == CONFLICT_KEY,
        ).execution_options(populate_existing=True))
        if body is None:
            return _output(source_job_id, row, head.options.get("pde_decision_version"))
        if _input(template, job, binding, freeze_ontology(engine, binding)) != payload["input"]:
            raise error("INPUT_VERSION_CONFLICT", "原件或模板配置已变化，请重新识别后审核")
        version = row.version if row else 0
        if body.expected_version != version:
            raise error("PDE_DECISION_VERSION_CONFLICT", "审核版本已变化，请刷新后重试")
        stamp = datetime.now(UTC)
        values = {"chosen": body.chosen, "note": body.note, "actor": owner,
                  "version": version + 1, "decided_at": stamp}
        if row is None:
            row = PdeConflictDecision(job_id=source_job_id, conflict_key=CONFLICT_KEY, **values)
            db.add(row)
            db.flush()
        else:
            saved = db.execute(update(PdeConflictDecision).where(
                PdeConflictDecision.id == row.id, PdeConflictDecision.version == version,
            ).values(**values).execution_options(synchronize_session=False))
            if saved.rowcount != 1:
                raise error("PDE_DECISION_VERSION_CONFLICT", "审核版本已变化，请刷新后重试")
        options = {**head.options, "pde_decision_version": version + 1}
        saved_head = db.execute(update(AnnotationExecution).where(
            AnnotationExecution.job_id == private_id,
            AnnotationExecution.run_id == head.run_id,
            AnnotationExecution.status == "completed",
        ).values(options=options).execution_options(synchronize_session=False))
        if saved_head.rowcount != 1:
            raise error("RESULT_UNAVAILABLE", "执行已更新，请刷新后重试")
        db.commit()
        db.refresh(row)
        return _output(source_job_id, row, version + 1)
    except IntegrityError as exc:
        db.rollback()
        raise error("PDE_DECISION_VERSION_CONFLICT", "审核版本已变化，请刷新后重试") from exc
    except BaseException:
        if body is not None:
            db.rollback()
        raise
