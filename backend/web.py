"""Local web interface API for Today's Mail Agent."""

import base64
import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from typing import Literal

from .main import run_agent
from .email_tool import (
    SCOPES,
    connect_gmail_account,
    ensure_gmail_watch,
    fetch_email_by_id,
    fetch_recent_emails,
    gmail_account_email,
    list_user_labels,
)
from .runtime import (
    CLASSIFICATION_CATEGORIES,
    classify_recent_emails,
    process_gmail_notification,
    process_scheduled_emails,
    summarize_one_email,
)
from .user_tokens import get_gmail_connection, save_gmail_connection


APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"
logger = logging.getLogger(__name__)
app = FastAPI(title="Today Mail", docs_url=None, redoc_url=None)


@app.middleware("http")
async def redact_oauth_callback_query_from_access_logs(request: Request, call_next):
    """Keep one-time Google authorization codes out of Uvicorn access logs."""
    is_oauth_callback = request.url.path == "/api/connect-gmail/callback"
    try:
        return await call_next(request)
    finally:
        if is_oauth_callback:
            # FastAPI has already read the query parameters. Uvicorn logs the
            # request scope after this middleware returns, so clear the query.
            request.scope["query_string"] = b""


def _safe_oauth_error_details(exc: Exception) -> str:
    """Return only short, non-secret OAuth error fields suitable for logs."""
    details = []
    for field in ("error", "description"):
        value = getattr(exc, field, None)
        if not isinstance(value, str) or not value:
            continue
        safe_value = "".join(
            character for character in value
            if character.isalnum() or character in " _.-"
        )[:160]
        if safe_value:
            details.append(f"{field}={safe_value}")
    return " ".join(details) or "no safe provider details available"


def _supabase_settings() -> tuple[str, str]:
    return (
        os.getenv("SUPABASE_URL", "").rstrip("/"),
        os.getenv("SUPABASE_ANON_KEY", ""),
    )


def _app_auth_required() -> bool:
    return os.getenv("REQUIRE_APP_AUTH", "false").strip().lower() in {"1", "true", "yes"}


def _require_app_user(authorization: str | None = Header(default=None)) -> dict | None:
    """Verify a Supabase Auth session when cloud authentication is configured."""
    url, anon_key = _supabase_settings()
    if not url and not anon_key and not _app_auth_required():
        return None  # Local development remains usable without Supabase.
    if not url or not anon_key:
        raise HTTPException(status_code=503, detail="Supabase sign-in is not fully configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{url}/auth/v1/user",
        headers={"apikey": anon_key, "Authorization": authorization},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            user = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Please sign in again.") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=401, detail="Your sign-in is not valid.")
    return user


def _verify_scheduled_job(authorization: str | None) -> None:
    expected_token = os.getenv("SCHEDULED_JOB_TOKEN", "")
    if not expected_token:
        raise HTTPException(status_code=503, detail="Scheduled email processing is not configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing scheduled-job authorization.")
    supplied_token = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid scheduled-job authorization.")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class UserRequest(BaseModel):
    text: str
    history: list[ChatMessage] = Field(default_factory=list)


CATEGORIES = CLASSIFICATION_CATEGORIES
OAUTH_STATE_COOKIE = "gmail_oauth_state"


def _web_oauth_flow(
    state: str | None = None,
    code_verifier: str | None = None,
    autogenerate_code_verifier: bool = False,
):
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Google web OAuth is not configured on the server.")
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:
        raise RuntimeError("Install the project requirements before connecting Gmail.") from exc

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=autogenerate_code_verifier,
    )
    flow.redirect_uri = redirect_uri
    return flow


def _oauth_state_cipher():
    from cryptography.fernet import Fernet

    key = os.getenv("GMAIL_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("Gmail token encryption is not configured on the server.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("GMAIL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from exc


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/config")
def public_app_config() -> dict[str, str]:
    """Return only the Supabase URL and publishable key needed by the browser."""
    url, anon_key = _supabase_settings()
    if bool(url) != bool(anon_key) or (_app_auth_required() and not url):
        raise HTTPException(status_code=503, detail="Supabase sign-in is not fully configured.")
    return {"supabase_url": url, "supabase_anon_key": anon_key}


@app.get("/styles.css", include_in_schema=False)
def styles() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css")


@app.get("/script.js", include_in_schema=False)
def script() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "script.js", media_type="text/javascript")


@app.post("/api/summarize")
def summarize(request: UserRequest, user: dict | None = Depends(_require_app_user)) -> dict[str, str]:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type a question about your emails first.")
    try:
        history = [
            {"role": message.role, "content": message.content}
            for message in request.history[-12:]
        ]
        return {"answer": run_agent(text, history, user["id"] if user else None)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/connect-gmail")
def connect_gmail(
    response: Response,
    user: dict | None = Depends(_require_app_user),
) -> dict[str, str]:
    if user:
        try:
            flow = _web_oauth_flow(autogenerate_code_verifier=True)
            authorization_url, state = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
            if not flow.code_verifier:
                raise RuntimeError("Could not create the Google sign-in verifier.")
            state_data = json.dumps(
                {
                    "user_id": user["id"],
                    "state": state,
                    "code_verifier": flow.code_verifier,
                }
            ).encode("utf-8")
            sealed_state = _oauth_state_cipher().encrypt(state_data).decode("ascii")
            response.set_cookie(
                OAUTH_STATE_COOKIE,
                sealed_state,
                max_age=600,
                httponly=True,
                secure=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").startswith("https://"),
                samesite="lax",
                path="/",
            )
            return {"authorization_url": authorization_url}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        return {"email": connect_gmail_account()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/connect-gmail/callback", include_in_schema=False)
def connect_gmail_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    redirect = RedirectResponse("/?gmail=connect_failed", status_code=303)
    redirect.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    if error:
        redirect = RedirectResponse("/?gmail=cancelled", status_code=303)
        redirect.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        return redirect
    if not cookie_state or not code or not state:
        raise HTTPException(status_code=400, detail="Google sign-in did not return a valid response. Start again from the app.")
    try:
        state_data = json.loads(_oauth_state_cipher().decrypt(cookie_state.encode("ascii"), ttl=600).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Google sign-in expired. Start connecting Gmail again.") from exc
    if not hmac.compare_digest(str(state_data.get("state", "")), state):
        raise HTTPException(status_code=400, detail="Google sign-in could not be verified. Start connecting Gmail again.")
    code_verifier = state_data.get("code_verifier")
    if not isinstance(code_verifier, str) or not code_verifier:
        raise HTTPException(status_code=400, detail="This Google sign-in attempt is outdated. Start connecting Gmail again.")
    stage = "Google token exchange"
    try:
        flow = _web_oauth_flow(state=state, code_verifier=code_verifier)
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            stage = "checking for an existing Gmail connection"
            existing = get_gmail_connection(state_data["user_id"])
            if not existing:
                raise RuntimeError("Google did not return a reusable permission. Try connecting again and approve access.")
            refresh_token = existing["refresh_token"]
        else:
            refresh_token = credentials.refresh_token
        stage = "checking the connected Gmail account"
        gmail_email = gmail_account_email(credentials)
        stage = "saving the encrypted Gmail token to Supabase"
        save_gmail_connection(state_data["user_id"], gmail_email, refresh_token)
        redirect = RedirectResponse("/?gmail=connected", status_code=303)
        redirect.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        return redirect
    except Exception as exc:
        logger.error(
            "Gmail OAuth callback failed during %s (%s): %s",
            stage,
            type(exc).__name__,
            _safe_oauth_error_details(exc),
        )
        raise HTTPException(status_code=400, detail="Could not finish connecting Gmail. Check the Google OAuth settings and try again.") from exc


@app.get("/api/gmail/status")
def gmail_status(user: dict | None = Depends(_require_app_user)) -> dict[str, str | bool]:
    if not user:
        return {"connected": False, "email": ""}
    connection = get_gmail_connection(user["id"])
    return {
        "connected": bool(connection),
        "email": connection["email"] if connection else "",
    }


@app.get("/api/dashboard/emails")
def dashboard_emails(user: dict | None = Depends(_require_app_user)) -> dict:
    """Fetch recent mail, classify uncategorized messages, and return updated labels."""
    try:
        user_id = user["id"] if user else None
        if not user_id:
            ensure_gmail_watch()
        emails = fetch_recent_emails(user_id)
        classification = classify_recent_emails(emails, user_id)
        labels = list_user_labels(user_id)
        label_names = {label["id"]: label["name"] for label in labels}
        dashboard_items = []
        for email in emails:
            dashboard_items.append({
                "id": email["id"],
                "from": email["from"],
                "subject": email["subject"],
                "received": email["received"],
                "labels": [label_names[label_id] for label_id in email.get("label_ids", []) if label_id in label_names],
                "preview": " ".join(email.get("body", "").split())[:240],
            })
        return {
            "emails": dashboard_items,
            "categories": CATEGORIES,
            "available_labels": list(label_names.values()),
            "classification": classification,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _verify_pubsub_request(authorization: str | None) -> None:
    """Verify the OIDC token configured on the Pub/Sub push subscription."""
    audience = os.getenv("PUBSUB_PUSH_AUDIENCE", "").strip()
    expected_email = os.getenv("PUBSUB_PUSH_SERVICE_ACCOUNT", "").strip()
    if not audience or not expected_email:
        raise HTTPException(status_code=503, detail="Pub/Sub push authentication is not configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Pub/Sub authorization token.")

    token = authorization.split(" ", 1)[1].strip()
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(token, GoogleRequest(), audience=audience)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub authorization token.") from exc
    if claims.get("email") != expected_email or claims.get("email_verified") is not True:
        raise HTTPException(status_code=401, detail="Pub/Sub service account did not match the configured account.")


@app.post("/api/gmail/notifications", status_code=204)
async def gmail_notifications(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    """Receive authenticated Gmail change notifications delivered through Pub/Sub."""
    if _supabase_settings()[0]:
        raise HTTPException(status_code=503, detail="Multi-user Gmail processing is not configured yet.")
    await run_in_threadpool(_verify_pubsub_request, authorization)
    try:
        push_body = await request.json()
        encoded_data = push_body["message"]["data"]
        notification = json.loads(base64.b64decode(encoded_data, validate=True).decode("utf-8"))
        notification_email = notification["emailAddress"]
        notification_history_id = str(notification["historyId"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Gmail notification payload.") from exc

    try:
        connected_email = await run_in_threadpool(connect_gmail_account)
        if notification_email.lower() != connected_email.lower():
            raise HTTPException(status_code=403, detail="Notification is for a different Gmail account.")
        await run_in_threadpool(process_gmail_notification, notification_history_id)
    except HTTPException:
        raise
    except Exception as exc:
        # A non-success response tells Pub/Sub that this notification was not handled.
        raise HTTPException(status_code=503, detail="Could not process Gmail notification.") from exc
    return Response(status_code=204)


@app.post("/api/internal/process-recent-emails")
def process_recent_emails_job(
    authorization: str | None = Header(default=None),
) -> dict[str, int | str]:
    """Run the scheduled recent-email classification job for connected users."""
    _verify_scheduled_job(authorization)
    try:
        result = process_scheduled_emails()
    except Exception as exc:
        logger.error("Scheduled Gmail processing could not start (%s).", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not start scheduled email processing.") from exc
    # A run with some unclassified emails is still a completed run. Return its
    # counts so GitHub Actions records success while the unsorted items remain
    # visible for later review or retry.
    return result


@app.get("/api/dashboard/emails/{message_id}")
def dashboard_email_details(message_id: str, user: dict | None = Depends(_require_app_user)) -> dict[str, str]:
    """Fetch one full readable email body for display, without calling the model."""
    try:
        email = fetch_email_by_id(message_id, max_body_chars=None, user_id=user["id"] if user else None)
        email.pop("label_ids", None)
        return email
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dashboard/emails/{message_id}/summary")
def summarize_dashboard_email(message_id: str, user: dict | None = Depends(_require_app_user)) -> dict[str, str]:
    """Fetch and summarize only the email selected in the dashboard."""
    try:
        email = fetch_email_by_id(message_id, user_id=user["id"] if user else None)
        return {"summary": summarize_one_email(email)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


