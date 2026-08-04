"""Constrained ontology-guided document Profile compiler and interpreter.

The module is deliberately source- and class-agnostic.  It compiles either
published extraction metadata or E6/E6b bindings into the same immutable runtime
values, then applies a small set of locators to the shared Word IR.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.extraction.docx_structure import DocSection, DocStructure, DocTable
from app.services.extraction.transforms import apply_transform

logger = logging.getLogger(__name__)

_LOCATORS = {"section_kv", "table_rows", "table_singleton"}
_NUMBERING_RE = re.compile(
    r"^\s*(?:(?:[0-9]+(?:[.．][0-9]+)*)[.．、)）\s]*|"
    r"[（(]?[一二三四五六七八九十百]+[)）、.．\s]+)"
)
_TRAILING_UNIT_RE = re.compile(r"\s*[（(][^）)]*[）)]\s*$")
_KV_RE = re.compile(r"^\s*([^：:]{1,80})[：:]\s*(.*)$")
_EQUIPMENT_ID_ALIASES = {"设备编号", "匹配设备"}
_EQUIPMENT_CANDIDATE_SEPARATOR_RE = re.compile(r"\s*(?:/|或|、)\s*")
_EQUIPMENT_CODE_RE = re.compile(r"^PF\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class IdentityProfile:
    aliases: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    pattern: str | None = None
    split: str | None = None
    fallback: str | None = None


@dataclass(frozen=True)
class LocatorProfile:
    locator: str
    anchor_any: tuple[str, ...] = ()
    header_groups: tuple[tuple[str, ...], ...] = ()
    endpoint_mode: str = "singleton"
    orientation: str = "columns"
    key_aliases: tuple[str, ...] = ()
    value_aliases: tuple[str, ...] = ()
    source_label: str | None = None
    identity: IdentityProfile | None = None


@dataclass(frozen=True)
class DocumentExtractionProfile:
    version: int
    sources: tuple[LocatorProfile, ...]
    property_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    transforms: dict[str, dict] = field(default_factory=dict)
    identity: IdentityProfile = field(default_factory=IdentityProfile)
    subclass_by: str | None = None
    label: str | None = None
    applicability: dict | None = None


@dataclass(frozen=True)
class DocumentPropertyBinding:
    property_iri: str
    label: str
    aliases: tuple[str, ...]
    transform_type: str = "none"
    transform_config: dict | None = None
    reject_invalid: bool = False
    is_identifier: bool = False
    is_label: bool = False


@dataclass(frozen=True)
class DocumentValue:
    property_iri: str
    label: str
    value: Any
    raw_value: str
    source_ref: dict
    note: str | None = None


@dataclass
class DocumentCandidate:
    target_class_iri: str
    values: list[DocumentValue]
    identifier: str | None
    group_key: str | None
    source_ref: dict
    notes: list[str] = field(default_factory=list)
    subclass_value: str | None = None
    candidate_group: str | None = None
    candidate_expression: str | None = None

    @property
    def extracted_properties(self) -> dict[str, Any]:
        return {value.property_iri: value.value for value in self.values}


@dataclass
class DocumentReadResult:
    candidates: list[DocumentCandidate] = field(default_factory=list)
    drifted_paths: list[str] = field(default_factory=list)
    degraded_reason: str | None = None


class DocumentProfileError(ValueError):
    """Profile cannot be compiled safely."""


def _as_strings(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise DocumentProfileError("alias collection must be a string or list")


def _parse_identity(raw) -> IdentityProfile:
    if not raw:
        return IdentityProfile()
    if not isinstance(raw, dict):
        raise DocumentProfileError("identity must be an object")
    pattern = raw.get("pattern")
    if pattern is not None:
        try:
            re.compile(str(pattern))
        except re.error as exc:
            raise DocumentProfileError(f"invalid identity pattern: {exc}") from exc
    return IdentityProfile(
        aliases=_as_strings(raw.get("aliases")),
        fields=_as_strings(raw.get("fields")),
        pattern=str(pattern) if pattern else None,
        split=str(raw["split"]) if raw.get("split") else None,
        fallback=str(raw["fallback"]).strip() if raw.get("fallback") else None,
    )


def _parse_header_groups(raw) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw, dict):
        return ()
    all_of = raw.get("all_of")
    if all_of is None and raw.get("any_of"):
        all_of = [{"any_of": raw["any_of"]}]
    if not isinstance(all_of, list):
        return ()
    groups: list[tuple[str, ...]] = []
    for group in all_of:
        if isinstance(group, dict):
            aliases = _as_strings(group.get("any_of"))
        else:
            aliases = _as_strings(group)
        if aliases:
            groups.append(aliases)
    return tuple(groups)


def _parse_source(raw) -> LocatorProfile:
    if not isinstance(raw, dict):
        raise DocumentProfileError("each source must be an object")
    locator = str(raw.get("locator") or "").strip()
    if locator not in _LOCATORS:
        raise DocumentProfileError(f"unsupported locator: {locator or '<empty>'}")
    anchors = raw.get("anchors") or {}
    anchor_any = _as_strings(
        anchors.get("any_of") if isinstance(anchors, dict) else anchors
    )
    header_groups = _parse_header_groups(raw.get("headers"))
    if locator == "section_kv" and not anchor_any:
        raise DocumentProfileError("section_kv requires anchors.any_of")
    if locator.startswith("table_") and not header_groups:
        raise DocumentProfileError(f"{locator} requires headers.all_of")
    endpoint_default = "row" if locator == "table_rows" else "singleton"
    orientation = str(raw.get("orientation") or "columns")
    key_aliases = _as_strings(raw.get("key_aliases"))
    value_aliases = _as_strings(raw.get("value_aliases"))
    if locator == "table_singleton" and orientation == "kv":
        if not key_aliases or not value_aliases:
            raise DocumentProfileError(
                "table_singleton kv requires key_aliases and value_aliases"
            )
    return LocatorProfile(
        locator=locator,
        anchor_any=anchor_any,
        header_groups=header_groups,
        endpoint_mode=str(raw.get("endpoint_mode") or endpoint_default),
        orientation=orientation,
        key_aliases=key_aliases,
        value_aliases=value_aliases,
        source_label=str(raw["source_label"]) if raw.get("source_label") else None,
        identity=_parse_identity(raw.get("identity")) if raw.get("identity") else None,
    )


def _compact_profile(text: str) -> dict:
    """Accept a small E6 convenience syntax in addition to JSON.

    section_kv:产品的基本性质|基本性质
    """
    if ":" in text:
        locator, raw_anchors = text.split(":", 1)
        locator = locator.strip()
        aliases = [part.strip() for part in raw_anchors.split("|") if part.strip()]
        if locator == "section_kv":
            return {
                "version": 1,
                "sources": [{
                    "locator": locator,
                    "anchors": {"any_of": aliases},
                }],
            }
    return {
        "version": 1,
        "sources": [{
            "locator": "section_kv",
            "anchors": {"any_of": [text]},
        }],
    }


def parse_profile(raw: str | dict) -> DocumentExtractionProfile:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise DocumentProfileError("profile is empty")
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DocumentProfileError(f"invalid profile JSON: {exc}") from exc
        else:
            payload = _compact_profile(text)
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise DocumentProfileError("profile must be a JSON object or string")
    if not isinstance(payload, dict):
        raise DocumentProfileError("profile root must be an object")
    version = int(payload.get("version", 1))
    if version != 1:
        raise DocumentProfileError(f"unsupported profile version: {version}")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise DocumentProfileError("profile requires at least one source")
    property_aliases: dict[str, tuple[str, ...]] = {}
    for key, aliases in (payload.get("property_aliases") or {}).items():
        property_aliases[str(key)] = _as_strings(aliases)
    transforms = payload.get("transforms") or {}
    if not isinstance(transforms, dict):
        raise DocumentProfileError("transforms must be an object")
    return DocumentExtractionProfile(
        version=version,
        sources=tuple(_parse_source(source) for source in raw_sources),
        property_aliases=property_aliases,
        transforms={str(key): value for key, value in transforms.items()
                    if isinstance(value, dict)},
        identity=_parse_identity(payload.get("identity")),
        subclass_by=(
            str(payload["subclass_by"]) if payload.get("subclass_by") else None
        ),
        label=str(payload["label"]) if payload.get("label") else None,
        applicability=(
            payload.get("applicability")
            if isinstance(payload.get("applicability"), dict)
            else None
        ),
    )


def _local_name(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _lookup_profile_value(mapping: dict, prop: dict):
    for key in (prop.get("iri"), prop.get("name"), _local_name(prop.get("iri", ""))):
        if key in mapping:
            return mapping[key]
    return None


def _datatype(prop: dict) -> str | None:
    datatype = prop.get("datatype")
    if datatype:
        return str(datatype).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    ranges = prop.get("range") or []
    if ranges:
        return str(ranges[0]).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    return None


def compile_ontology_bindings(
    engine,
    target_class_iri: str,
    profile: DocumentExtractionProfile,
) -> list[DocumentPropertyBinding]:
    bindings: list[DocumentPropertyBinding] = []
    for prop in engine.get_data_properties_by_domain(target_class_iri) or []:
        iri = prop.get("iri")
        if not iri:
            continue
        aliases: list[str] = []
        for alias in (
            prop.get("label"),
            prop.get("name"),
            *(prop.get("aliases") or []),
            *(_lookup_profile_value(profile.property_aliases, prop) or ()),
        ):
            if alias and str(alias) not in aliases:
                aliases.append(str(alias))
        transform = _lookup_profile_value(profile.transforms, prop) or {}
        datatype = _datatype(prop)
        transform_type = str(transform.get("type") or "none")
        transform_config = transform.get("config")
        if transform_type == "none" and datatype in {"boolean", "integer", "decimal"}:
            transform_type = "cast"
            transform_config = {"to": datatype}
        reject_invalid = bool(
            transform.get("reject_invalid")
            or (isinstance(transform_config, dict)
                and transform_config.get("reject_invalid"))
        )
        bindings.append(DocumentPropertyBinding(
            property_iri=str(iri),
            label=str(prop.get("label") or prop.get("name") or _local_name(str(iri))),
            aliases=tuple(aliases),
            transform_type=transform_type,
            transform_config=transform_config,
            reject_invalid=reject_invalid,
        ))
    return bindings


def _source_path_aliases(source_path: str) -> tuple[str, ...]:
    text = (source_path or "").strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return _as_strings(parsed)
    if "|" in text:
        return tuple(part.strip() for part in text.split("|") if part.strip())
    return (text,)


def compile_e6_bindings(property_bindings) -> list[DocumentPropertyBinding]:
    out: list[DocumentPropertyBinding] = []
    for binding in property_bindings:
        if (getattr(binding, "property_kind", "data") or "data") != "data":
            continue
        config = getattr(binding, "transform_config", None)
        reject_invalid = bool(
            isinstance(config, dict) and config.get("reject_invalid")
        )
        iri = str(binding.property_iri)
        out.append(DocumentPropertyBinding(
            property_iri=iri,
            label=_local_name(iri),
            aliases=_source_path_aliases(binding.source_path),
            transform_type=getattr(binding, "transform_type", None) or "none",
            transform_config=config,
            reject_invalid=reject_invalid,
            is_identifier=bool(getattr(binding, "is_identifier", False)),
            is_label=bool(getattr(binding, "is_label", False)),
        ))
    return out


def normalize_source_key(value: str) -> str:
    text = _NUMBERING_RE.sub("", str(value or "").strip())
    text = re.sub(r"\s+", "", text)
    text = text.replace("是否是", "是否")
    text = _TRAILING_UNIT_RE.sub("", text)
    return text.casefold()


def _matches(source: str, alias: str) -> bool:
    source_norm = normalize_source_key(source)
    alias_norm = normalize_source_key(alias)
    return bool(source_norm and alias_norm and (
        source_norm == alias_norm
        or alias_norm in source_norm
        or source_norm in alias_norm
    ))


def _match_quality(
    header_norm: str, alias_norm: str
) -> tuple[int, int] | None:
    """Score a header↔alias match: (tier, specificity). Higher = better.

    Tier 3 = exact, 2 = forward (alias ⊂ header), 1 = reverse (header ⊂ alias).
    Specificity = normalized string length (longer = more specific).
    Returns None on no match.
    """
    if not header_norm or not alias_norm:
        return None
    if header_norm == alias_norm:
        return (3, len(alias_norm))
    if alias_norm in header_norm:
        return (2, len(alias_norm))
    if header_norm in alias_norm:
        return (1, len(header_norm))
    return None


def _assign_columns(
    headers: list[str],
    bindings: list[DocumentPropertyBinding],
) -> dict[int, str]:
    """Compute a mutually-exclusive binding→column assignment for a table.

    Each column is assigned to at most one binding and vice-versa.  Candidates
    are ranked by ``_match_quality`` and assigned greedily by tier (exact first,
    then forward, then reverse).  Ties within the same tier are broken by
    specificity (longer normalized string wins); truly tied candidates are
    skipped (abstain — prefer missing data over fabricated data).
    """
    header_norms = [normalize_source_key(h) for h in headers]
    candidates: list[tuple[tuple[int, int], int, str]] = []
    for b_idx, binding in enumerate(bindings):
        best_per_col: dict[int, tuple[tuple[int, int], str]] = {}
        for alias in binding.aliases:
            alias_norm = normalize_source_key(alias)
            for c_idx, h_norm in enumerate(header_norms):
                q = _match_quality(h_norm, alias_norm)
                if q is None:
                    continue
                prev = best_per_col.get(c_idx)
                if prev is None or q > prev[0]:
                    best_per_col[c_idx] = (q, headers[c_idx])
        for c_idx, (score, header) in best_per_col.items():
            candidates.append((score, b_idx, header))

    candidates.sort(key=lambda c: c[0], reverse=True)

    binding_best: dict[int, tuple[int, int]] = {}
    for score, b_idx, _header in candidates:
        if b_idx not in binding_best or score > binding_best[b_idx]:
            binding_best[b_idx] = score

    used_cols: set[str] = set()
    used_bindings: set[int] = set()
    abstained: set[int] = set()
    assignment: dict[int, str] = {}
    for score, b_idx, header in candidates:
        if b_idx in used_bindings or b_idx in abstained or header in used_cols:
            continue
        if score < binding_best[b_idx]:
            abstained.add(b_idx)
            continue
        avail_cols = [
            c for c in candidates
            if c[1] == b_idx and c[0] == score and c[2] not in used_cols
        ]
        if len(avail_cols) > 1:
            abstained.add(b_idx)
            continue
        rival_bindings = [
            c for c in candidates
            if c[2] == header and c[0] == score
            and c[1] not in used_bindings and c[1] not in abstained
        ]
        if len(rival_bindings) > 1:
            continue
        assignment[b_idx] = header
        used_bindings.add(b_idx)
        used_cols.add(header)
    return assignment


def _matching_key(mapping: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        for key, value in mapping.items():
            if value not in (None, "") and _matches(key, alias):
                return key
    return None


def _binding_for_key(
    key: str,
    bindings: list[DocumentPropertyBinding],
) -> DocumentPropertyBinding | None:
    """Return the single most-specific binding for *key*; abstain on ties."""
    key_norm = normalize_source_key(key)
    best_binding: DocumentPropertyBinding | None = None
    best_score: tuple[int, int] | None = None
    tied = False
    for binding in bindings:
        for alias in binding.aliases:
            alias_norm = normalize_source_key(alias)
            q = _match_quality(key_norm, alias_norm)
            if q is None:
                continue
            if best_score is None or q > best_score:
                best_score = q
                best_binding = binding
                tied = False
            elif q == best_score and binding is not best_binding:
                tied = True
    return None if tied else best_binding


def _headers_match(table: DocTable, groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(
        any(
            _matches(header, alias)
            for header in table.headers
            for alias in group
        )
        for group in groups
    )


def _section_match(section: DocSection, aliases: tuple[str, ...]) -> bool:
    return any(_matches(section.heading, alias) for alias in aliases)


def _transform_value(
    binding: DocumentPropertyBinding,
    raw_value: Any,
    source_key: str,
    source_ref: dict,
) -> tuple[DocumentValue | None, str | None]:
    raw_text = str(raw_value).strip()
    config = dict(binding.transform_config or {})
    value: Any = raw_value
    number_pattern = config.get("number_pattern")
    if number_pattern:
        try:
            match = re.search(str(number_pattern), raw_text)
        except re.error as exc:
            note = f"{binding.label}: invalid number_pattern: {exc}"
            return None if binding.reject_invalid else DocumentValue(
                binding.property_iri, binding.label, raw_value, raw_text,
                source_ref, note,
            ), note
        if not match:
            note = f"{binding.label}: no numeric value in '{raw_text}'"
            if binding.reject_invalid:
                return None, note
        else:
            value = match.group(1) if match.lastindex else match.group(0)

    accepted_units = config.get("accepted_units")
    if accepted_units:
        unit_context = f"{source_key} {raw_text}".casefold()
        if not any(str(unit).casefold() in unit_context for unit in accepted_units):
            note = f"{binding.label}: unit is not one of {accepted_units}"
            if binding.reject_invalid:
                return None, note

    outcome = apply_transform(binding.transform_type, config, value)
    if outcome.note and binding.reject_invalid:
        return None, f"{binding.label}: {outcome.note}"
    return DocumentValue(
        property_iri=binding.property_iri,
        label=binding.label,
        value=outcome.value,
        raw_value=raw_text,
        source_ref=source_ref,
        note=outcome.note,
    ), (f"{binding.label}: {outcome.note}" if outcome.note else None)


def _document_identity(
    structure: DocStructure,
    identity: IdentityProfile,
) -> str | None:
    if not identity.pattern:
        return None
    haystack = "\n".join([structure.title, *structure.paragraphs])
    match = re.search(identity.pattern, haystack)
    return match.group(0) if match else None


def _identity_from_raw(
    raw: dict[str, str],
    values: list[DocumentValue],
    identity: IdentityProfile,
    bindings: list[DocumentPropertyBinding],
) -> str | None:
    key = _matching_key(raw, identity.aliases)
    if key:
        value = str(raw[key]).strip()
        if identity.split:
            value = value.split(identity.split, 1)[0].strip()
        if value:
            return value

    by_key: dict[str, Any] = {}
    by_iri = {value.property_iri: value.value for value in values}
    for binding in bindings:
        if binding.property_iri in by_iri:
            by_key[binding.property_iri] = by_iri[binding.property_iri]
            by_key[_local_name(binding.property_iri)] = by_iri[binding.property_iri]
    parts = [str(by_key.get(field, "")).strip() for field in identity.fields]
    if parts and all(parts):
        return " / ".join(parts)

    identifier_binding = next(
        (binding for binding in bindings if binding.is_identifier), None
    )
    if identifier_binding and identifier_binding.property_iri in by_iri:
        value = str(by_iri[identifier_binding.property_iri]).strip()
        if identity.split:
            value = value.split(identity.split, 1)[0].strip()
        return value or None
    return None


def _candidate_identity(
    structure: DocStructure,
    raw: dict[str, str],
    values: list[DocumentValue],
    identity: IdentityProfile,
    bindings: list[DocumentPropertyBinding],
    fallback_label: str,
) -> str:
    return (
        _document_identity(structure, identity)
        or _identity_from_raw(raw, values, identity, bindings)
        or identity.fallback
        or fallback_label
    )


def _equipment_candidate_variants(
    raw: dict[str, str],
    values: list[DocumentValue],
    identity: IdentityProfile,
    bindings: list[DocumentPropertyBinding],
) -> list[tuple[str, list[DocumentValue], str, str]]:
    """Expand a device choice expression into independently resolvable candidates."""
    if not _EQUIPMENT_ID_ALIASES.intersection(identity.aliases):
        return []
    key = _matching_key(raw, identity.aliases)
    if not key:
        return []
    expression = str(raw[key]).strip()
    codes = list(dict.fromkeys(
        part.strip().upper()
        for part in _EQUIPMENT_CANDIDATE_SEPARATOR_RE.split(expression)
        if part.strip()
    ))
    if len(codes) < 2 or not all(_EQUIPMENT_CODE_RE.fullmatch(code) for code in codes):
        return []
    group = "|".join(codes)
    identifier_iris = {
        binding.property_iri for binding in bindings if binding.is_identifier
    }
    variants = []
    for code in codes:
        candidate_values = [
            DocumentValue(
                property_iri=value.property_iri,
                label=value.label,
                value=code if value.property_iri in identifier_iris else value.value,
                raw_value=value.raw_value,
                source_ref=value.source_ref,
                note=value.note,
            )
            for value in values
        ]
        variants.append((code, candidate_values, group, expression))
    return variants


def _subclass_value(
    profile: DocumentExtractionProfile,
    values: list[DocumentValue],
) -> str | None:
    if not profile.subclass_by:
        return None
    for value in values:
        if (
            value.property_iri == profile.subclass_by
            or _local_name(value.property_iri) == profile.subclass_by
        ):
            return str(value.value)
    return None


def _read_section(
    structure: DocStructure,
    target_class_iri: str,
    profile: DocumentExtractionProfile,
    source: LocatorProfile,
    bindings: list[DocumentPropertyBinding],
    matched: set[str],
) -> list[DocumentCandidate]:
    candidates: list[DocumentCandidate] = []
    identity = source.identity or profile.identity
    fallback = profile.label or source.source_label or _local_name(target_class_iri)
    for section_pos, section in enumerate(structure.sections):
        if not _section_match(section, source.anchor_any):
            continue
        values: list[DocumentValue] = []
        notes: list[str] = []
        raw: dict[str, str] = {}
        scoped_sections = [section]
        for descendant in structure.sections[section_pos + 1:]:
            if descendant.heading and descendant.level <= section.level:
                break
            scoped_sections.append(descendant)
        for scoped_section in scoped_sections:
            for offset, paragraph in enumerate(scoped_section.paras):
                match = _KV_RE.match(paragraph)
                if not match:
                    continue
                key = match.group(1).strip()
                raw_value = match.group(2).strip()
                if not raw_value:
                    continue
                raw[key] = raw_value
                binding = _binding_for_key(key, bindings)
                if binding is None:
                    continue
                paragraph_index = (
                    scoped_section.para_indices[offset]
                    if offset < len(scoped_section.para_indices)
                    else None
                )
                source_ref = {
                    "kind": "paragraph",
                    "section": scoped_section.heading,
                    "heading_index": scoped_section.heading_index,
                    "paragraph_index": paragraph_index,
                    "key": key,
                }
                value, note = _transform_value(
                    binding, raw_value, key, source_ref
                )
                matched.add(binding.property_iri)
                if value is not None:
                    values.append(value)
                if note:
                    notes.append(note)
        if values or notes:
            identifier = _candidate_identity(
                structure, raw, values, identity, bindings, fallback
            )
            candidates.append(DocumentCandidate(
                target_class_iri=target_class_iri,
                values=values,
                identifier=identifier,
                group_key=identifier,
                source_ref={
                    "kind": "section",
                    "section": section.heading,
                    "heading_index": section.heading_index,
                },
                notes=notes,
                subclass_value=_subclass_value(profile, values),
            ))
    return candidates


def _table_cell_ref(table: DocTable, row_pos: int, key: str) -> dict:
    try:
        column = table.headers.index(key)
    except ValueError:
        column = None
    raw_row = (
        table.row_indices[row_pos]
        if row_pos < len(table.row_indices)
        else table.header_row_count + row_pos
    )
    return {
        "kind": "table_cell",
        "table": table.table_index,
        "row": raw_row,
        "column": column,
        "header": key,
    }


def _read_table_rows(
    structure: DocStructure,
    target_class_iri: str,
    profile: DocumentExtractionProfile,
    source: LocatorProfile,
    bindings: list[DocumentPropertyBinding],
    matched: set[str],
) -> list[DocumentCandidate]:
    candidates: list[DocumentCandidate] = []
    identity = source.identity or profile.identity
    fallback = profile.label or source.source_label or _local_name(target_class_iri)
    for table in structure.tables:
        if not _headers_match(table, source.header_groups):
            continue
        col_assignment = _assign_columns(table.headers, bindings)
        for row_pos, row in enumerate(table.rows):
            if not any(value not in (None, "") for value in row.values()):
                continue
            values: list[DocumentValue] = []
            notes: list[str] = []
            for b_idx, binding in enumerate(bindings):
                key = col_assignment.get(b_idx)
                if key is None or row.get(key) in (None, ""):
                    continue
                source_ref = _table_cell_ref(table, row_pos, key)
                value, note = _transform_value(
                    binding, row[key], key, source_ref
                )
                matched.add(binding.property_iri)
                if value is not None:
                    values.append(value)
                if note:
                    notes.append(note)
            variants = _equipment_candidate_variants(row, values, identity, bindings)
            if not variants:
                identifier = _candidate_identity(
                    structure, row, values, identity, bindings, fallback
                )
                variants = [(identifier, values, None, None)]
            for identifier, candidate_values, candidate_group, expression in variants:
                candidates.append(DocumentCandidate(
                    target_class_iri=target_class_iri,
                    values=candidate_values,
                    identifier=identifier,
                    group_key=identifier,
                    source_ref={
                    "kind": "table_row",
                    "table": table.table_index,
                    "row": (
                        table.row_indices[row_pos]
                        if row_pos < len(table.row_indices)
                        else table.header_row_count + row_pos
                    ),
                    },
                    notes=notes,
                    subclass_value=_subclass_value(profile, candidate_values),
                    candidate_group=candidate_group,
                    candidate_expression=expression,
                ))
    return candidates


def _read_table_singleton(
    structure: DocStructure,
    target_class_iri: str,
    profile: DocumentExtractionProfile,
    source: LocatorProfile,
    bindings: list[DocumentPropertyBinding],
    matched: set[str],
) -> list[DocumentCandidate]:
    if source.orientation != "kv":
        # Column-oriented singleton is equivalent to the first table row.
        rows = _read_table_rows(
            structure, target_class_iri, profile, source, bindings, matched
        )
        return rows[:1]

    candidates: list[DocumentCandidate] = []
    identity = source.identity or profile.identity
    fallback = profile.label or source.source_label or _local_name(target_class_iri)
    for table in structure.tables:
        if not _headers_match(table, source.header_groups):
            continue
        values: list[DocumentValue] = []
        notes: list[str] = []
        raw: dict[str, str] = {}
        for row_pos, row in enumerate(table.rows):
            key_header = _matching_key(row, source.key_aliases)
            value_header = _matching_key(row, source.value_aliases)
            if not key_header or not value_header:
                continue
            parameter = str(row[key_header]).strip()
            raw_value = str(row[value_header]).strip()
            if not parameter or not raw_value:
                continue
            raw[parameter] = raw_value
            binding = _binding_for_key(parameter, bindings)
            if binding is None:
                continue
            source_ref = _table_cell_ref(table, row_pos, value_header)
            source_ref["parameter"] = parameter
            value, note = _transform_value(
                binding, raw_value, parameter, source_ref
            )
            matched.add(binding.property_iri)
            if value is not None:
                values.append(value)
            if note:
                notes.append(note)
        if values or notes:
            identifier = _candidate_identity(
                structure, raw, values, identity, bindings, fallback
            )
            candidates.append(DocumentCandidate(
                target_class_iri=target_class_iri,
                values=values,
                identifier=identifier,
                group_key=identifier,
                source_ref={
                    "kind": "table",
                    "table": table.table_index,
                },
                notes=notes,
                subclass_value=_subclass_value(profile, values),
            ))
    return candidates


def read_document_profile(
    structure: DocStructure,
    target_class_iri: str,
    profile: DocumentExtractionProfile,
    property_bindings: list[DocumentPropertyBinding],
) -> DocumentReadResult:
    try:
        matched: set[str] = set()
        candidates: list[DocumentCandidate] = []
        for source in profile.sources:
            if source.locator == "section_kv":
                candidates.extend(_read_section(
                    structure, target_class_iri, profile, source,
                    property_bindings, matched,
                ))
            elif source.locator == "table_rows":
                candidates.extend(_read_table_rows(
                    structure, target_class_iri, profile, source,
                    property_bindings, matched,
                ))
            elif source.locator == "table_singleton":
                candidates.extend(_read_table_singleton(
                    structure, target_class_iri, profile, source,
                    property_bindings, matched,
                ))
        drifted = [
            "|".join(binding.aliases) or binding.property_iri
            for binding in property_bindings
            if binding.property_iri not in matched
        ]
        return DocumentReadResult(
            candidates=candidates,
            drifted_paths=drifted,
        )
    except Exception as exc:
        logger.warning("document Profile execution degraded", exc_info=True)
        return DocumentReadResult(
            degraded_reason=f"document Profile failed: {type(exc).__name__}: {exc}"
        )


def read_doc_pattern(
    binding,
    property_bindings,
    target_class_iri: str,
    file_path: str | Path | None,
    source_filename: str | None = None,
):
    """Compile E6/E6b and return the same RowReadResult used by DB/API readers."""
    from app.services.extraction.db_reader import RowCandidate, RowReadResult
    from app.services.extraction.docx_structure import parse_docx_structure

    if file_path is None:
        return RowReadResult(degraded_reason="doc_pattern requires a DOCX file")
    structure = parse_docx_structure(file_path, source_filename=source_filename)
    if structure.warnings and not structure.sections and not structure.tables:
        return RowReadResult(degraded_reason="; ".join(structure.warnings))
    try:
        profile = parse_profile(binding.target)
        compiled = compile_e6_bindings(property_bindings)
    except DocumentProfileError as exc:
        return RowReadResult(degraded_reason=f"invalid doc_pattern Profile: {exc}")
    result = read_document_profile(
        structure, target_class_iri, profile, compiled
    )
    if result.degraded_reason:
        return RowReadResult(degraded_reason=result.degraded_reason)
    system = (getattr(binding, "source_system", None) or "doc_pattern").strip()
    filename = source_filename or structure.source_filename or Path(file_path).name
    candidates: list[RowCandidate] = []
    for candidate in result.candidates:
        source_ref = {
            "system": system,
            "entity": filename,
            "record": candidate.identifier or candidate.source_ref,
            "location": candidate.source_ref,
        }
        candidates.append(RowCandidate(
            candidate_kind="instance",
            extracted_properties=candidate.extracted_properties,
            source_ref=source_ref,
            identifier=candidate.identifier,
            target_class_iri=target_class_iri,
            notes=candidate.notes,
        ))
    return RowReadResult(
        candidates=candidates,
        drifted_paths=result.drifted_paths,
    )
