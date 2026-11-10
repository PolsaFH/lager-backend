from __future__ import annotations

import pytest
from anthropic.types import Message

from lager import ai
from lager.ai import AIError, identify_component

from .conftest import make_settings


def message(content):
    return Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
        "stop_reason": "tool_use", "stop_sequence": None, "content": content,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


class FakeClient:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.kwargs = reply, exc, None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.reply


@pytest.fixture()
def use_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(ai, "_client", client)
        return client
    return install


def test_parses_forced_tool_call(use_client):
    client = use_client(FakeClient(message([{
        "type": "tool_use", "id": "t1", "name": ai.TOOL_NAME,
        "input": {"name": "BC547", "category": "Transistorer", "description": "d",
                  "specs": "NPN", "confidence": "high"},
    }])))
    out = identify_component(make_settings(), b"\x89PNG", "image/png")
    assert out["name"] == "BC547" and out["category"] == "Transistorer"
    assert client.kwargs["tool_choice"] == {"type": "tool", "name": ai.TOOL_NAME}
    assert client.kwargs["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


def test_unknown_category_and_confidence_are_normalised(use_client):
    use_client(FakeClient(message([{
        "type": "tool_use", "id": "t1", "name": ai.TOOL_NAME,
        "input": {"name": "x", "category": "Ukjent", "confidence": "very"},
    }])))
    out = identify_component(make_settings(), b"x", "image/png")
    assert out["category"] == "Annet" and out["confidence"] == "low" and out["specs"] == ""


def test_text_only_reply_is_an_error(use_client):
    use_client(FakeClient(message([{"type": "text", "text": "hmm"}])))
    with pytest.raises(AIError):
        identify_component(make_settings(), b"x", "image/png")


def test_upstream_failure_is_wrapped(use_client):
    use_client(FakeClient(exc=RuntimeError("401 invalid x-api-key sk-ant-secret")))
    with pytest.raises(AIError) as info:
        identify_component(make_settings(), b"x", "image/png")
    assert "sk-ant" not in str(info.value)
