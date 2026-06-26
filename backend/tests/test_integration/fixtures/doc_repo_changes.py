"""确定性 doc_repo 测试夹具（007 US1；FR-015 parity）。

一条「归一化文档生命周期变更」样例（`inline` 形态，data-model §3）及其**等价 upload
载荷**——供逐字节 parity 断言（[doc-repo-connector C2.2](../../../../specs/007-rnd-document-fact-source/contracts/doc-repo-connector.md)）。
两者经各自接入模式归一化后 MUST 产出**同一变更骨架**（entity_id/entity_type/version/label/fields），
从而物化出逐字节一致的影子行。
"""

from __future__ import annotations

# 托管文档模块命名空间（与既有 …/slpra/<module>/ 体例一致；_detect_module 经 /slpra/document/ 归类）。
DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"

# 一条技术转移报告 v2 / 临床Ⅰ期 / 已批准（data-model §3 形状）。
DOC_REPO_CHANGE_TTR_V2: dict = {
    "entity_id": "doc-TTR-001",
    "entity_type": "TechTransferReport",
    "version": 2,
    "label": "XX 项目技术转移报告",
    "fields": {
        "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_ClinicalI",
        "documentVersion": "2",
        "approvalStatus": "approved",
        "sourceSystem": "EDMS-A",
        "contentHash": "sha256:1f3b9cda2e",
        "externalRef": "edms://doc/TTR-001/v2",
    },
}

# 同一文档的更早版本 v1（用于幂等/乱序与生命周期 supersede 场景）。
DOC_REPO_CHANGE_TTR_V1: dict = {
    "entity_id": "doc-TTR-001",
    "entity_type": "TechTransferReport",
    "version": 1,
    "label": "XX 项目技术转移报告",
    "fields": {
        "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_ClinicalI",
        "documentVersion": "1",
        "approvalStatus": "approved",
        "sourceSystem": "EDMS-A",
        "contentHash": "sha256:0a0a0a0a0a",
        "externalRef": "edms://doc/TTR-001/v1",
    },
}

# 一条不同阶段的稳定性报告（用于按阶段检索 US3）。
DOC_REPO_CHANGE_STAB_PRECLIN: dict = {
    "entity_id": "doc-STAB-009",
    "entity_type": "StabilityReport",
    "version": 1,
    "label": "YY 化合物稳定性报告",
    "fields": {
        "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_Preclinical",
        "documentVersion": "1",
        "approvalStatus": "approved",
        "sourceSystem": "EDMS-A",
        "contentHash": "sha256:beefbeef01",
        "externalRef": "edms://doc/STAB-009/v1",
    },
}

INLINE_CHANGES: list[dict] = [DOC_REPO_CHANGE_TTR_V2]

# 等价 upload 载荷：不同信封（doc_id/doc_type/title/metadata），归一化后 == DOC_REPO_CHANGE_TTR_V2。
UPLOAD_PAYLOAD: list[dict] = [
    {
        "doc_id": "doc-TTR-001",
        "doc_type": "TechTransferReport",
        "version": 2,
        "title": "XX 项目技术转移报告",
        "metadata": {
            "hasDevelopmentPhase": f"{DOCUMENT_NS}Phase_ClinicalI",
            "documentVersion": "2",
            "approvalStatus": "approved",
            "sourceSystem": "EDMS-A",
            "contentHash": "sha256:1f3b9cda2e",
            "externalRef": "edms://doc/TTR-001/v2",
        },
    }
]


def _copy_changes(changes: list[dict]) -> list[dict]:
    """深拷贝（含嵌套 fields），避免测试间共享可变状态。"""
    return [{**c, "fields": dict(c.get("fields") or {})} for c in changes]


def inline_config(*, changes: list[dict] | None = None, simulate: str | None = None) -> dict:
    """doc_repo `inline` 接入配置（connection_config）。"""
    cfg: dict = {
        "access_mode": "inline",
        "inline_changes": _copy_changes(changes if changes is not None else INLINE_CHANGES),
    }
    if simulate:
        cfg["simulate"] = simulate
    return cfg


def upload_config(*, payload: list[dict] | None = None) -> dict:
    """doc_repo `upload` 接入配置（connection_config）。"""
    src = payload if payload is not None else UPLOAD_PAYLOAD
    return {
        "access_mode": "upload",
        "upload_payload": [{**p, "metadata": dict(p.get("metadata") or {})} for p in src],
    }


def http_config(
    *,
    token_ref: str = "EDMS_TOKEN",
    base_url: str = "https://edms.internal/api/changes",
    simulate: str | None = None,
) -> dict:
    """doc_repo `http` 接入配置（US4，FR-001/010）。

    **仅含变量名引用**（`token_ref`）+ `base_url`——明文凭据 MUST NOT 入库（宪章安全）。
    真实端点返回的 EDMS 文档信封形态与 `upload` 一致，故经同一归一化产出逐字节同骨架。
    """
    cfg: dict = {"access_mode": "http", "base_url": base_url, "token_ref": token_ref}
    if simulate:
        cfg["simulate"] = simulate
    return cfg


def http_endpoint_envelopes(payload: list[dict] | None = None) -> list[dict]:
    """模拟真实 http 端点返回的文档信封（与 upload 同形态，供桩端点返回）。"""
    src = payload if payload is not None else UPLOAD_PAYLOAD
    return [{**p, "metadata": dict(p.get("metadata") or {})} for p in src]
