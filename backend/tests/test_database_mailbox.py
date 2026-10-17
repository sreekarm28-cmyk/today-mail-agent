"""Unit tests for the persistent Supabase mailbox helpers.

These tests mock the Supabase REST boundary, so they never access real mailbox data.
"""

from datetime import datetime, timezone

from backend import user_tokens


def test_email_upsert_uses_connection_and_gmail_id_as_conflict_key(monkeypatch):
    calls = []

    def fake_request(method, path, payload=None, prefer=None):
        calls.append((method, path, payload, prefer))
        return [{
            "id": "db-1",
            "gmail_message_id": "gmail-1",
            "ai_label": None,
            "ai_label_status": "unlabeled",
        }]

    monkeypatch.setattr(user_tokens, "_request", fake_request)

    result = user_tokens.upsert_mailbox_emails(
        "user-1",
        "connection-1",
        [{"id": "gmail-1", "internal_date": 1_797_000_000_000}],
    )

    assert result["gmail-1"]["id"] == "db-1"
    method, path, rows, prefer = calls[0]
    assert method == "POST"
    assert "on_conflict=connection_id,gmail_message_id" in path
    assert rows[0]["user_id"] == "user-1"
    assert rows[0]["connection_id"] == "connection-1"
    assert rows[0]["gmail_message_id"] == "gmail-1"
    assert "resolution=merge-duplicates" in prefer


def test_email_body_is_normalized_before_database_write(monkeypatch):
    calls = []
    monkeypatch.setattr(
        user_tokens,
        "_request",
        lambda method, path, payload=None, prefer=None: calls.append((method, path, payload)),
    )

    user_tokens.upsert_mailbox_email_bodies(
        "user-1",
        [{"email_id": "db-1", "user_id": "user-1", "body": "Hello\xa0  there\nNext"}],
    )

    method, path, rows = calls[0]
    assert method == "POST"
    assert path == "email_bodies?on_conflict=email_id"
    assert rows[0]["user_id"] == "user-1"
    assert rows[0]["body"] == "Hello there\nNext"


def test_open_email_reads_body_scoped_to_the_signed_in_user(monkeypatch):
    timestamp_ms = 1_797_000_000_000
    calls = []

    def fake_request(method, path, payload=None, prefer=None):
        calls.append(path)
        if path.startswith("emails?"):
            return [{
                "id": "db-1",
                "gmail_message_id": "gmail-1",
                "sender": "sender@example.com",
                "subject": "Update",
                "internal_date": timestamp_ms,
                "gmail_labels": ["INBOX"],
                "ai_label": "Updates",
                "ai_label_status": "labeled",
            }]
        return [{"body": "The saved email body."}]

    monkeypatch.setattr(user_tokens, "_request", fake_request)

    result = user_tokens.get_persistent_email_by_id("user-1", "db-1")

    assert result["body"] == "The saved email body."
    assert result["ai_label"] == "Updates"
    assert "user_id=eq.user-1" in calls[0]
    assert "deleted=eq.false" in calls[0]
    assert "user_id=eq.user-1" in calls[1]


def test_open_email_does_not_read_body_when_email_is_not_owned_or_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        user_tokens,
        "_request",
        lambda method, path, payload=None, prefer=None: calls.append(path) or [],
    )

    result = user_tokens.get_persistent_email_by_id("user-2", "db-1")

    assert result is None
    assert len(calls) == 1
    assert "user_id=eq.user-2" in calls[0]


def test_hide_email_is_a_user_scoped_soft_hide(monkeypatch):
    calls = []
    monkeypatch.setattr(
        user_tokens,
        "_request",
        lambda method, path, payload=None, prefer=None: calls.append(
            (method, path, payload)
        ) or [{"id": "db-1"}],
    )

    assert user_tokens.hide_persistent_email("user-1", "db-1") is True

    method, path, payload = calls[0]
    assert method == "PATCH"
    assert "user_id=eq.user-1" in path
    assert "hidden_in_app=eq.false" in path
    assert payload == {"hidden_in_app": True}


def test_dashboard_email_list_converts_saved_timestamp(monkeypatch):
    timestamp_ms = 1_797_000_000_000
    monkeypatch.setattr(
        user_tokens,
        "_request",
        lambda *args, **kwargs: [{
            "id": "db-1",
            "gmail_message_id": "gmail-1",
            "sender": "sender@example.com",
            "subject": "Update",
            "snippet": "Preview",
            "internal_date": timestamp_ms,
            "gmail_labels": ["INBOX"],
            "ai_label": "Updates",
            "ai_label_status": "labeled",
        }],
    )

    result = user_tokens.get_persistent_emails("user-1")

    expected = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    assert result[0]["received"] == expected.isoformat(timespec="minutes")
    assert result[0]["ai_label"] == "Updates"
    assert "Updates" in result[0]["labels"]
