"""Per-user Gmail connection and short-lived email cache storage in Supabase."""

import json
import os
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
