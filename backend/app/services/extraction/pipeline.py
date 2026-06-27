"""Extraction pipeline: parse -> extract -> align -> review queue.

各阶段经 progress 事件总线实时上报（R1, FR-002）；Word 正文条件式产出 Action 候选
（FR-005）；受控词表归一化注入（FR-006）；LLM 不可用回退结构化抽取且不失败（FR-007）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.services.extraction.aligner import align_entity
from app.services.extraction.db_reader import reflect_database
from app.services.extraction.gliner_extractor import get_gliner_extractor
from app.services.extraction.llm_extractor import extract_with_fallback
from app.services.extraction.parser import parse_excel, parse_word
from app.services.extraction.progress import progress_bus
from app.services.extraction.semantic import get_embedder
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

        # 研发文档源（007 US2）：source_ref = 文档个体 IRI（供 extractedFrom 回链），
        # 按 content_ref 按需取正文 → LLM 抽取 → 对齐 → 候选入复核队列（review_status='pending'）。
        if config.source_type == "doc_repo":
            return await _run_doc_repo_branch(job, config, engine, db)

        # Stage 1: Parse
        if config.source_type == "excel":
            raw_rows = parse_excel(file_path, column_mapping=config.column_mapping or {},
                                   ner_columns=config.ner_columns)
            # Excel 自由文本列经本地 NER 富化本行属性（仅补空缺、结构化权威，US3）；
            # 清除 __freetext__ 暂存后再进入抽取/对齐主路径，不另生候选（FR-008/018）。
            await _enrich_excel_freetext(raw_rows, config, engine)
            word_sections = None
        elif config.source_type == "word":
            # Word 表格表头经 column_mapping 走确定性 IRI 映射（替代云端，FR-004）。
            word_sections = parse_word(file_path, config.column_mapping)
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
        embedder = get_embedder()  # 进程级单例，含跨候选标签向量缓存（语义对齐）。
        total = 0
        instance_candidates: list[ExtractionCandidate] = []

        # 相关性门控：仅自动/未映射路径（无 column_mapping）需要——结构化透传会把每行
        # 落到当前目标类，自动模式逐类各跑一遍时即「行×类」笛卡尔积放大。已配置
        # column_mapping 表示分析师已声明此源映射到此类，旁路门控、零回归。
        gate_tokens = (
            None if config.column_mapping
            else _class_label_tokens(engine, config.target_class_iri)
        )

        for entity_data in instances:
            if gate_tokens is not None and not _row_mentions_class(entity_data, gate_tokens):
                continue                       # 行未提及本类 → 不落候选
            props = tag_controlled_vocab(dict(entity_data))
            alignment = align_entity(
                candidate=props,
                target_class_iri=config.target_class_iri,
                engine=engine,
                id_property=id_prop,
                label_property=label_prop,
                threshold=settings.lexical_match_threshold,
                embedder=embedder,
                semantic_threshold=settings.semantic_match_threshold,
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

        # Word 正文段落：Action 条件式（既有）+ 本地 NER prose 实体（US2）并存。
        if word_sections:
            total += await _process_word_paragraphs(
                job, config, word_sections, source_ref, degraded_reason,
                engine, db, id_prop, label_prop, embedder, instance_candidates,
            )

        # 跨源归组：结构化 + prose 实例统一选规范实例（is_canonical），歧义不自动合并。
        _mark_canonical(instance_candidates)

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


async def _process_word_paragraphs(
    job: ExtractionJob,
    config: ExtractionConfig,
    word_sections: list[dict],
    source_ref: str,
    degraded_reason: str | None,
    engine: OntologyEngine,
    db: Session,
    id_prop: str | None,
    label_prop: str | None,
    embedder,
    instance_candidates: list[ExtractionCandidate],
) -> int:
    """Word 正文段落 → Action 候选（既有）+ 本地 NER prose instance 候选（US2）。

    每段同时尝试两条独立通道，互不替代（data-model §3.3）：
    1. ``parse_action_from_text``「若…则…必须…」→ ``candidate_kind="action"``。
    2. 本地零样本 NER 召回业务实体 → ``candidate_kind="instance"``，``source_ref`` 带
       ``#para`` 溯源、``review_status="pending"`` 入复核队列（FR-005/010）。

    NER 经 ``get_gliner_extractor()`` 守卫——缺包/缺权重/功能关/类无标签时静默跳过 prose
    分支、Action 与结构化主路径零回归（优雅降级，FR-012）。GLiNER 推理为 CPU 同步阻塞，
    经 ``asyncio.to_thread`` 调用以不阻塞事件循环（FR-015）。返回新增候选数。
    """
    # NER schema 与提取器一次性就绪（避免逐段重复派生/加载）。
    ner_schema = _schema_from_class(engine, config.target_class_iri)
    extractor = get_gliner_extractor()
    ner_ready = bool(extractor and ner_schema["labels"] and extractor.is_available())

    added = 0
    for sec in word_sections:
        if sec.get("type") != "paragraph":
            continue
        text = sec.get("content", "")

        # 通道 1：条件式 → Action 候选（FR-005，既有行为不变）。
        action = parse_action_from_text(text)
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
            added += 1

        # 通道 2：本地 NER prose 实体 → instance 候选（守卫降级）。
        if not ner_ready:
            continue
        ner_result = await asyncio.to_thread(
            extractor.extract_text, text, ner_schema["labels"], settings.gliner_threshold,
        )
        # label → 属性 IRI 键回填（label_to_iri）；空召回静默跳过。
        props = {ner_schema["label_to_iri"][label]: value
                 for label, value in ner_result.items()
                 if label in ner_schema["label_to_iri"]}
        if not props:
            continue

        props = tag_controlled_vocab(props)
        alignment = align_entity(
            candidate=props,
            target_class_iri=config.target_class_iri,
            engine=engine,
            id_property=id_prop,
            label_property=label_prop,
            threshold=settings.lexical_match_threshold,
            embedder=embedder,
            semantic_threshold=settings.semantic_match_threshold,
        )
        cand = ExtractionCandidate(
            job_id=job.id,
            target_class_iri=config.target_class_iri,
            extracted_properties=props,
            candidate_kind="instance",
            group_key=_compute_group_key(props, config.target_class_iri, id_prop),
            source_ref=f"{source_ref}#para",       # 溯源回链（FR-005）
            degraded_reason=degraded_reason,
            alignment_result=alignment.action,
            aligned_iri=alignment.match_iri,
            match_score=alignment.match_score,
            review_status="pending",               # 入复核队列，不自动断言（FR-010）
        )
        db.add(cand)
        instance_candidates.append(cand)
        added += 1

    return added


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


def fetch_document_content(
    content_ref: str | None, source_config: dict | None = None
) -> list[dict]:
    """按 `content_ref` 外部引用按需取文档正文（Q2：平台不持久化全文）。

    返回结构化字段行（`list[dict]`），供 `extract_with_fallback` 在降级时直通。
    inline/上传过渡模式从 `source_config['inline_content']` 取（测试/过渡）；`http` 模式（US4）
    经外部端点取。正文仅在抽取过程中存在，不回写 `source_config`、不另建持久化全文列。
    """
    return [dict(r) for r in ((source_config or {}).get("inline_content") or [])]


async def _run_doc_repo_branch(
    job: ExtractionJob,
    config: ExtractionConfig,
    engine: OntologyEngine,
    db: Session,
) -> ExtractionJob:
    """研发文档源分支（007 US2，content-extraction C2）。

    `source_ref = job.source_config['doc_ref']`（文档个体 IRI，非 `source_filename`）——每个候选
    据此携溯源来源，确认入库时 `_commit_candidate` 注入 `extractedFrom` 回链（C4）。复用既有
    `align_entity`/`_compute_group_key`/`extract_with_fallback`（降级）——doc_repo 不另起
    对齐栈（宪章 V）。
    """
    source_cfg = job.source_config or {}
    source_ref = source_cfg["doc_ref"]          # 文档个体 IRI（溯源锚点）
    content_ref = source_cfg.get("content_ref")

    job.status = "extracting"
    db.commit()
    _emit(job, "extracting", 40, "extracting")

    # 按需取正文（Q2：不持久化全文）。
    raw_rows = fetch_document_content(content_ref, source_cfg)

    instances, degraded_reason = await extract_with_fallback(
        raw_rows, config.target_class_iri, property_schema=[],
        controlled_vocab=CONTROLLED_VOCAB,
    )
    degraded = degraded_reason is not None

    job.status = "aligning"
    db.commit()
    _emit(job, "aligning", 70, "aligning", degraded=degraded)

    id_prop = _find_id_property(config.column_mapping)
    label_prop = _find_label_property(config.column_mapping)
    embedder = get_embedder()
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
            threshold=settings.lexical_match_threshold,
            embedder=embedder,
            semantic_threshold=settings.semantic_match_threshold,
        )
        group_key = _compute_group_key(props, config.target_class_iri, id_prop)
        cand = ExtractionCandidate(
            job_id=job.id,
            target_class_iri=config.target_class_iri,
            extracted_properties=props,
            candidate_kind="instance",
            group_key=group_key,
            source_ref=source_ref,            # = 文档个体 IRI（C2.1）
            degraded_reason=degraded_reason,
            alignment_result=alignment.action,
            aligned_iri=alignment.match_iri,
            match_score=alignment.match_score,
            review_status="pending",          # C2.2：不自动断言，一律入复核队列
        )
        db.add(cand)
        instance_candidates.append(cand)
        total += 1

    _mark_canonical(instance_candidates)

    job.total_candidates = total
    job.status = "reviewing"
    db.commit()
    _emit(job, "reviewing", 100, "reviewing", degraded=degraded)
    return job


async def _enrich_excel_freetext(
    raw_rows: list[dict],
    config: ExtractionConfig,
    engine: OntologyEngine,
) -> None:
    """Excel 自由文本列经本地零样本 NER 富化本行属性（008 US3，FR-008/018，contract P8–P12）。

    每行 ``__freetext__`` 暂存（``parse_excel`` 产出）逐段跑 GLiNER → 经 ``label_to_iri``
    得属性 → ``_merge_ner`` 仅补空缺（结构化权威、**不另生候选**）。NER 经
    ``get_gliner_extractor()`` 守卫——缺包/缺权重/功能关/类无标签时静默跳过富化，但**仍
    清除 __freetext__ 暂存**，使候选不含临时键（优雅降级零回归，contract P12/SC-005）。
    GLiNER 推理为 CPU 同步阻塞，经 ``asyncio.to_thread`` 调用以不阻塞事件循环（FR-015）。
    就地修改 ``raw_rows``。
    """
    if not any("__freetext__" in r for r in raw_rows):
        return

    # schema 与提取器一次性就绪（避免逐行重复派生/加载）。
    ner_schema = _schema_from_class(engine, config.target_class_iri)
    extractor = get_gliner_extractor()
    ner_ready = bool(extractor and ner_schema["labels"] and extractor.is_available())

    for row in raw_rows:
        freetext = row.get("__freetext__")
        if not freetext:
            row.pop("__freetext__", None)
            continue
        ner_props: dict[str, object] = {}
        if ner_ready:
            for text in freetext.values():
                result = await asyncio.to_thread(
                    extractor.extract_text, str(text),
                    ner_schema["labels"], settings.gliner_threshold,
                )
                for label, value in result.items():
                    iri = ner_schema["label_to_iri"].get(label)
                    if iri and iri not in ner_props:   # 同 IRI 多命中：确定性保留首个
                        ner_props[iri] = value
        _merge_ner(row, ner_props)             # 守卫关时 ner_props 空：仅清除暂存


def _merge_ner(row: dict, ner_props: dict) -> dict:
    """把本地 NER 抽取属性回填本行：仅补空缺、结构化权威；收尾清除 __freetext__ 暂存。

    对 ``ner_props`` 每个 ``(iri, value)``：仅当 ``row[iri]`` 缺省或为空（``None`` / 空白
    串）才写入，已有非空结构化值一律保留（结构化权威，contract P8/P9，FR-008）。无论是否
    命中，合并末尾都移除临时 ``__freetext__`` 键（contract P11，候选不含暂存）。就地修改并
    返回 ``row``。
    """
    for iri, value in ner_props.items():
        existing = row.get(iri)
        if existing is None or (isinstance(existing, str) and not existing.strip()):
            row[iri] = value
    row.pop("__freetext__", None)
    return row


def _schema_from_class(engine: OntologyEngine, target_class_iri: str) -> dict:
    """从目标本体类只读派生 NER 标签集（008 US2/US3，FR-009/013，contract S1–S6）。

    返回 ``{"labels": list[str], "label_to_iri": dict[str, str]}``：``labels`` 供
    GLiNER 作零样本抽取标签（每个 data_property 的 label，缺省回退 name）；
    ``label_to_iri`` 把抽取结果回填到属性 IRI 键。**只读** ``get_class_detail``，
    绝不触 World 写路径（宪章 II）。类无属性 / 不存在 → 空 schema（NER 跳过）。
    """
    detail = engine.get_class_detail(target_class_iri)
    if detail is None:
        return {"labels": [], "label_to_iri": {}}

    labels: list[str] = []
    label_to_iri: dict[str, str] = {}
    for p in getattr(detail, "data_properties", None) or []:
        label = (p.get("label") or p.get("name") or "").strip()
        iri = p.get("iri")
        if not label or not iri:
            continue
        if label in label_to_iri:
            # 同 label 多属性：确定性保留首个并告警（不随机，S6）。
            logger.warning(
                "NER schema 派生：标签 '%s' 对应多属性，保留首个 %s（忽略 %s）",
                label, label_to_iri[label], iri,
            )
            continue
        label_to_iri[label] = iri
        labels.append(label)
    return {"labels": labels, "label_to_iri": label_to_iri}


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


def _class_label_tokens(engine: OntologyEngine, target_class_iri: str) -> set[str]:
    """目标类的可匹配文本标记：label_zh / label_en / name（去空、长度≥2）。

    自动/未映射抽取下唯一可用的「源行↔目标类」相关性信号——本体类多为无
    data_properties 的角色/类型类，无属性可比，故以类标签/名称作判定。只读
    ``get_class_detail``，绝不触 World 写路径（宪章 II）。
    """
    detail = engine.get_class_detail(target_class_iri)
    if detail is None:
        return set()
    raw = (
        getattr(detail, "label_zh", None),
        getattr(detail, "label_en", None),
        getattr(detail, "name", None),
    )
    return {str(t).strip() for t in raw if t and len(str(t).strip()) >= 2}


def _row_mentions_class(row: dict, tokens: set[str]) -> bool:
    """行（键+值）文本是否提及目标类任一标记。``tokens`` 为空 → 放行（无从判定）。

    相关性门控（FR 复核质量）：自动抽取曾把每张表的每一行交叉落到全部类、致候选
    被「行×类」放大约 200 倍。此判定使一行仅在其文本确实提及某类时才作为该类候选，
    把笛卡尔积收敛为真实相关对。仅在未显式配置 ``column_mapping`` 时启用（见调用点）。
    """
    if not tokens:
        return True
    blob = " ".join([*map(str, row.keys()), *map(str, row.values())])
    return any(tok in blob for tok in tokens)


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
