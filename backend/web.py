"""Local web interface API for Today's Mail Agent."""

import base64
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from typing import Literal

from .main import run_agent
from .email_tool import (
    connect_gmail_account,
    ensure_gmail_watch,
    fetch_email_by_id,
    fetch_recent_emails,
    list_user_labels,
)
from .runtime import (
    CLASSIFICATION_CATEGORIES,
    classify_recent_emails,
    process_gmail_notification,
    summarize_one_email,
)


APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"
app = FastAPI(title="Today Mail", docs_url=None, redoc_url=None)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class UserRequest(BaseModel):
    text: str
    history: list[ChatMessage] = Field(default_factory=list)


CATEGORIES = CLASSIFICATION_CATEGORIES


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/styles.css", include_in_schema=False)
def styles() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "styles.css", media_type="text/css")


@app.get("/script.js", include_in_schema=False)
def script() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "script.js", media_type="text/javascript")


@app.post("/api/summarize")
def summarize(request: UserRequest) -> dict[str, str]:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type a question about your emails first.")
    try:
        history = [
            {"role": message.role, "content": message.content}
            for message in request.history[-12:]
        ]
        return {"answer": run_agent(text, history)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/connect-gmail")
def connect_gmail() -> dict[str, str]:
    try:
        return {"email": connect_gmail_account()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/dashboard/emails")
def dashboard_emails() -> dict:
    """Fetch recent mail, classify uncategorized messages, and return updated labels."""
    try:
        ensure_gmail_watch()
        emails = fetch_recent_emails()
        classification = classify_recent_emails(emails)
        labels = list_user_labels()
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


@app.get("/api/dashboard/emails/{message_id}")
def dashboard_email_details(message_id: str) -> dict[str, str]:
    """Fetch one full readable email body for display, without calling the model."""
    try:
        email = fetch_email_by_id(message_id, max_body_chars=None)
        email.pop("label_ids", None)
        return email
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/dashboard/emails/{message_id}/summary")
def summarize_dashboard_email(message_id: str) -> dict[str, str]:
    """Fetch and summarize only the email selected in the dashboard."""
    try:
        email = fetch_email_by_id(message_id)
        return {"summary": summarize_one_email(email)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


