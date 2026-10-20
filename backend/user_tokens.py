"""Per-user Gmail credentials and mailbox storage in Supabase."""

import json
import os
from datetime import datetime, timedelta, timezone
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken

from .diagnostics import log_timing


class GmailAccountLimitError(RuntimeError):
    """Raised when an app user already has the maximum connected inboxes."""


def _settings() -> tuple[str, str]:
    project_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret_key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not project_url or not secret_key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY on the server first.")
    return project_url, secret_key


def _fernet() -> Fernet:
    key = os.getenv("GMAIL_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("Set GMAIL_TOKEN_ENCRYPTION_KEY on the server first.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("GMAIL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def _request(method: str, path: str, payload: dict | list | None = None, prefer: str | None = None):
    project_url, secret_key = _settings()
    headers = {
        "apikey": secret_key,
        "Accept": "application/json",
    }
    # New Supabase secret keys belong in apikey; legacy service_role keys are JWTs.
    if not secret_key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {secret_key}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if prefer:
        headers["Prefer"] = prefer
    request = Request(f"{project_url}/rest/v1/{path}", data=body, headers=headers, method=method)
    started = perf_counter()
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
            result = json.loads(raw.decode("utf-8")) if raw else None
        log_timing(
            "supabase.request",
            started,
            method=method,
            resource=path.split("?", 1)[0],
            row_count=len(result) if isinstance(result, list) else (1 if result else 0),
        )
        return result
    except (HTTPError, URLError, ValueError) as exc:
        log_timing(
            "supabase.request",
            started,
            status="error",
            method=method,
            resource=path.split("?", 1)[0],
            error_type=type(exc).__name__,
        )
        if isinstance(exc, HTTPError):
            detail = exc.read().decode("utf-8", errors="replace")
            if "GMAIL_ACCOUNT_LIMIT" in detail:
                raise GmailAccountLimitError(
                    "You can connect up to two Gmail accounts."
                ) from exc
        raise RuntimeError("Could not safely access Supabase data.") from exc


def save_gmail_connection(user_id: str, email: str, refresh_token: str) -> dict:
    encrypted_token = _fernet().encrypt(refresh_token.encode("utf-8")).decode("ascii")
    rows = _request(
        "POST",
        "rpc/save_gmail_connection",
        {
            "p_user_id": user_id,
            "p_gmail_email": email,
            "p_refresh_token_ciphertext": encrypted_token,
        },
    )
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Supabase did not return the saved Gmail connection.")
    return rows[0]


def list_user_gmail_connections(user_id: str) -> list[dict]:
    rows = _request(
        "GET",
        "gmail_connections?select=id,user_id,gmail_email,connected_at&user_id=eq."
        f"{quote(user_id, safe='')}&order=connected_at.asc&limit=2",
    )
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid Gmail connection list.")
    return rows


def delete_gmail_connection(user_id: str, connection_id: str) -> bool:
    """Disconnect one of this user's Gmail accounts and cascade its app data."""
    rows = _request(
        "DELETE",
        "gmail_connections?select=id&user_id=eq."
        f"{quote(user_id, safe='')}&id=eq.{quote(connection_id, safe='')}",
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def get_gmail_connection(user_id: str, connection_id: str) -> dict | None:
    path = (
        "gmail_connections?select=id,user_id,gmail_email,refresh_token_ciphertext&user_id=eq."
        f"{quote(user_id, safe='')}&id=eq.{quote(connection_id, safe='')}&limit=1"
    )
    rows = _request("GET", path)
    if not rows:
        return None
    row = rows[0]
    decrypt_started = perf_counter()
    try:
        refresh_token = _fernet().decrypt(row["refresh_token_ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, KeyError, UnicodeEncodeError) as exc:
        log_timing("gmail.refresh_token_decrypt", decrypt_started, status="error")
        raise RuntimeError("The saved Gmail connection could not be unlocked. Contact the app owner.") from exc
    log_timing("gmail.refresh_token_decrypt", decrypt_started)
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "email": row["gmail_email"],
        "refresh_token": refresh_token,
    }


def get_gmail_connection_by_email(user_id: str, email: str) -> dict | None:
    rows = _request(
        "GET",
        "gmail_connections?select=id,user_id,gmail_email,refresh_token_ciphertext"
        f"&user_id=eq.{quote(user_id, safe='')}&gmail_email=eq.{quote(email, safe='')}&limit=1",
    )
    if not rows:
        return None
    row = rows[0]
    try:
        token = _fernet().decrypt(row["refresh_token_ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, KeyError, UnicodeEncodeError) as exc:
        raise RuntimeError("The saved Gmail connection could not be unlocked.") from exc
    return {"id": row["id"], "user_id": row["user_id"], "email": row["gmail_email"], "refresh_token": token}


def get_mailbox_sync_state(user_id: str, connection_id: str) -> dict | None:
    rows = _request(
        "GET",
        "sync_state?select=last_history_id,last_internal_date,initial_sync_complete,is_syncing,sync_started_at,last_synced_at"
        f"&user_id=eq.{quote(user_id, safe='')}&connection_id=eq.{quote(connection_id, safe='')}&limit=1",
    )
    return rows[0] if rows else None


def save_mailbox_sync_state(user_id: str, connection_id: str, values: dict) -> None:
    _request(
        "POST",
        "sync_state?on_conflict=connection_id",
        {"user_id": user_id, "connection_id": connection_id, **values},
        prefer="resolution=merge-duplicates,return=minimal",
    )


def acquire_mailbox_sync_lock(user_id: str, connection_id: str, stale_before: str) -> bool:
    """Atomically claim this mailbox's sync row; reclaim only an old stuck lock."""
    user_filter = quote(user_id, safe="")
    connection_filter = quote(connection_id, safe="")
    _request(
        "POST",
        "sync_state?on_conflict=connection_id",
        {"user_id": user_id, "connection_id": connection_id},
        prefer="resolution=ignore-duplicates,return=minimal",
    )
    rows = _request(
        "PATCH",
        "sync_state?user_id=eq." + user_filter
        + "&connection_id=eq." + connection_filter
        + "&or=(is_syncing.eq.false,sync_started_at.lt." + quote(stale_before, safe="") + ")"
        + "&select=user_id",
        {"is_syncing": True, "sync_started_at": datetime.now(timezone.utc).isoformat()},
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def release_mailbox_sync_lock(user_id: str, connection_id: str, synced_at: str | None = None) -> None:
    """Release a user's sync lock even when Gmail or model work failed."""
    _request(
        "PATCH",
        "sync_state?user_id=eq." + quote(user_id, safe="")
        + "&connection_id=eq." + quote(connection_id, safe=""),
        {
            "is_syncing": False,
            "sync_started_at": None,
            **({"last_synced_at": synced_at} if synced_at else {}),
        },
        prefer="return=minimal",
    )


def update_mailbox_sync_state(user_id: str, connection_id: str, values: dict) -> None:
    """Update only an existing mailbox's sync cursor and timestamps."""
    _request(
        "PATCH",
        "sync_state?user_id=eq." + quote(user_id, safe="")
        + "&connection_id=eq." + quote(connection_id, safe=""),
        values,
        prefer="return=minimal",
    )


def upsert_mailbox_emails(
    user_id: str,
    connection_id: str,
    emails: list[dict],
    classifications: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Upsert email metadata and return Gmail-ID to database-row mappings."""
    if not emails:
        return {}
    rows = []
    for email in emails:
        row = {
            "user_id": user_id,
            "connection_id": connection_id,
            "gmail_message_id": email["id"],
            "thread_id": email.get("thread_id", ""),
            "sender": email.get("from", ""),
            "subject": email.get("subject", "(No subject)"),
            "snippet": email.get("preview", ""),
            "internal_date": email["internal_date"],
            "gmail_labels": email.get("gmail_labels", []),
            "deleted": "TRASH" in email.get("gmail_labels", []),
        }
        if classifications is not None:
            category = classifications.get(email["id"])
            row["ai_label"] = category
            row["ai_label_status"] = "labeled" if category else "unlabeled"
            row["classification_attempted_at"] = datetime.now(timezone.utc).isoformat()
        rows.append(row)

    result_by_gmail_id = {}
    for offset in range(0, len(rows), 200):
        result = _request(
            "POST",
            "emails?on_conflict=connection_id,gmail_message_id"
            "&select=id,gmail_message_id,ai_label,ai_label_status",
            rows[offset : offset + 200],
            prefer="resolution=merge-duplicates,return=representation",
        )
        if not isinstance(result, list):
            raise RuntimeError("Supabase returned an invalid mailbox email response.")
        for row in result:
            result_by_gmail_id[row["gmail_message_id"]] = row
    return result_by_gmail_id


def upsert_mailbox_email_bodies(user_id: str, bodies: list[dict]) -> None:
    """Save full email text linked to its persistent email row."""
    if not bodies:
        return
    from .email_tool import normalize_email_body

    clean_bodies = [
        {**row, "body": normalize_email_body(str(row.get("body") or ""))}
        for row in bodies
    ]
    for offset in range(0, len(clean_bodies), 200):
        _request(
            "POST",
            "email_bodies?on_conflict=email_id",
            clean_bodies[offset : offset + 200],
            prefer="resolution=merge-duplicates,return=minimal",
        )


def update_mailbox_email(user_id: str, connection_id: str, gmail_message_id: str, values: dict) -> None:
    """Update Gmail-derived fields for one saved message."""
    _request(
        "PATCH",
        "emails?user_id=eq." + quote(user_id, safe="")
        + "&connection_id=eq." + quote(connection_id, safe="")
        + "&gmail_message_id=eq." + quote(gmail_message_id, safe=""),
        values,
        prefer="return=minimal",
    )


def mark_mailbox_classification_attempted(
    user_id: str, connection_id: str, gmail_message_ids: list[str]
) -> None:
    """Mark a batch as attempted before calling the model to avoid tight retries."""
    if not gmail_message_ids:
        return
    ids = ",".join(f'"{quote(message_id, safe="")}"' for message_id in gmail_message_ids)
    _request(
        "PATCH",
        "emails?user_id=eq." + quote(user_id, safe="")
        + "&connection_id=eq." + quote(connection_id, safe="")
        + "&gmail_message_id=in.(" + ids + ")",
        {
            "ai_label_status": "failed",
            "classification_attempted_at": datetime.now(timezone.utc).isoformat(),
        },
        prefer="return=minimal",
    )


def mark_mailbox_emails_deleted(user_id: str, connection_id: str, gmail_message_ids: list[str]) -> None:
    """Soft-delete messages removed from Gmail or moved to Trash."""
    for gmail_id in gmail_message_ids:
        update_mailbox_email(user_id, connection_id, gmail_id, {"deleted": True})


def hide_persistent_email(user_id: str, email_id: str) -> bool:
    """Hide one saved email from this user's app without changing Gmail."""
    rows = _request(
        "PATCH",
        "emails?id=eq." + quote(email_id, safe="")
        + "&user_id=eq." + quote(user_id, safe="")
        + "&hidden_in_app=eq.false&deleted=eq.false",
        {"hidden_in_app": True},
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def get_unlabeled_mailbox_emails(user_id: str, connection_id: str, limit: int = 100) -> list[dict]:
    """Load saved message text for later classification retries."""
    from .email_tool import normalize_email_body

    user_filter = quote(user_id, safe="")
    connection_filter = quote(connection_id, safe="")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    page_size = max(1, min(limit, 500))
    rows = []
    offset = 0
    while True:
        page = _request(
            "GET",
            "emails?select=id,gmail_message_id,sender,subject,snippet,internal_date,gmail_labels,ai_label_status,classification_attempted_at"
            f"&user_id=eq.{user_filter}&connection_id=eq.{connection_filter}"
            "&ai_label_status=in.(unlabeled,failed)"
            f"&internal_date=gte.{cutoff_ms}"
            f"&deleted=eq.false&hidden_in_app=eq.false"
            f"&order=internal_date.asc,id.asc&limit={page_size}&offset={offset}",
        )
        if not isinstance(page, list):
            raise RuntimeError("Supabase returned an invalid unlabeled email list.")
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    retry_before = datetime.now(timezone.utc) - timedelta(minutes=15)
    rows = [
        row for row in rows or []
        if row.get("ai_label_status") == "unlabeled"
        or not row.get("classification_attempted_at")
        or datetime.fromisoformat(row["classification_attempted_at"].replace("Z", "+00:00")) < retry_before
    ]
    body_by_email_id = {}
    for offset in range(0, len(rows or []), 100):
        email_ids = [row["id"] for row in rows[offset : offset + 100]]
        if not email_ids:
            continue
        body_rows = _request(
            "GET",
            "email_bodies?select=email_id,body&user_id=eq." + user_filter
            + "&email_id=in.(" + ",".join(email_ids) + ")&limit=100",
        )
        body_by_email_id.update({
            row["email_id"]: normalize_email_body(row.get("body") or "")
            for row in body_rows or []
            if isinstance(row, dict) and row.get("email_id")
        })
    pending = []
    for row in rows or []:
        pending.append({
            "id": row["gmail_message_id"],
            "database_id": row["id"],
            "from": row.get("sender", ""),
            "subject": row.get("subject", ""),
            "preview": row.get("snippet", ""),
            "internal_date": int(row["internal_date"]),
            "gmail_labels": row.get("gmail_labels", []),
            "received": datetime.fromtimestamp(int(row["internal_date"]) / 1000, tz=timezone.utc).isoformat(),
            "body": body_by_email_id.get(row["id"], ""),
        })
    return pending


def get_persistent_gmail_ids_since(user_id: str, connection_id: str, cutoff_ms: int) -> set[str]:
    """List saved Gmail IDs at or after a date, for expired-cursor reconciliation."""
    rows = _request(
        "GET",
        "emails?select=gmail_message_id"
        "&user_id=eq." + quote(user_id, safe="")
        + "&connection_id=eq." + quote(connection_id, safe="")
        + "&internal_date=gte." + str(max(0, int(cutoff_ms)))
        + "&deleted=eq.false&limit=1000",
    )
    return {
        row["gmail_message_id"]
        for row in rows or []
        if isinstance(row, dict) and row.get("gmail_message_id")
    }


def get_persistent_emails(user_id: str, connection_id: str | None = None) -> list[dict]:
    """Read this user's non-deleted emails from the most recent rolling 24 hours."""
    rows = []
    offset = 0
    page_size = 500
    user_filter = quote(user_id, safe="")
    connection_filter = quote(connection_id, safe="") if connection_id else None
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    while True:
        query = (
            "emails?select=id,gmail_message_id,connection_id,sender,subject,snippet,internal_date,gmail_labels,ai_label,ai_label_status"
            f"&user_id=eq.{user_filter}&deleted=eq.false"
        )
        if connection_filter:
            query += f"&connection_id=eq.{connection_filter}"
        query += (
            f"&hidden_in_app=eq.false&internal_date=gte.{cutoff_ms}"
            f"&order=internal_date.desc&limit={page_size}&offset={offset}"
        )
        page = _request(
            "GET",
            query,
        )
        if not isinstance(page, list):
            raise RuntimeError("Supabase returned an invalid mailbox email list.")
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    emails = []
    for row in rows:
        labels = row.get("gmail_labels", [])
        if not isinstance(labels, list):
            labels = []
        ai_label = row.get("ai_label") if row.get("ai_label_status") == "labeled" else None
        dashboard_labels = list(labels)
        if ai_label and ai_label not in dashboard_labels:
            dashboard_labels.append(ai_label)
        internal_date = int(row["internal_date"])
        received = datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc)
        emails.append({
            "id": row["id"],
            "gmail_message_id": row["gmail_message_id"],
            "connection_id": row.get("connection_id"),
            "from": row.get("sender", ""),
            "subject": row.get("subject", "(No subject)"),
            "received": received.isoformat(timespec="minutes"),
            "preview": row.get("snippet", ""),
            "labels": dashboard_labels,
            "ai_label": ai_label,
            "ai_label_status": row.get("ai_label_status", "unlabeled"),
        })
    return emails


def get_persistent_email_by_id(user_id: str, email_id: str) -> dict | None:
    """Read one email and body, restricted to the owning user."""
    from .email_tool import normalize_email_body

    user_filter = quote(user_id, safe="")
    email_filter = quote(email_id, safe="")
    rows = _request(
        "GET",
        "emails?select=id,gmail_message_id,connection_id,sender,subject,internal_date,gmail_labels,ai_label,ai_label_status"
        f"&id=eq.{email_filter}&user_id=eq.{user_filter}"
        "&deleted=eq.false&hidden_in_app=eq.false&limit=1",
    )
    if not rows:
        return None
    row = rows[0]
    body_rows = _request(
        "GET",
        "email_bodies?select=body"
        f"&email_id=eq.{email_filter}&user_id=eq.{user_filter}&limit=1",
    )
    internal_date = int(row["internal_date"])
    received = datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc)
    return {
        "id": row["id"],
        "gmail_message_id": row["gmail_message_id"],
        "connection_id": row.get("connection_id"),
        "from": row.get("sender", ""),
        "subject": row.get("subject", "(No subject)"),
        "received": received.isoformat(timespec="minutes"),
        "body": normalize_email_body(body_rows[0].get("body") or "") if body_rows else "",
        "labels": row.get("gmail_labels", []),
        "ai_label": row.get("ai_label") if row.get("ai_label_status") == "labeled" else None,
    }


def get_reminder_email(user_id: str, email_id: str) -> dict | None:
    """Return minimal email details only when it belongs to this user and is visible."""
    rows = _request(
        "GET",
        "emails?select=id&"
        f"id=eq.{quote(email_id, safe='')}&user_id=eq.{quote(user_id, safe='')}"
        "&deleted=eq.false&hidden_in_app=eq.false&limit=1",
    )
    return rows[0] if isinstance(rows, list) and rows else None


def create_user_reminder(
    user_id: str, email_id: str, remind_at: str, note: str
) -> dict:
    rows = _request(
        "POST",
        "reminders?select=id,email_id,remind_at,note,completed_at,created_at",
        {
            "user_id": user_id,
            "email_id": email_id,
            "remind_at": remind_at,
            "note": note,
        },
        prefer="return=representation",
    )
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Supabase did not return the saved reminder.")
    return rows[0]


def list_user_reminders(user_id: str) -> list[dict]:
    """Load this user's incomplete reminders and safe summary fields for their emails."""
    user_filter = quote(user_id, safe="")
    reminders = _request(
        "GET",
        "reminders?select=id,email_id,remind_at,note,completed_at,created_at"
        f"&user_id=eq.{user_filter}&completed_at=is.null"
        "&order=remind_at.asc&limit=500",
    )
    if not isinstance(reminders, list):
        raise RuntimeError("Supabase returned an invalid reminder list.")
    if not reminders:
        return []

    email_ids = sorted({str(row["email_id"]) for row in reminders if row.get("email_id")})
    if not email_ids:
        return []
    id_filter = ",".join(quote(email_id, safe="") for email_id in email_ids)
    emails = _request(
        "GET",
        "emails?select=id,subject,sender,internal_date,deleted,hidden_in_app"
        f"&user_id=eq.{user_filter}&id=in.({id_filter})&limit=500",
    )
    if not isinstance(emails, list):
        raise RuntimeError("Supabase returned invalid reminder email details.")
    email_by_id = {str(row["id"]): row for row in emails}
    result = []
    for reminder in reminders:
        email = email_by_id.get(str(reminder.get("email_id")), {})
        internal_date = int(email.get("internal_date") or 0)
        received = (
            datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc).isoformat(timespec="minutes")
            if internal_date else ""
        )
        result.append({
            **reminder,
            "subject": email.get("subject", "Email no longer available"),
            "sender": email.get("sender", ""),
            "received": received,
            "email_available": bool(email) and not email.get("deleted") and not email.get("hidden_in_app"),
        })
    return result


def update_user_reminder(
    user_id: str, reminder_id: str, remind_at: str, note: str
) -> bool:
    rows = _request(
        "PATCH",
        "reminders?select=id&"
        f"id=eq.{quote(reminder_id, safe='')}&user_id=eq.{quote(user_id, safe='')}"
        "&completed_at=is.null",
        {"remind_at": remind_at, "note": note},
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def complete_user_reminder(user_id: str, reminder_id: str) -> bool:
    rows = _request(
        "PATCH",
        "reminders?select=id&"
        f"id=eq.{quote(reminder_id, safe='')}&user_id=eq.{quote(user_id, safe='')}"
        "&completed_at=is.null",
        {"completed_at": datetime.now(timezone.utc).isoformat()},
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def delete_user_reminder(user_id: str, reminder_id: str) -> bool:
    rows = _request(
        "DELETE",
        "reminders?select=id&"
        f"id=eq.{quote(reminder_id, safe='')}&user_id=eq.{quote(user_id, safe='')}",
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def save_scheduled_reply(user_id: str, values: dict) -> dict:
    """Save a user-approved reply for the scheduled backend worker."""
    rows = _request(
        "POST",
        "scheduled_replies?select=id,status,send_at",
        {"user_id": user_id, **values},
        prefer="return=representation",
    )
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Supabase did not return the scheduled reply.")
    return rows[0]


def save_scheduled_message(user_id: str, values: dict) -> dict:
    """Save a user-approved new email for later delivery."""
    rows = _request(
        "POST",
        "scheduled_messages?select=id,status,send_at",
        {"user_id": user_id, **values},
        prefer="return=representation",
    )
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Supabase did not return the scheduled email.")
    return rows[0]


def claim_due_scheduled_replies(limit: int = 20) -> list[dict]:
    """Atomically claim due replies so overlapping timer runs do not duplicate work."""
    rows = _request("POST", "rpc/claim_due_scheduled_replies", {"p_limit": limit})
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid scheduled reply list.")
    return rows


def claim_due_scheduled_messages(limit: int = 20) -> list[dict]:
    """Atomically claim due new emails for one worker run."""
    rows = _request("POST", "rpc/claim_due_scheduled_messages", {"p_limit": limit})
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid scheduled email list.")
    return rows


def update_scheduled_reply_status(
    reply_id: str,
    user_id: str,
    status: str,
    *,
    sent_at: str | None = None,
    sent_gmail_message_id: str | None = None,
    last_error: str | None = None,
) -> bool:
    """Finish a claimed scheduled reply with a safe status and no provider details."""
    values = {"status": status, "last_error": last_error}
    if sent_at:
        values["sent_at"] = sent_at
    if sent_gmail_message_id:
        values["sent_gmail_message_id"] = sent_gmail_message_id
    rows = _request(
        "PATCH",
        "scheduled_replies?select=id&"
        f"id=eq.{quote(reply_id, safe='')}&user_id=eq.{quote(user_id, safe='')}&status=eq.sending",
        values,
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def update_scheduled_message_status(
    message_id: str,
    user_id: str,
    status: str,
    *,
    sent_at: str | None = None,
    sent_gmail_message_id: str | None = None,
    last_error: str | None = None,
) -> bool:
    """Update a claimed scheduled new email without exposing provider details."""
    values = {"status": status, "last_error": last_error}
    if sent_at:
        values["sent_at"] = sent_at
    if sent_gmail_message_id:
        values["sent_gmail_message_id"] = sent_gmail_message_id
    rows = _request(
        "PATCH",
        "scheduled_messages?select=id&"
        f"id=eq.{quote(message_id, safe='')}&user_id=eq.{quote(user_id, safe='')}&status=eq.sending",
        values,
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def search_recent_persistent_emails(
    user_id: str,
    query: str,
    connection_id: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search the user's last 24 hours of saved mail with PostgreSQL full-text search."""
    from .email_tool import normalize_email_body

    started = perf_counter()
    bounded_limit = max(1, min(int(limit), 5))
    rows = _request(
        "POST",
        "rpc/search_recent_emails",
        {
            "p_user_id": user_id,
            "p_query": query,
            "p_connection_id": connection_id,
            "p_limit": bounded_limit,
        },
    )
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid email search result.")

    matches = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        internal_date = int(row.get("internal_date") or 0)
        received = datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc)
        matches.append({
            "id": row["id"],
            "connection_id": row.get("connection_id"),
            "from": row.get("sender", ""),
            "subject": row.get("subject", "(No subject)"),
            "preview": row.get("snippet", ""),
            "received": received.isoformat(timespec="minutes"),
            "ai_label": row.get("ai_label"),
            "body": normalize_email_body(row.get("body") or ""),
        })
    log_timing("chat.database_search", started, result_count=len(matches), limit=bounded_limit)
    return matches


def get_email_embedding_chunks(
    user_id: str, email_ids: list[str]
) -> dict[str, list[str]]:
    """Load saved chunk text so unchanged email bodies are not embedded again."""
    if not email_ids:
        return {}
    grouped: dict[str, list[tuple[int, str]]] = {}
    user_filter = quote(user_id, safe="")
    # Keep the URL manageable and return only text needed for the equality check.
    for offset in range(0, len(email_ids), 100):
        batch = email_ids[offset : offset + 100]
        id_filter = quote(",".join(batch), safe=",")
        page_offset = 0
        while True:
            rows = _request(
                "GET",
                "email_embedding_chunks?select=email_id,chunk_index,chunk_text"
                f"&user_id=eq.{user_filter}&email_id=in.({id_filter})"
                f"&order=email_id.asc,chunk_index.asc&limit=1000&offset={page_offset}",
            )
            if not isinstance(rows, list):
                raise RuntimeError("Supabase returned invalid email embedding chunks.")
            for row in rows:
                if isinstance(row, dict) and row.get("email_id"):
                    grouped.setdefault(row["email_id"], []).append(
                        (int(row.get("chunk_index") or 0), str(row.get("chunk_text") or ""))
                    )
            if len(rows) < 1000:
                break
            page_offset += len(rows)
    return {
        email_id: [text for _, text in sorted(chunks)]
        for email_id, chunks in grouped.items()
    }


def save_email_embedding_chunks(
    user_id: str, email_id: str, chunks: list[dict]
) -> None:
    """Upsert one email's chunks, then remove stale trailing chunks."""
    user_filter = quote(user_id, safe="")
    email_filter = quote(email_id, safe="")
    if not chunks:
        _request(
            "DELETE",
            "email_embedding_chunks?user_id=eq." + user_filter
            + "&email_id=eq." + email_filter,
        )
        return

    rows = [
        {
            "email_id": email_id,
            "user_id": user_id,
            "chunk_index": index,
            "chunk_text": chunk["chunk_text"],
            "embedding": chunk["embedding"],
            "embedding_model": chunk["embedding_model"],
        }
        for index, chunk in enumerate(chunks)
    ]
    _request(
        "POST",
        "email_embedding_chunks?on_conflict=email_id,chunk_index",
        rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )
    _request(
        "DELETE",
        "email_embedding_chunks?user_id=eq." + user_filter
        + "&email_id=eq." + email_filter
        + "&chunk_index=gte." + str(len(chunks)),
    )


def get_recent_email_bodies_for_embedding(
    user_id: str,
    connection_id: str,
    force_email_ids: list[str] | None = None,
) -> list[dict]:
    """Load recent bodies that have no chunks, plus changed emails to refresh."""
    user_filter = quote(user_id, safe="")
    connection_filter = quote(connection_id, safe="")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    email_rows = []
    offset = 0
    while True:
        page = _request(
            "GET",
            "emails?select=id,connection_id,sender,subject,internal_date"
            f"&user_id=eq.{user_filter}&connection_id=eq.{connection_filter}"
            f"&deleted=eq.false&hidden_in_app=eq.false&internal_date=gte.{cutoff_ms}"
            f"&order=internal_date.desc&limit=500&offset={offset}",
        )
        if not isinstance(page, list):
            raise RuntimeError("Supabase returned an invalid recent email list.")
        email_rows.extend(page)
        if len(page) < 500:
            break
        offset += len(page)

    if not email_rows:
        return []
    all_ids = [row["id"] for row in email_rows if isinstance(row, dict) and row.get("id")]
    existing_ids = set()
    for batch_offset in range(0, len(all_ids), 100):
        batch = all_ids[batch_offset : batch_offset + 100]
        id_filter = quote(",".join(batch), safe=",")
        page_offset = 0
        while True:
            rows = _request(
                "GET",
                "email_embedding_chunks?select=email_id"
                f"&user_id=eq.{user_filter}&email_id=in.({id_filter})"
                f"&limit=1000&offset={page_offset}",
            )
            if not isinstance(rows, list):
                raise RuntimeError("Supabase returned invalid embedding references.")
            existing_ids.update(
                row["email_id"] for row in rows
                if isinstance(row, dict) and row.get("email_id")
            )
            if len(rows) < 1000:
                break
            page_offset += len(rows)

    forced = set(force_email_ids or [])
    pending_ids = [email_id for email_id in all_ids if email_id not in existing_ids or email_id in forced]
    if not pending_ids:
        return []

    bodies_by_id = {}
    for batch_offset in range(0, len(pending_ids), 100):
        batch = pending_ids[batch_offset : batch_offset + 100]
        id_filter = quote(",".join(batch), safe=",")
        rows = _request(
            "GET",
            "email_bodies?select=email_id,body"
            f"&user_id=eq.{user_filter}&email_id=in.({id_filter})&limit=1000",
        )
        if not isinstance(rows, list):
            raise RuntimeError("Supabase returned invalid recent email bodies.")
        for row in rows:
            if isinstance(row, dict) and row.get("email_id"):
                bodies_by_id[row["email_id"]] = row.get("body") or ""

    pending_set = set(pending_ids)
    return [
        {
            "id": row["id"],
            "sender": row.get("sender", ""),
            "subject": row.get("subject", "(No subject)"),
            "internal_date": int(row.get("internal_date") or 0),
            "body": bodies_by_id.get(row["id"], ""),
        }
        for row in email_rows
        if row.get("id") in pending_set
    ]


def search_email_embedding_chunks(
    user_id: str,
    query_embedding: list[float],
    connection_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search the user's last-day email chunks by cosine similarity."""
    if len(query_embedding) != 2048:
        raise ValueError("The query embedding must contain 2048 numbers.")
    started = perf_counter()
    bounded_limit = max(1, min(int(limit), 20))
    # PostgREST passes the pgvector value as its standard bracketed text form.
    vector_text = "[" + ",".join(format(float(value), ".9g") for value in query_embedding) + "]"
    rows = _request(
        "POST",
        "rpc/search_email_chunks",
        {
            "p_user_id": user_id,
            "p_connection_id": connection_id,
            "p_query_embedding": vector_text,
            "p_limit": bounded_limit,
        },
    )
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid semantic email search result.")
    matches = [row for row in rows if isinstance(row, dict) and row.get("email_id")]
    log_timing(
        "chat.semantic_search",
        started,
        result_count=len(matches),
        limit=bounded_limit,
    )
    return matches


def list_gmail_connections() -> list[dict[str, str]]:
    """List connected mailbox IDs and owners, without returning tokens."""
    rows = _request(
        "GET",
        "gmail_connections?select=id,user_id,gmail_email&order=connected_at.asc&limit=1000",
    )
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid Gmail connection list.")
    connections = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        user_id = row.get("user_id")
        connection_id = row.get("id")
        if isinstance(user_id, str) and user_id and isinstance(connection_id, str) and connection_id:
            connections.append({
                "user_id": user_id,
                "connection_id": connection_id,
                "email": row.get("gmail_email", ""),
            })
    return connections


def get_recent_email_cache(user_id: str, cutoff_iso: str) -> list[dict]:
    """Return this user's cached dashboard messages newer than the cutoff."""
    user_filter = quote(user_id, safe="")
    cutoff_filter = quote(cutoff_iso, safe="")
    path = (
        "recent_emails?select=gmail_message_id,sender,subject,received_at,preview,labels"
        f"&user_id=eq.{user_filter}&received_at=gte.{cutoff_filter}"
        "&order=received_at.desc&limit=500"
    )
    rows = _request("GET", path)
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid email cache response.")
    return [
        {
            "id": row["gmail_message_id"],
            "from": row.get("sender", ""),
            "subject": row.get("subject", "(No subject)"),
            "received": row["received_at"],
            "preview": row.get("preview", ""),
            "labels": row.get("labels", []),
        }
        for row in rows
    ]


def save_recent_email_cache(user_id: str, emails: list[dict]) -> None:
    """Upsert lightweight dashboard fields; never persist full email bodies."""
    if not emails:
        return
    rows = []
    for email in emails:
        rows.append({
            "user_id": user_id,
            "gmail_message_id": email["id"],
            "sender": email.get("from", ""),
            "subject": email.get("subject", "(No subject)"),
            "received_at": email["received"],
            "preview": email.get("preview", ""),
            "labels": email.get("labels", []),
        })
    _request(
        "POST",
        "recent_emails?on_conflict=user_id,gmail_message_id",
        rows,
        prefer="resolution=merge-duplicates,return=minimal",
    )


def replace_recent_email_cache(user_id: str, emails: list[dict]) -> None:
    """Replace one user's rolling dashboard snapshot with the latest Gmail inbox view."""
    _request(
        "DELETE",
        f"recent_emails?user_id=eq.{quote(user_id, safe='')}",
        prefer="return=minimal",
    )
    save_recent_email_cache(user_id, emails)


def delete_expired_email_cache(cutoff_iso: str, user_id: str | None = None) -> None:
    """Delete cached messages older than the cutoff, globally or for one user."""
    path = f"recent_emails?received_at=lt.{quote(cutoff_iso, safe='')}"
    if user_id:
        path += f"&user_id=eq.{quote(user_id, safe='')}"
    _request("DELETE", path, prefer="return=minimal")
