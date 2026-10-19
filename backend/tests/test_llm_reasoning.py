"""Tests that OpenRouter reasoning stays out of the tool-call output budget."""

import json

from backend import llm


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def test_chat_disables_reasoning_and_reports_reasoning_tokens(monkeypatch):
    captured = {}
    response = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [{
                    "function": {
                        "name": "return_inbox_answer",
                        "arguments": '{"answer":"Friday","source_ids":["sample-1"]}',
                    }
                }],
            },
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }

    def fake_urlopen(request, timeout):
        captured["request"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(response)

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm, "urlopen", fake_urlopen)

    message, diagnostics = llm.chat(
        [{"role": "user", "content": "Synthetic sample email question."}],
        [{"type": "function", "function": {"name": "return_inbox_answer"}}],
        tool_choice={"type": "function", "function": {"name": "return_inbox_answer"}},
        include_diagnostics=True,
    )

    assert captured["request"]["reasoning"] == {"enabled": False}
    assert captured["request"]["max_tokens"] == 1024
    assert diagnostics["reasoning_tokens"] == 0
    assert len(message["tool_calls"]) == 1
