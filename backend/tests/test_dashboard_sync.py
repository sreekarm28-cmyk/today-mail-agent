"""Tests for dashboard synchronization through the persistent mailbox."""

from uuid import uuid4

from backend import web


def test_dashboard_sync_returns_saved_database_emails(monkeypatch):
    user_id = str(uuid4())
    emails = [{
        "id": "mail-1",
        "from": "sender@example.com",
        "subject": "Update",
        "received": "2026-10-15T10:00:00+00:00",
        "preview": "A short preview",
        "labels": ["Updates"],
    }]
    sync_calls = []

    def fake_sync(request_user_id, user_email):
        sync_calls.append((request_user_id, user_email))
        return {"status": "success"}

    monkeypatch.setattr(web, "sync_persistent_mailbox", fake_sync)
    monkeypatch.setattr(web, "get_persistent_emails", lambda request_user_id: emails)

    result = web.sync_dashboard_emails({"id": user_id, "email": "person@example.com"})

    assert sync_calls == [(user_id, "person@example.com")]
    assert result["sync_status"] == "success"
    assert result["emails"] == emails
