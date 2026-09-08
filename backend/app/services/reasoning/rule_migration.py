"""Draft-only claim ledger for an exact historical rule definition."""

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash


def statements(value, path=()):
    """Retain exact text and offsets while splitting historical compound statements."""
    if isinstance(value, str):
        start = 0
        for index, char in enumerate(value):
            if char in "。；;\n":
                if value[start : index + 1].strip():
                    yield path, start, index + 1, value[start : index + 1]
                start = index + 1
        if value[start:].strip():
            yield path, start, len(value), value[start:]
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from statements(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from statements(child, (*path, index))
    else:
        yield path, None, None, deepcopy(value)


def migrate_rule(rule):
    original = {
        key: deepcopy(getattr(rule, key))
        for key in (
            "rule_key",
            "rule_group",
            "version",
            "status",
            "is_reviewed",
            "is_disabled",
            "antecedent",
            "consequent",
            "priority",
        )
    }
    original["id"] = str(rule.id)
    claims = []
    for path, start, end, text in statements(rule.consequent or {}):
        field = path[0]
        category = (
            "risk_decision"
            if field in {"risk_level", "postconditions"}
            else ("planned_control" if field == "traceability_docs" else "unresolved")
        )
        claims.append(
            {
                "claim_draft_id": evidence_hash([original, path, start, end]),
                "legacy_field": field,
                "original_statement": deepcopy(text),
                "legacy_path": list(path),
                "start_offset": start,
                "end_offset": end,
                "proposed_category": category,
                "target_claim_ref": None,
                "precondition": None,
                "subject_scope": None,
                "applicable_at": None,
                "universe_proof": None,
                "review_ref": None,
                "status": "unresolved",
                "issues": [
                    "CLAIM_PRECONDITION_UNPROVEN",
                    "EXACT_PARAMETER_MAPPING_REQUIRED",
                    "FALSE_UNKNOWN_MUST_NOT_IMPLY_LOW",
                ],
            }
        )
    return {
        "migration_plan_id": evidence_hash(original),
        "original": original,
        "original_hash": evidence_hash(original),
        "claims": claims,
        "target_rule_ref": None,
        "status": "draft",
        "business_reviewed": False,
        "consumer_entrypoints": ["legacy_report", "legacy_preview", "template_migration"],
        "required_tests": [
            "missing_evidence",
            "false",
            "unknown",
            "planned_only",
            "subject_mismatch",
            "time_mismatch",
            "incomplete_universe",
        ],
    }
