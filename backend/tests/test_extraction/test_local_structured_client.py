import json
from unittest.mock import MagicMock

from app.services.llm.local_client import _extract_json_object, chat_with_schema


def test_json_decoder_handles_braces_inside_strings_and_hidden_reasoning():
    expected = {"value": 'text } { and "quote'}
    assert _extract_json_object("prefix " + json.dumps(expected) + " suffix") == expected
    assert _extract_json_object('<think>{"fake":1}</think>```json\n{"real":2}\n```') == {"real": 2}
    assert _extract_json_object('<think>{"fake":1}') is None


def test_single_attempt_never_falls_back_or_exceeds_declared_tokens():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("offline")
    result = chat_with_schema(
        client,
        system="s",
        user="u",
        schema={"type": "object"},
        max_tokens=17,
        timeout_s=1,
        max_attempts=1,
    )
    assert result is None
    assert client.chat.completions.create.call_count == 1
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 17


def test_fallback_does_not_double_the_output_budget():
    client = MagicMock()
    reply = MagicMock()
    reply.choices[0].message.content = '{"ok":true}'
    client.chat.completions.create.side_effect = [RuntimeError("schema unsupported"), reply]
    assert chat_with_schema(client, system="s", user="u", schema={}, max_tokens=19) == {"ok": True}
    assert [
        call.kwargs["max_tokens"] for call in client.chat.completions.create.call_args_list
    ] == [19, 19]
