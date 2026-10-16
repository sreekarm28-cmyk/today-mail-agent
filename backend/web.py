"""Local web interface API for Today's Mail Agent."""

import hmac
import json
import logging
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .main import load_local_settings
from .diagnostics import log_timing, reset_request_id, set_request_id

load_local_settings()

from .email_tool import (
    SCOPES,
    connect_gmail_account,
    gmail_account_email,
)
from .runtime import (
    CLASSIFICATION_CATEGORIES,
    initial_sync_mailbox,
    process_scheduled_emails,
    summarize_one_email,
    sync_persistent_mailbox,
)
from .user_tokens import (
    get_gmail_connection,
    get_persistent_email_by_id,
    get_persistent_emails,
    save_gmail_connection,
)


APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
MODEL_REQUESTS_PER_MINUTE = 2
MODEL_REQUESTS_PER_DAY = 10
app = FastAPI(title="Today Mail", docs_url=None, redoc_url=None)


def _rate_limit_key(request: Request) -> str:
    """Use the verified app-user ID; use IP only for unauthenticated local mode."""
    user_id = getattr(request.state, "app_user_id", None)
    return f"user:{user_id}" if user_id else f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URI", "memory://"),
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def redact_oauth_callback_query_from_access_logs(request: Request, call_next):
    """Assign a request ID, log total time, and hide one-time OAuth codes."""
    is_oauth_callback = request.url.path == "/api/connect-gmail/callback"
    request_id = uuid4().hex[:12]
    request.state.request_id = request_id
    request_token = set_request_id(request_id)
    started = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        log_timing(
            "http.request",
            started,
            status="success" if status_code < 400 else "error",
            method=request.method,
            route=route_path,
            status_code=status_code,
        )
        if is_oauth_callback:
            # FastAPI has already read the query parameters. Uvicorn logs the
            # request scope after this middleware returns, so clear the query.
            request.scope["query_string"] = b""
        reset_request_id(request_token)


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


def _require_app_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict | None:
    """Verify a Supabase Auth session when cloud authentication is configured."""
    url, anon_key = _supabase_settings()
    if not url and not anon_key and not _app_auth_required():
        request.state.app_user_id = None
        return None  # Local development remains usable without Supabase.
    if not url or not anon_key:
        raise HTTPException(status_code=503, detail="Supabase sign-in is not fully configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    import urllib.error
    import urllib.request

    auth_request = urllib.request.Request(
        f"{url}/auth/v1/user",
        headers={"apikey": anon_key, "Authorization": authorization},
    )
    try:
        with urllib.request.urlopen(auth_request, timeout=10) as response:
            user = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Please sign in again.") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=401, detail="Your sign-in is not valid.")
    request.state.app_user_id = user["id"]
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
            logger.exception("Could not start Gmail connection")
            raise HTTPException(status_code=503, detail="Something went wrong. Please try again.") from exc
    try:
        return {"email": connect_gmail_account()}
    except Exception as exc:
        logger.exception("Could not connect Gmail locally")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.") from exc


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
    """Return the signed-in user's persistent email list from Supabase."""
    started = perf_counter()
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to view your saved inbox.")
    try:
        dashboard_items = get_persistent_emails(user["id"])
        log_timing("dashboard.database_read", started, email_count=len(dashboard_items))
        return {
            "emails": dashboard_items,
            "categories": CATEGORIES,
            "sync_status": "database",
        }
    except Exception as exc:
        log_timing("dashboard.database_read", started, status="error")
        logger.exception("Could not load persistent dashboard emails")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.") from exc


@app.post("/api/mailbox/initial-sync")
def initial_mailbox_sync(user: dict | None = Depends(_require_app_user)) -> dict:
    """Run the first persistent mailbox import for the signed-in app user."""
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to sync your mailbox.")
    try:
        return initial_sync_mailbox(user["id"], user.get("email", ""))
    except Exception as exc:
        logger.exception("Could not complete initial mailbox sync")
        raise HTTPException(status_code=400, detail="Could not sync your mailbox. Please try again.") from exc


@app.post("/api/dashboard/sync")
def sync_dashboard_emails(user: dict | None = Depends(_require_app_user)) -> dict:
    """Sync Gmail history into Supabase, then return saved emails from Supabase."""
    started = perf_counter()
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to sync your saved inbox.")
    try:
        sync_result = sync_persistent_mailbox(user["id"], user.get("email", ""))
        dashboard_items = get_persistent_emails(user["id"])
        log_timing("dashboard.database_sync", started, email_count=len(dashboard_items))
        return {
            "emails": dashboard_items,
            "categories": CATEGORIES,
            "sync_status": sync_result.get("status", "database"),
        }
    except Exception as exc:
        log_timing("dashboard.database_sync", started, status="error")
        logger.exception("Could not load or initialize persistent dashboard emails")
        raise HTTPException(status_code=400, detail="Could not sync your inbox. Please try again.") from exc


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


@app.get("/api/dashboard/emails/{email_id}")
def dashboard_email_details(email_id: str, user: dict | None = Depends(_require_app_user)) -> dict[str, str]:
    """Read one full email body from Supabase, without calling Gmail or the model."""
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to open this email.")
    try:
        email = get_persistent_email_by_id(user["id"], email_id)
        if not email:
            raise HTTPException(status_code=404, detail="Email not found.")
        return email
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        logger.exception("Could not load dashboard email details")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.") from exc


@app.post("/api/dashboard/emails/{email_id}/summary")
@limiter.shared_limit(f"{MODEL_REQUESTS_PER_MINUTE}/minute", scope="model-requests-per-minute")
@limiter.shared_limit(f"{MODEL_REQUESTS_PER_DAY}/day", scope="model-requests-per-day")
def summarize_dashboard_email(
    email_id: str,
    request: Request,
    user: dict | None = Depends(_require_app_user),
) -> dict[str, str]:
    """Summarize the selected email using its body already saved in Supabase."""
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to summarize this email.")
    started = perf_counter()
    try:
        fetch_started = perf_counter()
        email = get_persistent_email_by_id(user["id"], email_id)
        if not email:
            raise HTTPException(status_code=404, detail="Email not found.")
        log_timing("summary.database_read", fetch_started)
        model_started = perf_counter()
        summary = summarize_one_email(email)
        log_timing("summary.model", model_started)
        log_timing("summary.total", started)
        return {"summary": summary}
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        log_timing("summary.total", started, status="error")
        logger.exception("Could not summarize dashboard email")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.") from exc


