"""Tests for model-only email classification."""

import json

from backend import runtime


def test_classify_batch_returns_id_to_category_mapping(monkeypatch):
    expected_email = {"id": "mail-1", "subject": "Pay by noon", "body": "Please pay by noon."}
    captured = {}

    def fake_chat(messages, tools, **kwargs):
        captured["messages"] = messages
        captured["tools"] = tools
        return {
            "tool_calls": [{
                "function": {
                    "name": "return_email_categories",
                    "arguments": json.dumps({"assignments": [{"email_id": "mail-1", "category": "Urgent Action"}]}),
                }
            }]
        }

    monkeypatch.setattr(runtime, "chat", fake_chat)
    monkeypatch.setattr(runtime, "fetch_recent_emails", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gmail was accessed")))
    monkeypatch.setattr(runtime, "apply_user_label", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gmail label was applied")))

    result = runtime.classify_batch([expected_email])

    assert result == {"mail-1": "Urgent Action"}
    assert captured["tools"][0]["function"]["name"] == "return_email_categories"
    sent_payload = json.loads(captured["messages"][1]["content"])
    assert sent_payload["emails"][0]["id"] == "mail-1"


def test_classify_batch_rejects_unknown_category(monkeypatch):
    def fake_chat(*args, **kwargs):
        return {
            "tool_calls": [{
                "function": {
                    "name": "return_email_categories",
                    "arguments": json.dumps({"assignments": [{"email_id": "mail-1", "category": "Spam"}]}),
                }
            }]
        }

    monkeypatch.setattr(runtime, "chat", fake_chat)

    try:
        runtime.classify_batch([{"id": "mail-1"}])
    except ValueError as exc:
        assert "unknown category" in str(exc)
    else:
        raise AssertionError("Unknown category should be rejected")
