"""Read recent Gmail messages using Google OAuth and the Gmail API."""

from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import base64
import json
import os
from pathlib import Path
import re
from time import perf_counter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import keyring

from .diagnostics import log_timing


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
APP_DATA_DIR = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TodayMailAgent"
OAUTH_CLIENT_FILE = APP_DATA_DIR / "credentials.json"
_CREDENTIAL_SERVICE = "today-mail-agent"
_TOKEN_KEY_NAME = "GOOGLE_OAUTH_TOKEN"
GMAIL_BATCH_SIZE = 100


class _HTMLText(HTMLParser):
    """Collect readable text from an HTML-only email body."""

    _BLOCK_TAGS = {"address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "ol", "p", "pre", "section", "table", "tr", "ul"}

    def __init__(self) -> None:
        super().__init__()
        self.text = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "head"}:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and tag in self._BLOCK_TAGS:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag in self._BLOCK_TAGS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.text.append(data)

    def readable_text(self) -> str:
        text = "".join(self.text)
        text = re.sub(r"[\t\f\v ]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode(value: str | None) -> str:
    if not value:
        return ""
    pieces = []
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            pieces.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            pieces.append(part)
    return "".join(pieces)


def _body(message) -> str:
    parts = message.walk() if message.is_multipart() else [message]
    html_part = None
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
        elif part.get_content_type() == "text/html" and html_part is None:
            html_part = part

    if html_part:
        payload = html_part.get_payload(decode=True)
        if payload:
            parser = _HTMLText()
            parser.feed(payload.decode(html_part.get_content_charset() or "utf-8", errors="replace"))
            return parser.readable_text() or "(Email has no readable text.)"
    return "(No readable text body available.)"


def _credentials(user_id: str | None = None):
    """Load and refresh the local user's OAuth token, opening Google sign-in once if needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install requirements first: py -m pip install -r requirements.txt") from exc

    if user_id:
        from .user_tokens import get_gmail_connection

        connection = get_gmail_connection(user_id)
        if not connection:
            raise RuntimeError("Connect your Gmail account first.")
        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError("Google web OAuth is not configured on the server.")
        credentials = Credentials(
            token=None,
            refresh_token=connection["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        refresh_started = perf_counter()
        credentials.refresh(Request())
        log_timing("gmail.oauth_refresh", refresh_started)
        return credentials

    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved_token = os.getenv(_TOKEN_KEY_NAME) or keyring.get_password(_CREDENTIAL_SERVICE, _TOKEN_KEY_NAME)
    token_info = json.loads(saved_token) if saved_token else None
    credentials = Credentials.from_authorized_user_info(token_info) if token_info else None

    # A read-only token cannot apply labels. Start OAuth again so Google can
    # ask the user to approve the updated permission.
    if credentials and not set(SCOPES).issubset(set(credentials.scopes or [])):
        credentials = None

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    elif not credentials or not credentials.valid:
        if not OAUTH_CLIENT_FILE.exists():
            raise RuntimeError(
                "Gmail OAuth is not configured. Download a Desktop app OAuth JSON file "
                f"and save it as {OAUTH_CLIENT_FILE}."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CLIENT_FILE), SCOPES)
        credentials = flow.run_local_server(port=0, open_browser=True)

    token_json = credentials.to_json()
    os.environ[_TOKEN_KEY_NAME] = token_json
    keyring.set_password(_CREDENTIAL_SERVICE, _TOKEN_KEY_NAME, token_json)
    return credentials


def _gmail_service(user_id: str | None = None, credentials=None):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Install requirements first: py -m pip install -r requirements.txt") from exc
    return build(
        "gmail", "v1", credentials=credentials or _credentials(user_id), cache_discovery=False
    )


def connect_gmail_account() -> str:
    """Run the one-time browser consent flow and return the linked Gmail address."""
    try:
        profile = _gmail_service().users().getProfile(userId="me").execute()
    except Exception as exc:
        raise RuntimeError(f"Could not connect to Gmail: {exc}") from exc
    return profile.get("emailAddress", "Gmail account connected")


def gmail_account_email(credentials) -> str:
    """Return the email address belonging to an OAuth credential set."""
    profile = _gmail_service(credentials=credentials).users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")


def _local_timezone():
    zone_name = os.getenv("EMAIL_TIMEZONE")
    try:
        if not zone_name:
            return datetime.now().astimezone().tzinfo
        if zone_name == "Asia/Kolkata":
            # Windows Python installations may not include the IANA timezone database.
            return timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
        return ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Unknown EMAIL_TIMEZONE: {zone_name}") from exc


def _fetch_emails_in_range(start: datetime, end: datetime, user_id: str | None = None) -> list[dict[str, str]]:
    """Fetch inbox messages in a local-time interval and return their display data."""
    local_zone = _local_timezone()
    # Search broadly, then filter using Gmail's exact received timestamp.
    search_start = (start.astimezone(timezone.utc).date() - timedelta(days=1)).strftime("%Y/%m/%d")
    limit = max(1, min(int(os.getenv("EMAIL_MAX_RESULTS", "50")), 500))
    service_started = perf_counter()
    service = _gmail_service(user_id)
    log_timing("gmail.client_ready", service_started)
    list_started = perf_counter()
    response = service.users().messages().list(
        userId="me", q=f"in:inbox after:{search_start}", maxResults=limit
    ).execute()
    message_ids = response.get("messages", [])
    log_timing("gmail.message_list", list_started, candidate_count=len(message_ids))

    results = []
    content_started = perf_counter()
    for item in message_ids:
        raw_message = service.users().messages().get(
            userId="me", id=item["id"], format="raw"
        ).execute()
        raw = raw_message.get("raw")
        if not raw:
            continue
        padding = "=" * (-len(raw) % 4)
        message = message_from_bytes(base64.urlsafe_b64decode(raw + padding))

        internal_ms = raw_message.get("internalDate")
        received = (
            datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
            if internal_ms
            else parsedate_to_datetime(message.get("Date", "")) if message.get("Date") else None
        )
        if received is None:
            continue
        if received.tzinfo is None:
            received = received.replace(tzinfo=local_zone)
        received = received.astimezone(local_zone)
        if not start <= received < end:
            continue

        results.append({
            "id": item["id"],
            "from": _decode(message.get("From")),
            "subject": _decode(message.get("Subject")) or "(No subject)",
            "received": received.isoformat(timespec="minutes"),
            "body": _body(message)[:6000],
            "label_ids": raw_message.get("labelIds", []),
        })

    log_timing(
        "gmail.message_content",
        content_started,
        requested_count=len(message_ids),
        returned_count=len(results),
    )
    return results


def fetch_recent_emails(user_id: str | None = None) -> list[dict[str, str]]:
    """Fetch inbox messages received in the most recent rolling 24 hours."""
    now = datetime.now(_local_timezone())
    return _fetch_emails_in_range(now - timedelta(hours=24), now, user_id)


def _fetch_message_metadata_in_batches(service, message_ids: list[dict]) -> dict[str, dict]:
    """Fetch metadata for many Gmail messages using one HTTP batch per group."""
    messages_by_id: dict[str, dict] = {}
    errors: list[Exception] = []

    for offset in range(0, len(message_ids), GMAIL_BATCH_SIZE):
        batch_items = message_ids[offset : offset + GMAIL_BATCH_SIZE]
        request_ids = {
            str(index): item["id"]
            for index, item in enumerate(batch_items)
        }

        def on_response(request_id, response, exception):
            message_id = request_ids[request_id]
            if exception is not None:
                errors.append(exception)
            else:
                messages_by_id[message_id] = response

        batch = service.new_batch_http_request()
        for request_id, item in zip(request_ids, batch_items):
            request = service.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            )
            batch.add(request, request_id=request_id, callback=on_response)
        batch_started = perf_counter()
        batch.execute()
        log_timing(
            "gmail.message_metadata_batch",
            batch_started,
            request_count=len(batch_items),
        )

    if errors:
        raise errors[0]
    return messages_by_id


def fetch_recent_email_summaries(user_id: str | None = None) -> list[dict[str, str | list[str]]]:
    """Fetch lightweight inbox metadata for the dashboard, without email bodies."""
    local_zone = _local_timezone()
    now = datetime.now(local_zone)
    start = now - timedelta(hours=24)
    search_start = (start.astimezone(timezone.utc).date() - timedelta(days=1)).strftime("%Y/%m/%d")
    limit = max(1, min(int(os.getenv("EMAIL_MAX_RESULTS", "50")), 500))
    service_started = perf_counter()
    service = _gmail_service(user_id)
    log_timing("gmail.client_ready", service_started)
    list_started = perf_counter()
    response = service.users().messages().list(
        userId="me", q=f"in:inbox after:{search_start}", maxResults=limit
    ).execute()
    message_ids = response.get("messages", [])
    log_timing("gmail.message_list", list_started, candidate_count=len(message_ids))

    labels_started = perf_counter()
    gmail_labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_names_by_id = {
        label["id"]: label["name"]
        for label in gmail_labels
        if label.get("type") == "user" and label.get("id") and label.get("name")
    }
    log_timing("gmail.labels_list", labels_started, label_count=len(label_names_by_id))

    results: list[dict[str, str | list[str]]] = []
    metadata_started = perf_counter()
    messages_by_id = _fetch_message_metadata_in_batches(service, message_ids)
    for item in message_ids:
        message = messages_by_id[item["id"]]
        internal_ms = message.get("internalDate")
        if not internal_ms:
            continue
        received = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).astimezone(local_zone)
        if not start <= received <= now:
            continue
        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in message.get("payload", {}).get("headers", [])
        }
        results.append({
            "id": item["id"],
            "from": _decode(headers.get("from")),
            "subject": _decode(headers.get("subject")) or "(No subject)",
            "received": received.isoformat(timespec="minutes"),
            "preview": message.get("snippet", ""),
            "label_ids": message.get("labelIds", []),
            "labels": [
                label_names_by_id[label_id]
                for label_id in message.get("labelIds", [])
                if label_id in label_names_by_id
            ],
        })
    log_timing(
        "gmail.message_metadata",
        metadata_started,
        requested_count=len(message_ids),
        returned_count=len(results),
        batch_count=(len(message_ids) + GMAIL_BATCH_SIZE - 1) // GMAIL_BATCH_SIZE,
    )
    return results


def fetch_email_by_id(message_id: str, max_body_chars: int | None = 6000, user_id: str | None = None) -> dict[str, str]:
    """Fetch one Gmail message by ID, optionally limiting its body for model use."""
    local_zone = _local_timezone()
    fetch_started = perf_counter()
    message_data = _gmail_service(user_id).users().messages().get(
        userId="me", id=message_id, format="raw"
    ).execute()
    raw = message_data.get("raw")
    if not raw:
        raise RuntimeError("Gmail did not return the email content.")
    padding = "=" * (-len(raw) % 4)
    message = message_from_bytes(base64.urlsafe_b64decode(raw + padding))
    internal_ms = message_data.get("internalDate")
    received = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc) if internal_ms else None
    if received is None:
        raise RuntimeError("Gmail did not return the email's received time.")
    body = _body(message)
    if max_body_chars is not None:
        body = body[:max_body_chars]
    log_timing("gmail.selected_email_content", fetch_started, body_char_count=len(body))
    return {
        "id": message_id,
        "from": _decode(message.get("From")),
        "to": _decode(message.get("To")),
        "cc": _decode(message.get("Cc")),
        "subject": _decode(message.get("Subject")) or "(No subject)",
        "received": received.astimezone(local_zone).isoformat(timespec="minutes"),
        "body": body,
        "label_ids": message_data.get("labelIds", []),
    }


def ensure_user_labels(label_names: list[str], user_id: str | None = None) -> list[dict[str, str]]:
    """Return existing labels and create any requested custom labels that are missing."""
    started = perf_counter()
    service = _gmail_service(user_id)
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    by_name = {
        label["name"]: {"id": label["id"], "name": label["name"]}
        for label in labels
        if label.get("type") == "user" and label.get("id") and label.get("name")
    }

    created_count = 0
    for name in label_names:
        if name in by_name:
            continue
        created = service.users().labels().create(
            userId="me", body={"name": name}
        ).execute()
        by_name[name] = {"id": created["id"], "name": created["name"]}
        created_count += 1

    log_timing(
        "gmail.labels_ensure",
        started,
        label_count=len(label_names),
        created_count=created_count,
    )
    return list(by_name.values())


def apply_user_label(message_id: str, label_id: str, label_name: str, user_id: str | None = None) -> str:
    """Apply a previously verified existing user label to one Gmail message."""
    started = perf_counter()
    try:
        _gmail_service(user_id).users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()
    except Exception:
        log_timing("gmail.apply_label", started, status="error", category=label_name)
        raise
    log_timing("gmail.apply_label", started, category=label_name)
    return f"Applied the '{label_name}' label."
