"""Tests for user-approved Gmail replies and scheduled delivery."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import runtime


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
