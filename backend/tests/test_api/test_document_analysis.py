"""Stateless Word document-analysis API contract."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from app.api import document_analysis
from app.models.extraction import ExtractionJob


def _word_bytes(tmp_path: Path) -> bytes:
    document = Document()
    document.add_heading("第一章 总则", level=1)
    document.add_paragraph("这是无需图谱抽取的即时分析正文。")
    document.add_heading("1.1 范围", level=2)
    document.add_paragraph("本节描述适用范围。")
    path = tmp_path / "instant.docx"
    document.save(path)
    return path.read_bytes()


def test_word_analysis_is_stateless_and_returns_linked_tree(
    client, db, tmp_path, monkeypatch
):
    analyzed_paths: list[Path] = []
    real_parse = document_analysis.analyze_word_core

    def capture_parse(path, *args, **kwargs):
        analyzed_paths.append(Path(path))
        return real_parse(path, *args, **kwargs)

    monkeypatch.setattr(document_analysis, "analyze_word_core", capture_parse)
    monkeypatch.setattr(document_analysis, "get_local_llm", lambda: None)

    response = client.post(
        "/api/document-analysis/word",
        files={
            "file": (
                "即时分析.docx",
                _word_bytes(tmp_path),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["filename"] == "即时分析.docx"
    assert payload["content"]["type"] == "doc"
    assert payload["section_tree"]["node_type"] == "document"
    first = payload["section_tree"]["children"][0]
    assert first["heading"] == "第一章 总则"
    assert first["source_range"]["anchor_block_id"] == "paragraph:0:0"
    assert payload["pagination"]["mode"] == "single_page_fallback"
    assert payload["parser_version"] >= 1
    assert payload["summary_prompt_version"]
    assert len(analyzed_paths) == 1
    assert not analyzed_paths[0].exists()
    assert "triples" not in payload
    assert "relationships" not in payload
    assert "job_id" not in payload
    assert db.query(ExtractionJob).count() == 0


def test_word_analysis_uses_structure_only_preview(client, tmp_path, monkeypatch):
    captured = {}
    real_annotate = document_analysis.annotate_word

    def capture_annotate(path, engine, **kwargs):
        captured.update(engine=engine, **kwargs)
        return real_annotate(path, engine, **kwargs)

    monkeypatch.setattr(document_analysis, "annotate_word", capture_annotate)
    monkeypatch.setattr(document_analysis, "get_local_llm", lambda: None)

    response = client.post(
        "/api/document-analysis/word",
        files={"file": ("instant.docx", _word_bytes(tmp_path))},
    )

    assert response.status_code == 200, response.text
    assert captured["engine"] is None
    assert captured["structure_only"] is True
    assert captured["rich_style"] is True
    assert captured["structure"] is not None


def test_word_analysis_summary_failure_degrades_without_failing_request(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(document_analysis, "get_local_llm", lambda: object())
    monkeypatch.setattr(
        document_analysis,
        "summarize_word_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    response = client.post(
        "/api/document-analysis/word",
        files={"file": ("instant.docx", _word_bytes(tmp_path))},
    )

    assert response.status_code == 200, response.text
    root_metadata = response.json()["section_tree"]["layer_metadata"]
    assert root_metadata["summary_status"] == "failed"
    assert root_metadata["summary_source"] == "extractive_fallback"


def test_word_analysis_rejects_non_word_and_empty_uploads(client):
    unsupported = client.post(
        "/api/document-analysis/word",
        files={"file": ("notes.txt", b"text/plain")},
    )
    empty = client.post(
        "/api/document-analysis/word",
        files={"file": ("empty.docx", b"")},
    )

    assert unsupported.status_code == 422
    assert "仅支持" in unsupported.json()["detail"]
    assert empty.status_code == 422
    assert "为空" in empty.json()["detail"]


def test_word_analysis_reports_legacy_doc_conversion_failure(client, monkeypatch):
    async def fail_conversion(_path):
        raise document_analysis.DocConversionError("LibreOffice 不可用")

    monkeypatch.setattr(document_analysis, "ensure_docx_async", fail_conversion)

    response = client.post(
        "/api/document-analysis/word",
        files={"file": ("legacy.doc", b"legacy")},
    )

    assert response.status_code == 422
    assert "文档转换失败" in response.json()["detail"]
    assert "LibreOffice 不可用" in response.json()["detail"]
