"""Encrypted per-user Gmail refresh-token storage in Supabase."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken


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


def _request(method: str, path: str, payload: dict | None = None, prefer: str | None = None):
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
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except (HTTPError, URLError, ValueError) as exc:
        raise RuntimeError("Could not safely access the Gmail connection in Supabase.") from exc


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
    try:
        refresh_token = _fernet().decrypt(row["refresh_token_ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, KeyError, UnicodeEncodeError) as exc:
        raise RuntimeError("The saved Gmail connection could not be unlocked. Contact the app owner.") from exc
    return {"email": row["gmail_email"], "refresh_token": refresh_token}
