"""Tests for avoiding repeated dashboard Gmail syncs."""

from uuid import uuid4

from backend import web


def test_recent_sync_reuses_cache_instead_of_fetching_gmail(monkeypatch):
    user_id = str(uuid4())
    emails = [{
        "id": "mail-1",
        "from": "sender@example.com",
        "subject": "Update",
        "received": "2026-10-15T10:00:00+00:00",
        "preview": "A short preview",
        "labels": ["Updates"],
    }]
    cache = []
    fetch_calls = []

    def fake_fetch(request_user_id):
        fetch_calls.append(request_user_id)
        return emails

    monkeypatch.setattr(web, "fetch_recent_email_summaries", fake_fetch)
    monkeypatch.setattr(web, "_to_dashboard_items", lambda fetched: fetched)
    monkeypatch.setattr(web, "delete_expired_email_cache", lambda cutoff, request_user_id: None)
    monkeypatch.setattr(
        web,
        "replace_recent_email_cache",
        lambda request_user_id, rows: cache.__setitem__(slice(None), rows),
    )
    monkeypatch.setattr(web, "get_recent_email_cache", lambda request_user_id, cutoff: list(cache))
    web._dashboard_sync_last_success.pop(user_id, None)

    try:
        first = web.sync_dashboard_emails({"id": user_id})
        second = web.sync_dashboard_emails({"id": user_id})

        assert len(fetch_calls) == 1
        assert first["sync_status"] == "updated"
        assert second["sync_status"] == "reused"
        assert second["emails"] == emails
    finally:
        web._dashboard_sync_last_success.pop(user_id, None)
        web._dashboard_sync_locks.pop(user_id, None)
