"""Persist the template's display engine without changing its report schema."""

from sqlalchemy import update

from app.models.extraction import AstTemplate
from app.services import audit

from .policy import MODE, available_profiles, error, profile_binding, response_fields


def configuration(template):
    return {**response_fields(template), "finder_profiles": available_profiles(template)}


def save_configuration(db, template, request, actor):
    if (template.schema_json or {}).get("demo_profile"):
        raise error("STATIC_DEMO_TEMPLATE", "静态演示模板不支持切换识别引擎")
    current = response_fields(template)
    expected = {
        "recognition_mode": request.expected_recognition_mode,
        "finder_profile_id": request.expected_finder_profile_id,
    }
    if current != expected:
        raise error("RECOGNITION_CONFIG_CHANGED", "识别引擎设置已变更，请刷新后重试")
    if request.recognition_mode == MODE:
        try:
            profile_binding(template, request.finder_profile_id, template.iri_pattern)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise error(
                "FINDER_PROFILE_UNSUPPORTED", "请选择支持当前文档类型的本体指引1.0配置", 422
            ) from exc
    elif request.finder_profile_id is not None:
        raise error(
            "FINDER_PROFILE_UNEXPECTED",
            "文档结构解析＋本体指引引擎不能指定本体指引1.0配置",
            422,
        )

    desired = {
        "recognition_mode": request.recognition_mode,
        "finder_profile_id": request.finder_profile_id,
    }
    # Check stored values as well as the effective configuration. This works for
    # both SQLite and PostgreSQL and does not overwrite another editor's choice.
    changed = db.execute(
        update(AstTemplate)
        .where(
            AstTemplate.id == template.id,
            AstTemplate.recognition_mode == template.recognition_mode,
            AstTemplate.finder_profile_id == template.finder_profile_id,
            AstTemplate.iri_pattern == template.iri_pattern,
        )
        .values(**desired),
        execution_options={"synchronize_session": False},
    )
    if changed.rowcount != 1:
        db.rollback()
        raise error("RECOGNITION_CONFIG_CHANGED", "识别引擎设置已变更，请刷新后重试")
    audit.append(
        db,
        "template.recognition_engine_update",
        actor=actor,
        entity_iri=str(template.id),
        details={"before": current, "after": desired},
        commit=False,
    )
    db.commit()
    db.refresh(template)
    return configuration(template)
