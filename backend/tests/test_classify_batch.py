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


def test_gmail_label_failure_keeps_email_retryable(monkeypatch):
    updates = []
    email = {"id": "mail-1", "subject": "Pay by noon", "body": "Please pay by noon."}

    monkeypatch.setattr(runtime, "ensure_user_labels", lambda *args: [{"name": "Urgent Action", "id": "label-1"}])
    monkeypatch.setattr(runtime, "mark_mailbox_classification_attempted", lambda *args: None)
    monkeypatch.setattr(runtime, "classify_batch", lambda _emails: {"mail-1": "Urgent Action"})
    monkeypatch.setattr(
        runtime,
        "upsert_mailbox_emails",
        lambda *args: {"mail-1": {"id": "db-mail-1"}},
    )
    monkeypatch.setattr(
        runtime,
        "apply_user_label",
        lambda *args: (_ for _ in ()).throw(RuntimeError("Gmail unavailable")),
    )
    monkeypatch.setattr(
        runtime,
        "update_mailbox_email",
        lambda user_id, connection_id, gmail_id, values: updates.append(
            (user_id, connection_id, gmail_id, values)
        ),
    )

    classified, failed = runtime._classify_persistent_emails("user-1", "connection-1", [email])

    assert (classified, failed) == (0, 1)
    assert updates[0][:3] == ("user-1", "connection-1", "mail-1")
    assert updates[0][3]["ai_label_status"] == "failed"
