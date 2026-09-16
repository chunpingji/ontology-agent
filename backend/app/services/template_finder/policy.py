"""Exact template revision bindings; legacy metadata stays outside the ontology."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException

from app.config import settings

MODE = "finder_legacy"
PRIVATE_MODE = "finder_template_demo"
PROFILES = Path(__file__).with_name("profiles")
BUILTINS = {"cmc_baseline_v1": PROFILES / "cmc_baseline_v1.json"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def error(code, message, status=409):
    return HTTPException(status, {"code": code, "message": message})


@dataclass(frozen=True)
class Binding:
    root_class_iri: str
    profile: dict

    @property
    def profile_id(self):
        return self.profile["id"]


def profile_binding(template, profile_id, root):
    profile = json.loads(BUILTINS[profile_id].read_text())
    if root not in profile["root_classes"] or template.iri_pattern != root:
        raise ValueError("模板根类与本体指引1.0配置不一致")
    if (template.schema_json or {}).get("demo_profile"):
        raise ValueError("静态 demo_profile 与本体指引1.0绑定冲突")
    return Binding(root, profile)


def available_profiles(template):
    if (template.schema_json or {}).get("demo_profile"):
        return []
    profiles = []
    for profile_id in BUILTINS:
        try:
            profile_binding(template, profile_id, template.iri_pattern)
        except ValueError:
            continue
        profiles.append({"id": profile_id, "label": "CMC（本体指引1.0）"})
    return profiles


def resolve(template):
    try:
        mode = getattr(template, "recognition_mode", None)
        if mode == "ontology_guided":
            return None
        if mode == MODE:
            return profile_binding(template, template.finder_profile_id, template.iri_pattern)
        if mode is not None:
            raise ValueError("未知关系图谱识别引擎")
        config = json.loads(settings.template_finder_config_path.read_text())
        rows = config["bindings"]
        if not isinstance(rows, list):
            raise ValueError("bindings must be a list")
        ids = [str(UUID(row["template_id"])) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate template binding")
        matched = [row for row, tid in zip(rows, ids) if tid == str(template.id)]
        if not matched:
            return None
        row = matched[0]
        return profile_binding(template, row["finder_profile"], row["root_class_iri"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise error("FINDER_CONFIG_INVALID", f"本体指引1.0配置不可用：{exc}") from exc


def response_fields(template):
    binding = resolve(template)
    return {
        "recognition_mode": MODE if binding else "ontology_guided",
        "finder_profile_id": binding.profile_id if binding else None,
    }


def require_normal(template):
    if resolve(template):
        raise error("RECOGNITION_MODE_MISMATCH", "该模板使用本体指引1.0，请使用本体指引1.0识别入口")
