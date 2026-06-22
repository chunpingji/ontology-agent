"""Extraction pipeline: parse -> extract -> align -> review queue.

各阶段经 progress 事件总线实时上报（R1, FR-002）；Word 正文条件式产出 Action 候选
（FR-005）；受控词表归一化注入（FR-006）；LLM 不可用回退结构化抽取且不失败（FR-007）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.services.extraction.aligner import align_entity
from app.services.extraction.db_reader import reflect_database
from app.services.extraction.llm_extractor import extract_with_fallback
from app.services.extraction.parser import parse_excel, parse_word
from app.services.extraction.progress import progress_bus
from app.services.extraction.vocabulary import (
    CONTROLLED_VOCAB,
    parse_action_from_text,
    tag_controlled_vocab,
)
from app.services.ontology_engine import OntologyEngine

logger = logging.getLogger(__name__)


def _emit(job: ExtractionJob, stage: str, pct: int, status: str,
          degraded: bool = False) -> None:
    progress_bus.publish(
        str(job.id),
        {"job_id": str(job.id), "stage": stage, "pct": pct,
         "status": status, "degraded": degraded},
    )


async def run_extraction_pipeline(
    job: ExtractionJob,
    config: ExtractionConfig,
    file_path: Path | None,
    engine: OntologyEngine,
    db: Session,
) -> ExtractionJob:
    """Run the full extraction pipeline for a job."""
    try:
        job.status = "parsing"
        db.commit()
        _emit(job, "parsing", 10, "parsing")

        source_ref = job.source_filename or config.source_type

        # 数据库源：只读结构反射→class/link 候选（仅入审核队列, FR-012）。
        if config.source_type == "database":
            return await _run_database_branch(job, config, source_ref, db)

        # Stage 1: Parse
        if config.source_type == "excel":
            raw_rows = parse_excel(file_path, column_mapping=config.column_mapping or {})
            word_sections = None
        elif config.source_type == "word":
            word_sections = parse_word(file_path)
            raw_rows = [s["content"] for s in word_sections if s.get("type") == "table_row"]
        else:
            raise ValueError(f"Unsupported source type: {config.source_type}")

        job.status = "extracting"
        db.commit()
        _emit(job, "extracting", 40, "extracting")

        # Stage 2: Extract — LLM 抽取并在不可用时回退（degraded）。
        # 受控词表注入抽取提示，在生成阶段约束取值（FR-006 / US1-AC3）。
        instances, degraded_reason = await extract_with_fallback(
            raw_rows, config.target_class_iri, property_schema=[],
            controlled_vocab=CONTROLLED_VOCAB,
        )
        degraded = degraded_reason is not None

        job.status = "aligning"
        db.commit()
        _emit(job, "aligning", 70, "aligning", degraded=degraded)

        # Stage 3: Align + persist instance candidates.
        id_prop = _find_id_property(config.column_mapping)
        label_prop = _find_label_property(config.column_mapping)
        total = 0
        instance_candidates: list[ExtractionCandidate] = []

        for entity_data in instances:
            props = tag_controlled_vocab(dict(entity_data))
            alignment = align_entity(
                candidate=props,
                target_class_iri=config.target_class_iri,
                engine=engine,
                id_property=id_prop,
                label_property=label_prop,
            )
            group_key = _compute_group_key(props, config.target_class_iri, id_prop)
            cand = ExtractionCandidate(
                job_id=job.id,
                target_class_iri=config.target_class_iri,
                extracted_properties=props,
                candidate_kind="instance",
                group_key=group_key,
                source_ref=source_ref,
                degraded_reason=degraded_reason,
                alignment_result=alignment.action,
                aligned_iri=alignment.match_iri,
                match_score=alignment.match_score,
                review_status="pending",
            )
            db.add(cand)
            instance_candidates.append(cand)
            total += 1

        # 跨源归组：每个 group_key 选一个规范实例（is_canonical），歧义不自动合并。
        _mark_canonical(instance_candidates)

        # Word 正文「若…则…必须…」→ Action 候选（FR-005）。
        if word_sections:
            for sec in word_sections:
                if sec.get("type") != "paragraph":
                    continue
                action = parse_action_from_text(sec.get("content", ""))
                if action:
                    db.add(ExtractionCandidate(
                        job_id=job.id,
                        target_class_iri=config.target_class_iri,
                        extracted_properties={"action": action["action"]},
                        candidate_kind="action",
                        action_conditions=action,
                        source_ref=source_ref,
                        degraded_reason=degraded_reason,
                        alignment_result="new",
                        review_status="pending",
                    ))
                    total += 1

        job.total_candidates = total
        job.status = "reviewing"
        db.commit()
        _emit(job, "reviewing", 100, "reviewing", degraded=degraded)

    except Exception as e:
        logger.exception("Extraction pipeline failed")
        job.status = "failed"
        job.error_message = str(e)
        db.commit()
        _emit(job, "failed", 100, "failed")

    return job


async def _run_database_branch(
    job: ExtractionJob,
    config: ExtractionConfig,
    source_ref: str,
    db: Session,
) -> ExtractionJob:
    """数据库源分支：反射表/外键→class/link 候选（只读, R2/R7, FR-012）。"""
    db_source = (job.source_config or {}).get("db_source") or {}
    job.status = "extracting"
    db.commit()
    _emit(job, "extracting", 40, "extracting")

    structures = reflect_database(
        dsn_ref=db_source.get("dsn_ref", ""),
        schema=db_source.get("schema") or db_source.get("schema_name"),
        include_tables=db_source.get("include_tables"),
    )

    job.status = "aligning"
    db.commit()
    _emit(job, "aligning", 70, "aligning")

    total = 0
    for s in structures:
        db.add(ExtractionCandidate(
            job_id=job.id,
            target_class_iri=config.target_class_iri,
            extracted_properties=s.properties,
            candidate_kind=s.candidate_kind,  # "class" | "link"
            group_key=s.name,
            source_ref=f"db:{source_ref}",
            alignment_result="new",
            review_status="pending",
        ))
        total += 1

    job.total_candidates = total
    job.status = "reviewing"
    db.commit()
    _emit(job, "reviewing", 100, "reviewing")
    return job


def _compute_group_key(props: dict, target_class_iri: str, id_prop: str | None) -> str | None:
    """跨源归组键：设备=唯一编号；药品=活性成分+剂型+规格（FR-009）。"""
    cls = target_class_iri.lower()
    if "drug" in cls or "product" in cls or "药" in target_class_iri:
        parts = [
            _lookup(props, "activeingredient") or _lookup(props, "活性成分"),
            _lookup(props, "dosageform") or _lookup(props, "剂型"),
            _lookup(props, "specification") or _lookup(props, "规格"),
        ]
        parts = [str(p) for p in parts if p]
        return "|".join(parts) if parts else None
    # 默认（设备等）：唯一编号。
    if id_prop:
        val = _lookup(props, id_prop)
        if val:
            return str(val)
    return None


def _lookup(props: dict, key: str):
    for k, v in props.items():
        if key.lower() in k.lower():
            return v
    return None


def _mark_canonical(candidates: list[ExtractionCandidate]) -> None:
    """每个 group_key 选 match_score 最高者为规范实例；无 group_key 不标记。"""
    groups: dict[str, list[ExtractionCandidate]] = {}
    for c in candidates:
        if c.group_key:
            groups.setdefault(c.group_key, []).append(c)
    for members in groups.values():
        best = max(members, key=lambda m: m.match_score or 0.0)
        best.is_canonical = True


def _find_id_property(column_mapping: dict | None) -> str | None:
    if not column_mapping:
        return None
    for col, prop in column_mapping.items():
        if "id" in col.lower() or "id" in prop.lower() or "编号" in col:
            return prop
    return None


def _find_label_property(column_mapping: dict | None) -> str | None:
    if not column_mapping:
        return None
    for col, prop in column_mapping.items():
        if "name" in col.lower() or "name" in prop.lower() or "名称" in col:
            return prop
    return None
