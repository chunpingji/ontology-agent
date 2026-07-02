"""013 融合编辑器：Slot 的 `disabled` 是「仅编辑器内视觉标记」的往返契约。

用户选定语义（binding）：`disabled` 持久化进 `schema_json` 并由 GET 原样回传（供
融合编辑器重建灰显状态），但**绝不影响报告生成**——后端 `Slot` 模型（Pydantic v2
默认 ``extra="ignore"``）在 ``ReportTemplate.model_validate`` 时丢弃该未知键。
``create_template`` 校验、``update_template`` 校验、``resolve_template`` 取回三处
走的都是这同一调用，因此报告管线永远看不到 `disabled`。

该特性是**纯前端**改动；本模块锁定其唯一的后端保证——即无需任何后端管线改动、
无需给 `Slot` 增加字段、无需迁移。
"""

from __future__ import annotations

from app.services.reporting.ast_template import ReportTemplate, resolve_template

HEADERS = {"X-User": "analyst", "X-Role": "senior_analyst"}


def _schema_with_disabled_slot() -> dict:
    """一个最小合法 schema：两个 manual 插槽，第二个带编辑器写入的 `disabled: true`。"""
    return {
        "template_id": "disabled-roundtrip",
        "doc_no": "QS-A-020F05",
        "revision": "v1",
        "sections": [
            {
                "section_id": "sec1",
                "title": "分节一",
                "groups": [
                    {
                        "group_id": "grp1",
                        "title": "分组一",
                        "kind": "fields",
                        "slots": [
                            {
                                "slot_id": "slot_enabled",
                                "label": "启用插槽",
                                "source": {"kind": "manual"},
                                "required": False,
                                "on_missing": "annotate",
                                "missing_placeholder": "⚠ 待评估（数据缺失）",
                            },
                            {
                                "slot_id": "slot_disabled",
                                "label": "禁用插槽",
                                "source": {"kind": "manual"},
                                "required": False,
                                "on_missing": "annotate",
                                "missing_placeholder": "⚠ 待评估（数据缺失）",
                                # 融合编辑器写入的「仅编辑器内视觉标记」——后端应原样
                                # 持久化，但在报告校验时被 Slot 模型丢弃。
                                "disabled": True,
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _disabled_slot(schema_json: dict) -> dict:
    return schema_json["sections"][0]["groups"][0]["slots"][1]


def _create(client, name: str) -> str:
    r = client.post(
        "/api/ast-templates",
        json={"name": name, "version": "v1", "schema_json": _schema_with_disabled_slot()},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestDisabledSlotRoundTrip:
    def test_create_accepts_disabled_key(self, client, db):
        """POST 不因未知的 `disabled` 键 422——Slot 模型默认忽略额外键。"""
        r = client.post(
            "/api/ast-templates",
            json={
                "name": "禁用往返-create",
                "version": "v1",
                "schema_json": _schema_with_disabled_slot(),
            },
            headers=HEADERS,
        )
        assert r.status_code == 201, r.text

    def test_get_preserves_disabled_flag(self, client, db):
        """GET 原样回传 `disabled: true`（编辑器据此重建灰显状态）。"""
        tpl_id = _create(client, "禁用往返-get")

        got = client.get(f"/api/ast-templates/{tpl_id}", headers=HEADERS)
        assert got.status_code == 200, got.text
        slot = _disabled_slot(got.json()["schema_json"])
        assert slot["disabled"] is True
        # 启用的插槽自然没有该键。
        enabled = got.json()["schema_json"]["sections"][0]["groups"][0]["slots"][0]
        assert "disabled" not in enabled

    def test_report_validation_drops_disabled(self, client, db):
        """报告侧 ReportTemplate.model_validate（resolve_template 内部同一调用）丢弃 `disabled`。"""
        tpl_id = _create(client, "禁用往返-validate")
        schema_json = client.get(
            f"/api/ast-templates/{tpl_id}", headers=HEADERS
        ).json()["schema_json"]

        tpl = ReportTemplate.model_validate(schema_json)
        _, _, slot = list(tpl.iter_slots())[1]
        assert slot.slot_id == "slot_disabled"
        assert "disabled" not in slot.model_dump()  # 报告管线永不见到该键

    def test_resolve_template_drops_disabled(self, client, db):
        """端到端：设为默认后 resolve_template 取回，其 slot 无 `disabled`。"""
        tpl_id = _create(client, "禁用往返-resolve")
        # 设为默认，让 resolve_template 经 Tier-2（DB 默认模板）命中它。
        assert (
            client.post(f"/api/ast-templates/{tpl_id}/set-default", headers=HEADERS).status_code
            == 200
        )

        tpl, source, _ = resolve_template(None, db)
        assert source == "default"
        disabled = [s for _, _, s in tpl.iter_slots() if s.slot_id == "slot_disabled"]
        assert disabled, "默认模板应包含 slot_disabled"
        assert "disabled" not in disabled[0].model_dump()
