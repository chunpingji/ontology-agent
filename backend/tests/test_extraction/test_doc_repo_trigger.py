"""US2 抽取触发编排（T021；FR-007/Q1 手动发起）。

[content-extraction C1](../../../specs/007-rnd-document-fact-source/contracts/content-extraction-orchestration.md)：
文档 approved/新版本事件 → 编排**入待抽取队列**（`ExtractionJob(source_type='doc_repo',
status='pending', source_config={doc_ref,content_ref})`）；入队**不自动发起**抽取管线；
由授权角色经手动发起端点启动 `run_extraction_pipeline`；新旧版本溯源可区分。
"""

from __future__ import annotations

from uuid import UUID

from app.models.extraction import ExtractionCandidate, ExtractionJob

FACTS = "http://slpra.org/facts#"
DOC_IRI = f"{FACTS}doc-TTR-001"  # 稳定文档个体 IRI（溯源锚点，§4）


def _doc_config(client) -> str:
    """创建一个 doc_repo 抽取配置，返回 config_id。"""
    r = client.post("/api/extraction/configs", json={
        "name": "技术转移报告内部实体",
        "target_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "source_type": "doc_repo",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _enqueue(client, headers, cfg_id, *, doc_ref=DOC_IRI, content_ref="edms://doc/TTR-001/v2"):
    return client.post(
        "/api/extraction/jobs/from-document",
        json={"doc_ref": doc_ref, "content_ref": content_ref, "config_id": cfg_id},
        headers=headers,
    )


def test_approved_event_enqueues_pending_doc_repo_job(client, analyst_headers, db):
    """C1.1：approved 事件入队 → 创建 pending 的 doc_repo 作业，source_config 携 doc_ref/content_ref。"""
    cfg_id = _doc_config(client)
    r = _enqueue(client, analyst_headers, cfg_id)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["source_type"] == "doc_repo"
    assert job["status"] == "pending"

    row = db.get(ExtractionJob, UUID(job["id"]))
    assert row.status == "pending"
    assert row.source_config["doc_ref"] == DOC_IRI
    assert row.source_config["content_ref"] == "edms://doc/TTR-001/v2"


def test_enqueue_does_not_auto_start_pipeline(client, analyst_headers, db):
    """C1.2：入队不自动发起——作业停留 pending、零候选，管线未运行。"""
    cfg_id = _doc_config(client)
    job = _enqueue(client, analyst_headers, cfg_id).json()

    final = client.get(f"/api/extraction/jobs/{job['id']}").json()
    assert final["status"] == "pending"  # 未被自动推进
    assert final["total_candidates"] == 0
    cands = db.query(ExtractionCandidate).filter(
        ExtractionCandidate.job_id == UUID(job["id"])).all()
    assert cands == []  # 无任何候选 → 管线确未运行


def test_manual_start_requires_authorized_role(client, analyst_headers, operator_headers):
    """C1.2：手动发起端点经授权角色——operator 不可发起（403），senior_analyst 可。"""
    cfg_id = _doc_config(client)
    job = _enqueue(client, analyst_headers, cfg_id).json()

    forbidden = client.post(f"/api/extraction/jobs/{job['id']}/start", headers=operator_headers)
    assert forbidden.status_code == 403, forbidden.text

    ok = client.post(f"/api/extraction/jobs/{job['id']}/start", headers=analyst_headers)
    assert ok.status_code == 202, ok.text
    # 发起后立即离开 pending（端点同步置 running，后台运行管线）。
    assert ok.json()["status"] != "pending"


def test_manual_start_runs_pipeline_out_of_pending(client, analyst_headers, db):
    """C1.2：手动发起 → 作业离开 pending 进入处理态（非 failed 表示分支命中）。"""
    cfg_id = _doc_config(client)
    job = _enqueue(client, analyst_headers, cfg_id).json()

    client.post(f"/api/extraction/jobs/{job['id']}/start", headers=analyst_headers)
    final = client.get(f"/api/extraction/jobs/{job['id']}").json()
    assert final["status"] in ("running", "reviewing", "done")


def test_new_and_old_version_traceably_distinct(client, analyst_headers, db):
    """C1.3：新旧版本入队溯源可区分——content_ref 携版本指针，两作业可分辨。"""
    cfg_id = _doc_config(client)
    j_v1 = _enqueue(client, analyst_headers, cfg_id, content_ref="edms://doc/TTR-001/v1").json()
    j_v2 = _enqueue(client, analyst_headers, cfg_id, content_ref="edms://doc/TTR-001/v2").json()

    assert j_v1["id"] != j_v2["id"]
    r1 = db.get(ExtractionJob, UUID(j_v1["id"])).source_config
    r2 = db.get(ExtractionJob, UUID(j_v2["id"])).source_config
    # 同一稳定文档个体（§4 extractedFrom 锚点），版本指针经 content_ref 区分。
    assert r1["doc_ref"] == r2["doc_ref"] == DOC_IRI
    assert r1["content_ref"] != r2["content_ref"]
