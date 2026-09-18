"""Offline-only tokenization and a budgeted adapter to the configured local model."""

import logging
from collections import OrderedDict
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from threading import Lock

import httpx

from app.config import settings
from app.schemas.evidence import TaskBudget
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.extraction_tasks import (
    GenericExtractionRunner,
    semantic_schema_from_engine,
)
from app.services.extraction.hierarchical_context import TokenizationUnavailable
from app.services.extraction.performance import measure, timed
from app.services.llm.local_client import chat_with_schema, get_local_llm
from app.services.llm.model_runtime import model_scope

logger = logging.getLogger(__name__)
_counts_lock = Lock()


@lru_cache(maxsize=8)
def _shared_counts(identity, endpoint):
    return OrderedDict()


class LocalTokenizer:
    def __init__(self, path: str):
        from tokenizers import Tokenizer

        source = Path(path)
        if not source.is_file():
            raise ValueError("local tokenizer.json is unavailable")
        self.identity = sha256(source.read_bytes()).hexdigest()
        self.tokenizer = Tokenizer.from_file(str(source))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text).ids)


class ServerTokenizer:
    """Use the configured local GGUF server's actual vocabulary, never estimates.

    Deployment must verify model_revision against the delivered GGUF checksum.
    Checking /props ties tokenization to the same model path as inference.
    """

    def __init__(self, base_url, revision, model_path, *, client=None):
        if not revision or not model_path:
            raise ValueError("a pinned local model revision and model path are required")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/").removesuffix("/v1"), timeout=10, trust_env=False
        )
        self.model_path = model_path
        try:
            self.verify()
        except Exception:
            self.close()
            raise
        self.identity = stable_id("server-tokenizer", [revision, model_path, "llama-tokenize-v1"])
        self._counts = OrderedDict() if client is not None else _shared_counts(
            self.identity, base_url.rstrip("/"),
        )

    def close(self):
        if self._owns_client:
            self.client.close()

    def verify(self):
        response = self._request("GET", "/props")
        if response.json().get("model_path") != self.model_path:
            raise TokenizationUnavailable(
                "local tokenizer model differs from configured model artifact"
            )

    @timed("tokenizer_http")
    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            response.json()
            return response
        except (httpx.HTTPError, ValueError) as exc:
            raise TokenizationUnavailable("tokenizer_unavailable") from exc

    def count(self, text):
        key = sha256(text.encode()).hexdigest()
        with _counts_lock:
            if key in self._counts:
                self._counts.move_to_end(key)
                return self._counts[key]
        response = self._request(
            "POST",
            "/tokenize",
            json={"content": text, "add_special": True, "parse_special": False},
        )
        tokens = response.json().get("tokens")
        if not isinstance(tokens, list) or not all(isinstance(t, int) for t in tokens):
            raise TokenizationUnavailable("local tokenizer returned invalid tokens")
        with _counts_lock:
            if len(self._counts) >= 4096:
                self._counts.popitem(last=False)
            self._counts[key] = len(tokens)
        return len(tokens)


def configured_generic_runner(engine) -> GenericExtractionRunner:
    tokenizer, model_call = None, None
    schema = semantic_schema_from_engine(engine)
    identity = stable_id(
        "local-model",
        {
            "name": settings.local_llm_model,
            "revision": settings.local_llm_model_revision,
            "tokenizer_path": settings.local_llm_tokenizer_path,
            "tokenizer_backend": settings.local_llm_tokenizer_backend,
            "server_model_path": settings.local_llm_server_model_path,
        },
    )
    if settings.local_llm_enabled and settings.local_llm_model_revision:
        client = get_local_llm()
        if client is not None:
            try:
                if settings.local_llm_tokenizer_backend == "llama_server":
                    tokenizer = ServerTokenizer(
                        settings.local_llm_base_url,
                        settings.local_llm_model_revision,
                        settings.local_llm_server_model_path,
                    )
                else:
                    tokenizer = LocalTokenizer(settings.local_llm_tokenizer_path)
            except (ImportError, OSError, ValueError, httpx.HTTPError):
                tokenizer = None

            def model_call(system, user, response_schema, budget):
                if isinstance(tokenizer, ServerTokenizer):
                    tokenizer.verify()
                with model_scope(should_stop=runner.assert_owner_fn), measure("model"):
                    return chat_with_schema(
                        client, system=system, user=user, schema=response_schema,
                        schema_name="evidence_assertion", max_tokens=budget.max_output_tokens,
                        raise_on_error=True, timeout_s=budget.timeout_s, max_attempts=1,
                        timeout_retries=budget.timeout_retries,
                        total_timeout_s=settings.evidence_total_timeout_s,
                    )
            model_call.measures_model = True

    runner = GenericExtractionRunner(
        schema,
        tokenizer,
        model_call,
        model_identity=identity,
        compact_identifiers=True,
        citation_repair=True,
        relationship_priority=True,
        atomic_citations=True,
        record_level_targets=True,
        budget=TaskBudget(
            max_input_tokens=settings.evidence_max_input_tokens,
            max_output_tokens=settings.evidence_max_output_tokens,
            max_tasks=settings.evidence_max_tasks,
            max_regions_per_task=settings.evidence_max_regions_per_task,
            max_objects_per_task=settings.evidence_max_objects_per_task,
            timeout_s=settings.evidence_timeout_s,
            timeout_retries=settings.evidence_timeout_retries,
        ),
    )
    return runner
