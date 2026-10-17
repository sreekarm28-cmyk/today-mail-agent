"""Tests for dashboard synchronization through the persistent mailbox."""

from uuid import uuid4

from backend import web
from backend import runtime


def test_dashboard_sync_returns_saved_database_emails(monkeypatch):
    user_id = str(uuid4())
    emails = [{
        "id": "mail-1",
        "connection_id": "connection-1",
        "from": "sender@example.com",
        "subject": "Update",
        "received": "2026-10-15T10:00:00+00:00",
        "preview": "A short preview",
        "labels": ["Updates"],
    }]
    sync_calls = []

    def fake_connections(request_user_id):
        return [{"id": "connection-1", "gmail_email": "person@example.com"}]

    def fake_sync(request_user_id, user_email):
        sync_calls.append((request_user_id, user_email))
        return {"status": "success"}

    monkeypatch.setattr(web, "sync_persistent_mailbox", fake_sync)
    monkeypatch.setattr(web, "list_user_gmail_connections", fake_connections)
    monkeypatch.setattr(web, "get_persistent_emails", lambda request_user_id: emails)

    result = web.sync_dashboard_emails({"id": user_id, "email": "person@example.com"})

    assert sync_calls == [(user_id, "person@example.com")]
    assert result["sync_status"] == "success"
    assert result["emails"][0]["account_email"] == "person@example.com"
    assert result["emails"][0]["connection_id"] == "connection-1"


def test_dashboard_sync_runs_each_connected_gmail_account(monkeypatch):
    user_id = str(uuid4())
    connections = [
        {"id": "connection-1", "gmail_email": "first@example.com"},
        {"id": "connection-2", "gmail_email": "second@example.com"},
    ]
    sync_calls = []

    monkeypatch.setattr(runtime, "list_user_gmail_connections", lambda _user_id: connections)

    def fake_sync_one(request_user_id, connection_id):
        sync_calls.append((request_user_id, connection_id))
        return {"status": "success", "checked": 2, "classified": 1, "failed": 0}

    monkeypatch.setattr(runtime, "_sync_one_persistent_mailbox", fake_sync_one)

    result = runtime.sync_persistent_mailbox(user_id)

    assert sync_calls == [(user_id, "connection-1"), (user_id, "connection-2")]
    assert result == {
        "status": "success",
        "checked": 4,
        "classified": 2,
        "failed": 0,
        "accounts_processed": 2,
    }


def test_dashboard_sync_can_target_one_connected_gmail_account(monkeypatch):
    user_id = str(uuid4())
    connections = [
        {"id": "connection-1", "gmail_email": "first@example.com"},
        {"id": "connection-2", "gmail_email": "second@example.com"},
    ]
    sync_calls = []

    monkeypatch.setattr(runtime, "list_user_gmail_connections", lambda _user_id: connections)
    monkeypatch.setattr(
        runtime,
        "_sync_one_persistent_mailbox",
        lambda request_user_id, connection_id: sync_calls.append(connection_id)
        or {"status": "success", "checked": 0, "classified": 0, "failed": 0},
    )

    result = runtime.sync_persistent_mailbox(user_id, connection_id="connection-2")

    assert sync_calls == ["connection-2"]
    assert result["accounts_processed"] == 1
