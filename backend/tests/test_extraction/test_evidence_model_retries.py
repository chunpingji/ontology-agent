import json

import httpx
import pytest

from app.schemas.evidence import TaskBudget
from app.services.extraction import local_semantic_model
from app.services.llm.local_client import LocalModelClient, StructuredModelError


@pytest.fixture
def model_runner(monkeypatch):
    """Use the real SDK/JSON adapter with an in-memory HTTP transport."""
    monkeypatch.setattr(local_semantic_model.settings, "local_llm_enabled", True)
    monkeypatch.setattr(local_semantic_model.settings, "local_llm_model_revision", "test-revision")
    monkeypatch.setattr(local_semantic_model.settings, "local_llm_base_url", "http://model.test/v1")
    monkeypatch.setattr(local_semantic_model.settings, "local_llm_api_key", "test-only")
    monkeypatch.setattr(local_semantic_model.settings, "local_llm_tokenizer_backend", "file")
    monkeypatch.setattr(local_semantic_model.settings, "evidence_timeout_s", 600.0)
    monkeypatch.setattr(local_semantic_model.settings, "evidence_timeout_retries", 3)
    monkeypatch.setattr(local_semantic_model, "LocalTokenizer", lambda _: object())
    monkeypatch.setattr(local_semantic_model, "semantic_schema_from_engine", lambda _: {})

    def create(outcomes):
        requests = []
        pending = iter(outcomes)

        def transport(request):
            requests.append(request)
            outcome = next(pending)
            if outcome == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if isinstance(outcome, int):
                return httpx.Response(outcome, json={"error": {"message": "synthetic error"}})
            return httpx.Response(200, json={
                "id": "test-completion", "object": "chat.completion", "created": 1,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": outcome}}],
            })

        client = LocalModelClient("http://model.test/v1", "test-only",
                                  transport=httpx.MockTransport(transport))
        monkeypatch.setattr(local_semantic_model, "get_local_llm", lambda: client)
        runner = local_semantic_model.configured_generic_runner(None)
        return runner, requests

    yield create


def invoke(runner, budget=None):
    return runner.model_call(
        "system", "source evidence", {"type": "object"}, budget or runner.budget
    )

def test_three_timeouts_then_success_uses_four_identical_requests(model_runner, caplog):
    runner, requests = model_runner(["timeout", "timeout", "timeout", '{"entities":[]}'])
    assert invoke(runner) == {"entities": []}
    assert len(requests) == 4
    assert all(request.content == requests[0].content for request in requests)
    for request in requests:
        assert all(0 < v <= 600 for v in request.extensions["timeout"].values())
        assert json.loads(request.content)["response_format"]["type"] == "json_schema"
    assert "retry 3/3" in caplog.text
    assert runner.budget.timeout_s == TaskBudget().timeout_s == 600.0
    assert runner.budget.timeout_retries == TaskBudget().timeout_retries == 3


def test_fourth_timeout_stops_without_a_fifth_request(model_runner):
    runner, requests = model_runner(["timeout"] * 4 + ['{"should_not_run":true}'])
    with pytest.raises(StructuredModelError, match="^model_timeout$"):
        invoke(runner)
    assert len(requests) == 4


@pytest.mark.parametrize("timeouts", [0, 1, 2])
def test_success_stops_retries_immediately(model_runner, timeouts):
    runner, requests = model_runner(["timeout"] * timeouts + ['{"ok":true}'])
    assert invoke(runner) == {"ok": True}
    assert len(requests) == timeouts + 1


@pytest.mark.parametrize("failure,code", [
    (500, "model_request_failed"), (429, "model_request_failed"),
    ("{invalid", "model_parse_error"),
])
def test_non_timeout_errors_are_not_retried_or_sent_to_fallback(model_runner, failure, code):
    runner, requests = model_runner([failure, '{"should_not_run":true}'])
    with pytest.raises(StructuredModelError, match=f"^{code}$"):
        invoke(runner)
    assert len(requests) == 1


def test_non_timeout_error_during_retry_stops_further_attempts(model_runner):
    runner, requests = model_runner(["timeout", 500, '{"should_not_run":true}'])
    with pytest.raises(StructuredModelError, match="^model_request_failed$"):
        invoke(runner)
    assert len(requests) == 2


def test_explicit_task_budget_can_disable_timeout_retries(model_runner):
    runner, requests = model_runner(["timeout", '{"should_not_run":true}'])
    with pytest.raises(StructuredModelError, match="^model_timeout$"):
        invoke(runner, TaskBudget(timeout_s=12, timeout_retries=0))
    assert len(requests) == 1
    assert set(requests[0].extensions["timeout"].values()) == {12.0}


def test_lease_loss_during_model_wait_prevents_timeout_retry(model_runner):
    from app.services.extraction.annotation_execution import ExecutionLost

    runner, requests = model_runner(["timeout", '{"should_not_run":true}'])

    def assert_owner():
        if requests:
            raise ExecutionLost()

    runner.assert_owner_fn = assert_owner
    with pytest.raises(ExecutionLost):
        invoke(runner)
    assert len(requests) == 1
