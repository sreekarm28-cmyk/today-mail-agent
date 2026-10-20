"""Tests for user-approved Gmail replies and scheduled delivery."""

from datetime import datetime, timedelta, timezone
from email import message_from_bytes
import base64

import pytest

from backend import email_tool, runtime


def test_schedule_approved_reply_stores_final_text_and_gmail_thread(monkeypatch):
    email = {"id": "db-email-1", "connection_id": "connection-1"}
    context = {
        "gmail_message_id": "gmail-message-1",
        "thread_id": "thread-1",
        "in_reply_to_message_id": "<original@example.com>",
        "references_header": "<older@example.com>",
        "recipient": "sender@example.com",
        "subject": "Project update",
    }
    captured = {}
    monkeypatch.setattr(runtime, "_load_reply_context", lambda *_: (email, context))

    def save(user_id, values):
        captured["user_id"] = user_id
        captured["values"] = values
        return {"id": "reply-1", "status": "pending", "send_at": values["send_at"]}

    monkeypatch.setattr(runtime, "save_scheduled_reply", save)
    send_at = datetime.now(timezone.utc) + timedelta(hours=1)

    result = runtime.schedule_approved_reply("user-1", "db-email-1", "  I can attend.  ", send_at)

    assert result["status"] == "pending"
    assert captured["user_id"] == "user-1"
    assert captured["values"]["body"] == "I can attend."
    assert captured["values"]["recipient_email"] == "sender@example.com"
    assert captured["values"]["thread_id"] == "thread-1"
    assert captured["values"]["connection_id"] == "connection-1"
    assert captured["values"]["send_at"].endswith("+00:00")


def test_schedule_approved_reply_rejects_past_time(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_load_reply_context",
        lambda *_: pytest.fail("Must reject the time before reading Gmail."),
    )

    with pytest.raises(ValueError, match="future"):
        runtime.schedule_approved_reply(
            "user-1", "email-1", "Reply", datetime.now(timezone.utc) - timedelta(minutes=1)
        )


def test_due_reply_is_sent_then_marked_sent(monkeypatch):
    row = {
        "id": "reply-1",
        "user_id": "user-1",
        "connection_id": "connection-1",
        "body": "Approved reply",
        "recipient_email": "sender@example.com",
        "subject": "Project update",
        "thread_id": "thread-1",
        "gmail_message_id": "gmail-message-1",
        "in_reply_to_message_id": "<original@example.com>",
        "references_header": "",
    }
    calls = []
    monkeypatch.setattr(runtime, "claim_due_scheduled_replies", lambda _limit: [row])
    monkeypatch.setattr(runtime, "send_gmail_reply", lambda *args: calls.append(("send", args)))
    monkeypatch.setattr(
        runtime,
        "update_scheduled_reply_status",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    result = runtime.process_due_scheduled_replies()

    assert result == {"replies_checked": 1, "replies_sent": 1, "replies_failed": 0}
    assert calls[0][0] == "send"
    assert calls[0][1][0:2] == ("user-1", "connection-1")
    assert calls[1][0][2] == "sent"


def test_failed_due_reply_is_recorded_without_stopping_batch(monkeypatch):
    row = {
        "id": "reply-1",
        "user_id": "user-1",
        "connection_id": "connection-1",
        "body": "Approved reply",
    }
    saved = []
    monkeypatch.setattr(runtime, "claim_due_scheduled_replies", lambda _limit: [row])
    monkeypatch.setattr(runtime, "send_gmail_reply", lambda *_: (_ for _ in ()).throw(RuntimeError("provider details")))
    monkeypatch.setattr(
        runtime,
        "update_scheduled_reply_status",
        lambda *args, **kwargs: saved.append((args, kwargs)) or True,
    )

    result = runtime.process_due_scheduled_replies()

    assert result == {"replies_checked": 1, "replies_sent": 0, "replies_failed": 1}
    assert saved[0][0][2] == "failed"
    assert "provider details" not in saved[0][1]["last_error"]


def test_schedule_approved_new_email_saves_sender_and_final_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        runtime,
        "get_gmail_connection",
        lambda user_id, connection_id: {"id": connection_id, "email": "me@example.com"},
    )

    def save(user_id, values):
        captured["user_id"] = user_id
        captured["values"] = values
        return {"id": "scheduled-1", "status": "pending", "send_at": values["send_at"]}

    monkeypatch.setattr(runtime, "save_scheduled_message", save)
    send_at = datetime.now(timezone.utc) + timedelta(days=1)

    result = runtime.schedule_approved_new_email(
        "user-1", "connection-1", "friend@example.com", "Hello", "  Approved text  ", send_at
    )

    assert result["status"] == "pending"
    assert captured["values"]["body"] == "Approved text"
    assert captured["values"]["connection_id"] == "connection-1"
    assert captured["values"]["recipient_email"] == "friend@example.com"


def test_due_new_email_is_sent_then_marked_sent(monkeypatch):
    row = {
        "id": "scheduled-1",
        "user_id": "user-1",
        "connection_id": "connection-1",
        "recipient_email": "friend@example.com",
        "subject": "Hello",
        "body": "Approved text",
    }
    sent = []
    updates = []
    monkeypatch.setattr(runtime, "claim_due_scheduled_messages", lambda _limit: [row])
    monkeypatch.setattr(runtime, "send_gmail_message", lambda *args: sent.append(args))
    monkeypatch.setattr(
        runtime,
        "update_scheduled_message_status",
        lambda *args, **kwargs: updates.append((args, kwargs)) or True,
    )

    result = runtime.process_due_scheduled_messages()

    assert result == {"messages_checked": 1, "messages_sent": 1, "messages_failed": 0}
    assert sent[0] == (
        "user-1", "connection-1", "friend@example.com", "Hello", "Approved text"
    )
    assert updates[0][0][2] == "sent"


def test_failed_due_new_email_is_recorded_with_safe_error(monkeypatch):
    row = {
        "id": "scheduled-1",
        "user_id": "user-1",
        "connection_id": "connection-1",
        "recipient_email": "friend@example.com",
        "subject": "Hello",
        "body": "Approved text",
    }
    updates = []
    monkeypatch.setattr(runtime, "claim_due_scheduled_messages", lambda _limit: [row])
    monkeypatch.setattr(runtime, "send_gmail_message", lambda *_: (_ for _ in ()).throw(RuntimeError("private provider body")))
    monkeypatch.setattr(
        runtime,
        "update_scheduled_message_status",
        lambda *args, **kwargs: updates.append((args, kwargs)) or True,
    )

    result = runtime.process_due_scheduled_messages()

    assert result == {"messages_checked": 1, "messages_sent": 0, "messages_failed": 1}
    assert updates[0][0][2] == "failed"
    assert "private provider body" not in updates[0][1]["last_error"]


def test_new_email_draft_uses_user_request_and_returns_editable_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        runtime, "get_gmail_connection",
        lambda *_: {"id": "connection-1", "email": "me@example.com"},
    )
    monkeypatch.setattr(runtime, "get_langfuse_client", lambda: None)

    def fake_chat(messages, tools, **kwargs):
        captured["messages"] = messages
        captured["tools"] = tools
        captured["kwargs"] = kwargs
        return {"content": "Hello, thanks for your message."}

    monkeypatch.setattr(runtime, "chat", fake_chat)

    result = runtime.create_new_email_draft(
        "user-1", "connection-1", "friend@example.com", "Hello", "Thank them."
    )

    assert result["draft"] == "Hello, thanks for your message."
    assert result["from_email"] == "me@example.com"
    assert captured["tools"] == []
    assert captured["kwargs"]["max_tokens"] == 700
    assert "Thank them." in captured["messages"][1]["content"]


def test_send_gmail_message_sends_plain_text_to_given_recipient(monkeypatch):
    captured = {}

    class SendRequest:
        def execute(self):
            return {"id": "gmail-sent-1"}

    class Messages:
        def send(self, *, userId, body):
            captured["user_id"] = userId
            captured["raw"] = body["raw"]
            captured["body"] = body
            return SendRequest()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(email_tool, "_gmail_service", lambda *_args, **_kwargs: Service())

    result = email_tool.send_gmail_message(
        "user-1", "connection-1", "friend@example.com", "Hello", "Approved text"
    )

    decoded = base64.urlsafe_b64decode(captured["raw"] + "===")
    message = message_from_bytes(decoded)
    assert result == "gmail-sent-1"
    assert captured["user_id"] == "me"
    assert captured["body"].keys() == {"raw"}
    assert message["To"] == "friend@example.com"
    assert message["Subject"] == "Hello"
    assert message.get_payload(decode=True).decode("utf-8").strip() == "Approved text"
