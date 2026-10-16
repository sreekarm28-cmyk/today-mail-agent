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
        raise RuntimeError("Could not safely access Supabase data.") from exc


def save_gmail_connection(user_id: str, email: str, refresh_token: str) -> None:
    encrypted_token = _fernet().encrypt(refresh_token.encode("utf-8")).decode("ascii")
    path = "gmail_connections?on_conflict=user_id"
    _request(
        "POST",
        path,
        {
            "user_id": user_id,
            "gmail_email": email,
            "refresh_token_ciphertext": encrypted_token,
        },
        prefer="resolution=merge-duplicates,return=minimal",
    )


def get_gmail_connection(user_id: str) -> dict | None:
    path = (
        "gmail_connections?select=gmail_email,refresh_token_ciphertext&user_id=eq."
        f"{quote(user_id, safe='')}&limit=1"
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
    return {"email": row["gmail_email"], "refresh_token": refresh_token}


def save_persistent_user_tokens(
    user_id: str,
    email: str,
    access_token: str | None,
    refresh_token: str,
    token_expiry: str | None,
) -> None:
    """Store OAuth tokens encrypted in the new mailbox users table."""
    cipher = _fernet()
    payload = {
        "user_id": user_id,
        "email": email,
        "access_token": cipher.encrypt((access_token or "").encode("utf-8")).decode("ascii"),
        "refresh_token": cipher.encrypt(refresh_token.encode("utf-8")).decode("ascii"),
        "token_expiry": token_expiry,
    }
    _request(
        "POST",
        "users?on_conflict=user_id",
        payload,
        prefer="resolution=merge-duplicates,return=minimal",
    )


def get_mailbox_sync_state(user_id: str) -> dict | None:
    rows = _request(
        "GET",
        "sync_state?select=last_history_id,last_internal_date,initial_sync_complete,is_syncing,sync_started_at,last_synced_at"
        f"&user_id=eq.{quote(user_id, safe='')}&limit=1",
    )
    return rows[0] if rows else None


def save_mailbox_sync_state(user_id: str, values: dict) -> None:
    _request(
        "POST",
        "sync_state?on_conflict=user_id",
        {"user_id": user_id, **values},
        prefer="resolution=merge-duplicates,return=minimal",
    )


def acquire_mailbox_sync_lock(user_id: str, stale_before: str) -> bool:
    """Atomically claim this user's sync row; reclaim only an old stuck lock."""
    user_filter = quote(user_id, safe="")
    _request(
        "POST",
        "sync_state?on_conflict=user_id",
        {"user_id": user_id},
        prefer="resolution=ignore-duplicates,return=minimal",
    )
    rows = _request(
        "PATCH",
        "sync_state?user_id=eq." + user_filter
        + "&or=(is_syncing.eq.false,sync_started_at.lt." + quote(stale_before, safe="") + ")"
        + "&select=user_id",
        {"is_syncing": True, "sync_started_at": datetime.now(timezone.utc).isoformat()},
        prefer="return=representation",
    )
    return isinstance(rows, list) and bool(rows)


def release_mailbox_sync_lock(user_id: str, synced_at: str | None = None) -> None:
    """Release a user's sync lock even when Gmail or model work failed."""
    _request(
        "PATCH",
        "sync_state?user_id=eq." + quote(user_id, safe=""),
        {
            "is_syncing": False,
            "sync_started_at": None,
            **({"last_synced_at": synced_at} if synced_at else {}),
        },
        prefer="return=minimal",
    )


def update_mailbox_sync_state(user_id: str, values: dict) -> None:
    """Update only an existing user's sync cursor and timestamps."""
    _request(
        "PATCH",
        "sync_state?user_id=eq." + quote(user_id, safe=""),
        values,
        prefer="return=minimal",
    )


def upsert_mailbox_emails(
    user_id: str,
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
        rows.append(row)

    result_by_gmail_id = {}
    for offset in range(0, len(rows), 200):
        result = _request(
            "POST",
            "emails?on_conflict=user_id,gmail_message_id"
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


def update_mailbox_email(user_id: str, gmail_message_id: str, values: dict) -> None:
    """Update Gmail-derived fields for one saved message."""
    _request(
        "PATCH",
        "emails?user_id=eq." + quote(user_id, safe="")
        + "&gmail_message_id=eq." + quote(gmail_message_id, safe=""),
        values,
        prefer="return=minimal",
    )


def mark_mailbox_emails_deleted(user_id: str, gmail_message_ids: list[str]) -> None:
    """Soft-delete messages removed from Gmail or moved to Trash."""
    for gmail_id in gmail_message_ids:
        update_mailbox_email(user_id, gmail_id, {"deleted": True})


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


def get_unlabeled_mailbox_emails(user_id: str, limit: int = 100) -> list[dict]:
    """Load saved message text for later classification retries."""
    from .email_tool import normalize_email_body

    user_filter = quote(user_id, safe="")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    rows = _request(
        "GET",
        "emails?select=id,gmail_message_id,sender,subject,snippet,internal_date,gmail_labels,ai_label_status"
        f"&user_id=eq.{user_filter}&ai_label_status=in.(unlabeled,failed)"
        f"&internal_date=gte.{cutoff_ms}"
        f"&deleted=eq.false&hidden_in_app=eq.false"
        f"&order=internal_date.asc&limit={max(1, min(limit, 500))}",
    )
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


def get_persistent_gmail_ids_since(user_id: str, cutoff_ms: int) -> set[str]:
    """List saved Gmail IDs at or after a date, for expired-cursor reconciliation."""
    rows = _request(
        "GET",
        "emails?select=gmail_message_id"
        "&user_id=eq." + quote(user_id, safe="")
        + "&internal_date=gte." + str(max(0, int(cutoff_ms)))
        + "&deleted=eq.false&limit=1000",
    )
    return {
        row["gmail_message_id"]
        for row in rows or []
        if isinstance(row, dict) and row.get("gmail_message_id")
    }


def get_persistent_emails(user_id: str) -> list[dict]:
    """Read this user's non-deleted emails from the most recent rolling 24 hours."""
    rows = []
    offset = 0
    page_size = 500
    user_filter = quote(user_id, safe="")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
    while True:
        page = _request(
            "GET",
            "emails?select=id,gmail_message_id,sender,subject,snippet,internal_date,gmail_labels,ai_label,ai_label_status"
            f"&user_id=eq.{user_filter}&deleted=eq.false"
            f"&hidden_in_app=eq.false"
            f"&internal_date=gte.{cutoff_ms}"
            f"&order=internal_date.desc&limit={page_size}&offset={offset}",
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
        "emails?select=id,gmail_message_id,sender,subject,internal_date,gmail_labels,ai_label,ai_label_status"
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
        "from": row.get("sender", ""),
        "subject": row.get("subject", "(No subject)"),
        "received": received.isoformat(timespec="minutes"),
        "body": normalize_email_body(body_rows[0].get("body") or "") if body_rows else "",
        "labels": row.get("gmail_labels", []),
        "ai_label": row.get("ai_label") if row.get("ai_label_status") == "labeled" else None,
    }


def list_gmail_connections() -> list[dict[str, str]]:
    """List app-user IDs with a connected Gmail account, without returning tokens."""
    rows = _request(
        "GET",
        "gmail_connections?select=user_id&order=connected_at.asc&limit=1000",
    )
    if not isinstance(rows, list):
        raise RuntimeError("Supabase returned an invalid Gmail connection list.")
    connections = []
    for row in rows:
        user_id = row.get("user_id") if isinstance(row, dict) else None
        if isinstance(user_id, str) and user_id:
            connections.append({"user_id": user_id})
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
