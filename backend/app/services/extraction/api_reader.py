"""声明驱动 REST/JSON 源读取器（014 US2，R7/FR-008/019，contracts/source-connector.md）。

把内网 REST/JSON 端点的分页条目读成与 DB 适配器**完全同构**的统一候选
（:class:`RowCandidate` / :class:`RowReadResult`）——同一套属性绑定、transform、
标识符/标签、对象属性解析语义，只是源从「表行」换成「JSON 条目」（FR-008）。

- 传输：经 :func:`connector_for` 取 ``RestConnector``（凭据经 env 注入、绝不入库，FR-006），
  ``fetch_items()`` 跟随分页取全部条目。
- 条目游走：属性绑定的 ``source_path`` 为 JSON 路径（``$.data[*].field``）——连接器已把
  ``$.data[*]`` 前缀（分页信封）走完，读取器取 ``[*].`` 之后的**逐条目相对路径**。
- 漂移（R5/FR-020）：某绑定路径在所有条目均缺失 → 记入 ``drifted_paths``（置 E6
  health='drift'）；单条目缺失则跳过该值、该条目仍产候选。
- 对象属性：``id_reference`` 解析既有个体 IRI（否则 link 候选）；``nested_object`` 递归
  内嵌结构 → 子候选（经 ``group_key`` 与父候选相连，有界深度，T027）。
- 优雅降级（R12/FR-019/SC-008）：内网不可达/超时 → 零候选 + ``degraded_reason``，作业
  完成（``status="degraded"``），绝不崩溃；公网姿态由连接配置校验前置拦截（不在此判定）。
"""

from __future__ import annotations

import logging

from app.models.integration import IntegrationConnector
from app.models.ontology_meta import (
    OntologyClass,
    OntologyClassMapping,
    OntologyPropertyBinding,
)
from app.services.extraction.db_reader import (
    RowCandidate,
    RowReadResult,
    _resolve_id_reference,
)
from app.services.extraction.transforms import apply_transform
from app.services.integration.connector_factory import connector_for
from app.services.integration.rest_connector import TRANSPORT_ERRORS

logger = logging.getLogger(__name__)

_MISSING = object()   # 「路径缺失」哨兵，区别于「存在但为 null/空」。
_MAX_DEPTH = 5        # nested_object 递归护栏（有界深度，防内嵌自引用环）。


def _item_leaf_path(source_path: str) -> str:
    """取属性绑定 ``source_path`` 的**逐条目相对路径**（连接器已走完 ``[*]`` 前的信封）。

    ``$.data[*].drugName`` → ``drugName``；``$.data[*].manufacturer`` → ``manufacturer``；
    裸路径 ``name`` → ``name``（内嵌子对象属性绑定用裸叶路径）。
    """
    sp = (source_path or "").strip()
    if "[*]" in sp:
        sp = sp.split("[*]")[-1]
    return sp.lstrip("$").lstrip(".").strip()


def _walk(node, leaf: str):
    """按点分叶路径向下取值；任一层缺失 → 返回 ``_MISSING`` 哨兵。"""
    for key in (k for k in (leaf or "").split(".") if k):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return _MISSING
    return node


def _as_nested_dicts(value) -> list[dict]:
    """内嵌对象归一为 dict 列表：单 dict → ``[dict]``；list → 其中的 dict；其余 → ``[]``。"""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _extract_data_props(data_bindings, obj) -> tuple[dict, list[str], set[str]]:
    """对一个 JSON 对象套用数据属性绑定 → (props, notes, 命中的 source_path 集)。

    路径缺失的绑定跳过（不入 props）；命中则套用逐值 transform（R6），note 收集非致命问题。
    """
    props: dict = {}
    notes: list[str] = []
    present: set[str] = set()
    for pb in data_bindings:
        raw = _walk(obj, _item_leaf_path(pb.source_path))
        if raw is _MISSING:
            continue
        present.add(pb.source_path)
        outcome = apply_transform(pb.transform_type, pb.transform_config, raw)
        props[pb.property_iri] = outcome.value
        if outcome.note:
            notes.append(f"{pb.property_iri}: {outcome.note}")
    return props, notes, present


def _process_object_binding(
    pb, obj, props, identifier, source_ref, engine, db, depth: int
) -> tuple[list[RowCandidate], set[str]]:
    """解析一个对象属性绑定 → (附带候选, 命中的 source_path 集)。

    ``id_reference``：解析既有个体 IRI 写回 ``props``（否则产 ``link`` 复核候选，FR-023a）。
    ``nested_object``：递归内嵌 dict → 子 ``instance`` 候选，``identifier`` 沿用父标识符使
    二者共享 ``group_key``（T027）；有界深度 ``_MAX_DEPTH``。
    """
    raw = _walk(obj, _item_leaf_path(pb.source_path))
    if raw is _MISSING or raw is None:
        return [], set()
    present = {pb.source_path}

    if pb.object_resolution == "nested_object" and pb.nested_binding_id and depth < _MAX_DEPTH:
        child = db.get(OntologyClassMapping, pb.nested_binding_id)
        if child is None:
            return [], present
        child_pbs = (
            db.query(OntologyPropertyBinding).filter_by(class_mapping_id=child.id).all()
        )
        child_cls = db.get(OntologyClass, child.class_id)
        child_iri = child_cls.slpra_iri if child_cls else pb.target_class_iri
        child_data = [p for p in child_pbs if (p.property_kind or "data") == "data"]
        child_objs = [p for p in child_pbs if p.property_kind == "object"]

        out: list[RowCandidate] = []
        for nd in _as_nested_dicts(raw):
            sub_props, sub_notes, _ = _extract_data_props(child_data, nd)
            for cpb in child_objs:                      # 递归子对象（有界深度）
                nested, _ = _process_object_binding(
                    cpb, nd, sub_props, identifier, source_ref, engine, db, depth + 1
                )
                out.extend(nested)
            out.append(RowCandidate(
                candidate_kind="instance",
                extracted_properties=sub_props,
                source_ref=source_ref,
                identifier=identifier,                  # 与父候选共享 group_key
                target_class_iri=child_iri,
                notes=sub_notes,
            ))
        return out, present

    if pb.object_resolution == "id_reference":
        match_iri = _resolve_id_reference(engine, pb.target_class_iri, pb.target_id_path, raw)
        if match_iri is not None:
            props[pb.property_iri] = match_iri          # 物化对象链接
            return [], present
        return [RowCandidate(                            # FR-023a — 未解析 → link 复核候选
            candidate_kind="link",
            extracted_properties={pb.property_iri: raw},
            source_ref=source_ref,
            target_class_iri=pb.target_class_iri,
            notes=[f"{pb.property_iri}: 未找到匹配 "
                   f"{pb.target_class_iri}[{pb.target_id_path}={raw}]"],
        )], present

    return [], present


async def read_api_items(binding, property_bindings, engine, db) -> RowReadResult:
    """读取 ``api_endpoint``-绑定源端点为声明驱动候选（US2，与 DB 适配器同构输出）。

    经 ``binding.source_system``（连接器名）取 ``rest_api`` 连接器，分页拉取全部条目，逐
    条目套用属性绑定 → 一个 ``instance`` 候选（+ 内嵌 ``nested_object`` 子候选 / 未解析
    ``id_reference`` link 候选）。凭据经 env 注入、绝不入库（FR-006）。

    降级（R12/FR-019）：连接器未配置 / 内网不可达 / 超时 → 零候选 + ``degraded_reason``，
    作业完成不崩溃。漂移（R5/FR-020）：路径在所有条目均缺失 → ``drifted_paths``。
    """
    source_ref_system = (binding.source_system or "").strip()
    endpoint = (binding.target or "").strip()

    connector = (
        db.query(IntegrationConnector)
        .filter(IntegrationConnector.name == source_ref_system)
        .first()
    )
    if connector is None:
        reason = f"API 源连接器未找到：{source_ref_system}（请先登记 rest_api 连接器）"
        logger.warning("API 源降级：%s", reason)
        return RowReadResult(degraded_reason=reason)

    conn = connector_for(connector)
    # R12/FR-019 — 内网不可达/超时/凭据缺失 → 降级（零候选、不崩溃，绝非「暂不支持」占位）。
    try:
        items = await conn.fetch_items()
    except TRANSPORT_ERRORS as exc:
        reason = f"API 源不可达：{type(exc).__name__}: {exc}"
        logger.warning("API 源降级（内网不可达）：%s", reason)
        return RowReadResult(degraded_reason=reason)
    except ValueError as exc:                            # 凭据 env 未注入（FR-006，显式非静默）
        reason = f"API 源凭据缺失：{exc}"
        logger.warning("API 源降级（凭据缺失）：%s", reason)
        return RowReadResult(degraded_reason=reason)

    data_bindings = [pb for pb in property_bindings if (pb.property_kind or "data") == "data"]
    object_bindings = [pb for pb in property_bindings if pb.property_kind == "object"]
    id_binding = next((pb for pb in property_bindings if pb.is_identifier), None)
    id_leaf = _item_leaf_path(id_binding.source_path) if id_binding else None

    candidates: list[RowCandidate] = []
    present_paths: set[str] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        # 可溯源出处（SC-003）：system=连接器引用，entity=端点，record=标识符值→序号。
        record = _walk(item, id_leaf) if id_leaf else _MISSING
        if record in (_MISSING, None, ""):
            record = idx
        source_ref = {"system": source_ref_system, "entity": endpoint, "record": str(record)}

        props, notes, present = _extract_data_props(data_bindings, item)
        present_paths |= present

        extra: list[RowCandidate] = []
        for pb in object_bindings:
            more, obj_present = _process_object_binding(
                pb, item, props, None, source_ref, engine, db, 0
            )
            present_paths |= obj_present
            extra.extend(more)

        id_val = props.get(id_binding.property_iri) if id_binding else None
        identifier = str(id_val) if id_val not in (None, "") else None
        # 子候选（nested_object）继承父标识符 → 共享 group_key（T027，链接父实例）。
        for c in extra:
            if c.candidate_kind == "instance" and c.identifier is None:
                c.identifier = identifier

        candidates.append(RowCandidate(
            candidate_kind="instance",
            extracted_properties=props,
            source_ref=source_ref,
            identifier=identifier,
            notes=notes,
        ))
        candidates.extend(extra)

    # 漂移：声明路径在所有条目均缺失（object 绑定同理）。
    declared = [pb for pb in (data_bindings + object_bindings) if pb.source_path]
    drifted = [pb.source_path for pb in declared if pb.source_path not in present_paths]
    if drifted:
        logger.warning("E6 漂移：端点 %s 缺路径 %s（跳过对应绑定, R5）", endpoint, drifted)

    logger.info("API 声明式读取：端点 %s → %d 条目 → %d 候选（漂移路径 %d）",
                endpoint, len(items), len(candidates), len(drifted))
    return RowReadResult(candidates=candidates, drifted_paths=drifted)
