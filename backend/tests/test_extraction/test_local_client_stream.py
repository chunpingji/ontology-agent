"""A completed Responses stream retains messages when its terminal output is empty."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.services.llm.local_client import _consume_response


class Reply(BaseModel):
    status: str
    output: list


class Stream:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def __aiter__(self):
        async def events():
            for event in self.events:
                yield event

        return events()


class Connection:
    def __init__(self, events):
        async def create(**_):
            return Stream(events)

        self.responses = SimpleNamespace(create=create)


@pytest.mark.parametrize("status,terminal_output,expected", [
    ("completed", [], ["first", "second"]),
    ("completed", ["terminal"], ["terminal"]),
    ("incomplete", [], []),
])
def test_streamed_items_fill_only_an_empty_completed_terminal_response(
    status, terminal_output, expected,
):
    events = [
        SimpleNamespace(type="response.output_item.done", output_index=1, item="second"),
        SimpleNamespace(type="response.output_item.done", output_index=0, item="first"),
        SimpleNamespace(type="response." + status,
                        response=Reply(status=status, output=terminal_output)),
    ]
    final = asyncio.run(_consume_response(Connection(events), {"stream": True}))
    assert final.status == status
    assert final.output == expected
