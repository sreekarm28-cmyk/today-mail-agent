"""Read recent Gmail messages using Google OAuth and the Gmail API."""

from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser
import base64
import json
import os
from pathlib import Path
import re
from time import perf_counter

import keyring

from .diagnostics import log_timing


SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
APP_DATA_DIR = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TodayMailAgent"
OAUTH_CLIENT_FILE = APP_DATA_DIR / "credentials.json"
_CREDENTIAL_SERVICE = "today-mail-agent"
_TOKEN_KEY_NAME = "GOOGLE_OAUTH_TOKEN"
GMAIL_RAW_BATCH_SIZE = 10


class GmailSendPermissionError(RuntimeError):
    """The connected Gmail account has not granted permission to send mail."""


class GmailDeliveryUnknownError(RuntimeError):
    """Gmail may have accepted a message, but the client did not get confirmation."""


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
        return normalize_email_body("".join(self.text))


def normalize_email_body(text: str) -> str:
    """Clean email text, dropping unsubscribe boilerplate but preserving useful links."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    source_lines = text.split("\n")
    kept_lines = []
    skip_following_url = False
    for line in source_lines:
        cleaned_line = re.sub(r"\s+", " ", line).strip()
        has_footer_link_text = bool(re.search(
            r"\b(unsubscribe|opt[ -]?out|manage (?:your )?(?:email )?preferences|email preferences|view (?:this )?email (?:in )?(?:your )?browser)\b",
            cleaned_line,
            re.I,
        ))
        is_url_only = bool(re.fullmatch(r"[<(\[]*https?://\S+[)>\]]*", cleaned_line, re.I))
        if has_footer_link_text:
            skip_following_url = not is_url_only
            continue
        if skip_following_url and is_url_only:
            skip_following_url = False
            continue
        skip_following_url = False
        kept_lines.append(cleaned_line)

    lines = kept_lines
    normalized = []
    for line in lines:
        if line:
            normalized.append(line)
        elif normalized and normalized[-1] != "":
            normalized.append("")
    return "\n".join(normalized).strip()


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
                return normalize_email_body(
                    payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                )
        elif part.get_content_type() == "text/html" and html_part is None:
            html_part = part

    if html_part:
        payload = html_part.get_payload(decode=True)
        if payload:
            parser = _HTMLText()
            parser.feed(payload.decode(html_part.get_content_charset() or "utf-8", errors="replace"))
            return parser.readable_text() or "(Email has no readable text.)"
    return "(No readable text body available.)"


def _credentials(user_id: str | None = None, connection_id: str | None = None):
    """Load and refresh the local user's OAuth token, opening Google sign-in once if needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install requirements first: py -m pip install -r requirements.txt") from exc

    if user_id:
        from .user_tokens import get_gmail_connection

        if not connection_id:
            raise RuntimeError("Choose a connected Gmail account first.")
        connection = get_gmail_connection(user_id, connection_id)
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


def _gmail_service(
    user_id: str | None = None, connection_id: str | None = None, credentials=None
):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Install requirements first: py -m pip install -r requirements.txt") from exc
    return build(
        "gmail", "v1",
        credentials=credentials or _credentials(user_id, connection_id),
        cache_discovery=False,
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


def get_gmail_reply_context(
    user_id: str, connection_id: str, gmail_message_id: str
) -> dict[str, str]:
    """Read Gmail headers needed to address and thread a reply correctly."""
    started = perf_counter()
    message = _gmail_service(user_id, connection_id).users().messages().get(
        userId="me",
        id=gmail_message_id,
        format="metadata",
        metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"],
    ).execute()
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in (message.get("payload") or {}).get("headers", [])
        if isinstance(item, dict)
    }
    recipient_header = headers.get("reply-to") or headers.get("from", "")
    addresses = getaddresses([recipient_header])
    recipient = next((address for _, address in addresses if address), "")
    message_id = headers.get("message-id", "").strip()
    thread_id = str(message.get("threadId") or "")
    if not recipient or not message_id or not thread_id:
        raise RuntimeError("Gmail did not provide enough message details to reply safely.")
    log_timing("gmail.reply_context", started)
    original_subject = _decode(headers.get("subject")) or "(No subject)"
    return {
        "recipient": recipient,
        "subject": original_subject,
        "thread_id": thread_id,
        "gmail_message_id": gmail_message_id,
        "in_reply_to_message_id": message_id,
        "references_header": headers.get("references", "").strip(),
    }


def send_gmail_reply(
    user_id: str,
    connection_id: str,
    reply_context: dict[str, str],
    body: str,
    *,
    message_id: str | None = None,
) -> str:
    """Send an explicitly approved reply into the original Gmail thread."""
    if not body.strip():
        raise ValueError("Reply text cannot be empty.")
    message = EmailMessage(policy=policy.SMTP)
    message["To"] = reply_context["recipient"]
    subject = reply_context["subject"]
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    original_message_id = reply_context["in_reply_to_message_id"]
    message["In-Reply-To"] = original_message_id
    references = reply_context.get("references_header", "").strip()
    message["References"] = f"{references} {original_message_id}".strip()
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body.strip())
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")

    started = perf_counter()
    service = _gmail_service(user_id, connection_id)
    try:
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": reply_context["thread_id"]},
        ).execute()
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 403:
            raise GmailSendPermissionError(
                "Approve Gmail's send permission before sending replies."
            ) from exc
        if isinstance(status, int) and 400 <= status < 500:
            raise
        raise GmailDeliveryUnknownError(
            "Gmail may have accepted the reply, but did not confirm the result."
        ) from exc
    if not isinstance(result, dict) or not result.get("id"):
        raise GmailDeliveryUnknownError(
            "Gmail may have accepted the reply, but did not confirm the result."
        )
    log_timing("gmail.send_reply", started)
    return str(result["id"])


def send_gmail_message(
    user_id: str,
    connection_id: str,
    recipient: str,
    subject: str,
    body: str,
    *,
    message_id: str | None = None,
) -> str:
    """Send a new email after the user has reviewed and approved its text."""
    addresses = getaddresses([recipient])
    parsed = [address for _, address in addresses if address]
    if not parsed or any("@" not in address for address in parsed):
        raise ValueError("Enter a valid recipient email address.")
    if not subject.strip() or not body.strip():
        raise ValueError("Email subject and message cannot be empty.")
    message = EmailMessage(policy=policy.SMTP)
    message["To"] = ", ".join(parsed)
    message["Subject"] = subject.strip()
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body.strip())
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    started = perf_counter()
    service = _gmail_service(user_id, connection_id)
    try:
        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 403:
            raise GmailSendPermissionError(
                "Approve Gmail's send permission before sending messages."
            ) from exc
        if isinstance(status, int) and 400 <= status < 500:
            raise
        raise GmailDeliveryUnknownError(
            "Gmail may have accepted the email, but did not confirm the result."
        ) from exc
    if not isinstance(result, dict) or not result.get("id"):
        raise GmailDeliveryUnknownError(
            "Gmail may have accepted the email, but did not confirm the result."
        )
    log_timing("gmail.send_message", started)
    return str(result["id"])


def find_sent_gmail_message(
    user_id: str, connection_id: str, message_id: str
) -> str | None:
    """Find a previously sent message using its stable RFC Message-ID header."""
    query_id = message_id.strip().removeprefix("<").removesuffix(">")
    if not query_id:
        return None
    response = _gmail_service(user_id, connection_id).users().messages().list(
        userId="me",
        q=f"in:sent rfc822msgid:{query_id}",
        maxResults=1,
    ).execute()
    messages = response.get("messages", []) if isinstance(response, dict) else []
    if not messages or not isinstance(messages[0], dict):
        return None
    found_id = messages[0].get("id")
    return str(found_id) if found_id else None


def prepare_initial_mailbox_sync(user_id: str, days: int, connection_id: str):
    """Build one Gmail client and read the history ID before listing mailbox messages."""
    credentials = _credentials(user_id, connection_id)
    service_started = perf_counter()
    service = _gmail_service(user_id, connection_id, credentials=credentials)
    log_timing("gmail.client_ready", service_started)
    profile = service.users().getProfile(userId="me").execute()
    history_id = profile.get("historyId")
    if not history_id:
        raise RuntimeError("Gmail did not return a history ID for the initial sync.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return service, credentials, str(history_id), int(cutoff.timestamp() * 1000)


def fetch_initial_mailbox_messages(service, cutoff_ms: int) -> list[dict]:
    """Fetch all non-spam, non-trash messages newer than the cutoff, including bodies."""
    label_response = service.users().labels().list(userId="me").execute()
    label_names_by_id = {
        label["id"]: label["name"]
        for label in label_response.get("labels", [])
        if label.get("id") and label.get("name")
    }

    message_ids = []
    page_token = None
    list_started = perf_counter()
    while True:
        arguments = {
            "userId": "me",
            "q": f"after:{cutoff_ms // 1000}",
            "maxResults": 500,
        }
        if page_token:
            arguments["pageToken"] = page_token
        response = service.users().messages().list(**arguments).execute()
        message_ids.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    log_timing("gmail.initial_message_list", list_started, candidate_count=len(message_ids))

    results = []
    content_started = perf_counter()
    for offset in range(0, len(message_ids), GMAIL_RAW_BATCH_SIZE):
        batch_items = message_ids[offset : offset + GMAIL_RAW_BATCH_SIZE]
        messages_by_id = {}
        batch_errors = []
        request_ids = {str(index): item["id"] for index, item in enumerate(batch_items)}

        def on_response(request_id, response, exception):
            message_id = request_ids[request_id]
            if exception is not None:
                batch_errors.append(exception)
            else:
                messages_by_id[message_id] = response

        batch = service.new_batch_http_request()
        for request_id, item in zip(request_ids, batch_items):
            request = service.users().messages().get(
                userId="me", id=item["id"], format="raw"
            )
            batch.add(request, request_id=request_id, callback=on_response)
        batch_started = perf_counter()
        batch.execute()
        log_timing(
            "gmail.initial_raw_batch",
            batch_started,
            request_count=len(batch_items),
        )
        if batch_errors:
            raise batch_errors[0]

        for item in batch_items:
            message_data = messages_by_id[item["id"]]
            internal_ms = message_data.get("internalDate")
            if not internal_ms or int(internal_ms) < cutoff_ms:
                continue
            raw = message_data.get("raw")
            if not raw:
                continue
            padding = "=" * (-len(raw) % 4)
            message = message_from_bytes(base64.urlsafe_b64decode(raw + padding))
            internal_date = int(internal_ms)
            received = datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc)
            label_ids = message_data.get("labelIds", [])
            results.append({
                "id": item["id"],
                "thread_id": message_data.get("threadId", ""),
                "from": _decode(message.get("From")),
                "subject": _decode(message.get("Subject")) or "(No subject)",
                "received": received.isoformat(timespec="seconds"),
                "internal_date": internal_date,
                "preview": message_data.get("snippet", ""),
                "gmail_labels": [
                    label_names_by_id[label_id]
                    for label_id in label_ids
                    if label_id in label_names_by_id
                ],
                "body": _body(message),
            })

    log_timing(
        "gmail.initial_message_content",
        content_started,
        requested_count=len(message_ids),
        returned_count=len(results),
    )
    return results


def prepare_incremental_mailbox_sync(user_id: str, connection_id: str):
    """Return a Gmail client and the current cursor used for history fallback."""
    credentials = _credentials(user_id, connection_id)
    started = perf_counter()
    service = _gmail_service(user_id, connection_id, credentials=credentials)
    log_timing("gmail.client_ready", started)
    profile = service.users().getProfile(userId="me").execute()
    history_id = profile.get("historyId")
    if not history_id:
        raise RuntimeError("Gmail did not return a history ID.")
    return service, credentials, str(history_id)


def fetch_gmail_history_changes(service, start_history_id: str) -> dict:
    """Read every Gmail history page and fetch the current full data for changed mail."""
    changed_ids: set[str] = set()
    deleted_ids: set[str] = set()
    page_token = None
    latest_history_id = start_history_id
    started = perf_counter()
    while True:
        arguments = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "maxResults": 500,
        }
        if page_token:
            arguments["pageToken"] = page_token
        response = service.users().history().list(**arguments).execute()
        latest_history_id = str(response.get("historyId") or latest_history_id)
        for item in response.get("history", []):
            for deleted in item.get("messagesDeleted", []):
                message_id = (deleted.get("message") or {}).get("id")
                if message_id:
                    deleted_ids.add(message_id)
                    changed_ids.discard(message_id)
            for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                for changed in item.get(key, []):
                    message_id = (changed.get("message") or {}).get("id")
                    if message_id and message_id not in deleted_ids:
                        changed_ids.add(message_id)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    log_timing(
        "gmail.history_list",
        started,
        changed_count=len(changed_ids),
        deleted_count=len(deleted_ids),
    )

    label_started = perf_counter()
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_names = {
        label["id"]: label["name"]
        for label in labels
        if label.get("id") and label.get("name")
    }
    log_timing("gmail.labels_list", label_started, label_count=len(label_names))

    emails = []
    not_found = set()
    content_started = perf_counter()
    for gmail_id in sorted(changed_ids):
        try:
            message_data = service.users().messages().get(
                userId="me", id=gmail_id, format="raw"
            ).execute()
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                not_found.add(gmail_id)
                continue
            raise
        raw = message_data.get("raw")
        internal_ms = message_data.get("internalDate")
        if not raw or not internal_ms:
            continue
        raw_padding = "=" * (-len(raw) % 4)
        parsed = message_from_bytes(base64.urlsafe_b64decode(raw + raw_padding))
        internal_date = int(internal_ms)
        label_ids = message_data.get("labelIds", [])
        gmail_labels = [label_names[label_id] for label_id in label_ids if label_id in label_names]
        emails.append({
            "id": gmail_id,
            "thread_id": message_data.get("threadId", ""),
            "from": _decode(parsed.get("From")),
            "subject": _decode(parsed.get("Subject")) or "(No subject)",
            "received": datetime.fromtimestamp(internal_date / 1000, tz=timezone.utc).isoformat(timespec="seconds"),
            "internal_date": internal_date,
            "preview": message_data.get("snippet", ""),
            "gmail_labels": gmail_labels,
            "body": _body(parsed),
        })
        if "TRASH" in label_ids:
            deleted_ids.add(gmail_id)
    deleted_ids.update(not_found)
    log_timing(
        "gmail.history_message_details",
        content_started,
        requested_count=len(changed_ids),
        returned_count=len(emails),
        not_found_count=len(not_found),
    )
    return {
        "history_id": latest_history_id,
        "emails": emails,
        "deleted_ids": sorted(deleted_ids),
    }


def ensure_user_labels(
    label_names: list[str], user_id: str | None = None, connection_id: str | None = None
) -> list[dict[str, str]]:
    """Return existing labels and create any requested custom labels that are missing."""
    started = perf_counter()
    service = _gmail_service(user_id, connection_id)
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


def apply_user_label(
    message_id: str,
    label_id: str,
    label_name: str,
    user_id: str | None = None,
    connection_id: str | None = None,
) -> str:
    """Apply a previously verified existing user label to one Gmail message."""
    started = perf_counter()
    try:
        _gmail_service(user_id, connection_id).users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()
    except Exception:
        log_timing("gmail.apply_label", started, status="error", category=label_name)
        raise
    log_timing("gmail.apply_label", started, category=label_name)
    return f"Applied the '{label_name}' label."
