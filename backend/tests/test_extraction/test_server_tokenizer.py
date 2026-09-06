import httpx
import pytest

from app.services.extraction.local_semantic_model import ServerTokenizer


def test_exact_local_server_tokenizer_pins_model_and_caches_counts():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/props":
            return httpx.Response(
                200, json={"model_path": "/models/fixed.gguf", "model_alias": "local"}
            )
        assert request.url.path == "/tokenize"
        return httpx.Response(200, json={"tokens": [17, 25, 9]})

    client = httpx.Client(base_url="http://localhost:8080", transport=httpx.MockTransport(handler))
    tokenizer = ServerTokenizer(
        "http://localhost:8080/v1", "a" * 64, "/models/fixed.gguf", client=client
    )
    assert tokenizer.count("中文🙂 250 mg") == tokenizer.count("中文🙂 250 mg") == 3
    assert calls.count("/tokenize") == 1
    with pytest.raises(ValueError, match="model"):
        ServerTokenizer("http://localhost:8080/v1", "a" * 64, "/models/other.gguf", client=client)


def test_server_tokenizer_has_no_character_count_fallback():
    client = httpx.Client(
        base_url="http://localhost:8080",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"model_path": "/models/fixed.gguf"})
        ),
    )
    tokenizer = ServerTokenizer(
        "http://localhost:8080/v1", "a" * 64, "/models/fixed.gguf", client=client
    )
    with pytest.raises(ValueError, match="tokens"):
        tokenizer.count("content")


def test_runtime_tokenizer_timeout_keeps_shared_ir_root_and_explicit_incomplete(tmp_path):
    from docx import Document

    from app.services.extraction.extraction_tasks import GenericExtractionRunner
    from app.services.extraction.hierarchical_context import TokenizationUnavailable
    from app.services.extraction.word_analysis import analyze_word_core

    class FailingTokenizer:
        identity = "unavailable-test"

        def count(self, text):
            raise TokenizationUnavailable("tokenizer_unavailable")

    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("生产信息")
    doc.save(source)
    runner = GenericExtractionRunner(
        {"urn:Report": {"properties": [], "relationships": []}},
        FailingTokenizer(),
        lambda *args: pytest.fail("must not call model"),
        model_identity="fixture",
    )
    result = runner.run(analyze_word_core(source).ir, effective_class="urn:Report")
    assert result.completion == "incomplete"
    assert result.diagnostics == ["tokenizer_unavailable"]
    assert len(result.candidates) == 1
