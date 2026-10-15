"""Small request-correlated timing logs for backend operations."""

from contextvars import ContextVar
import json
import logging
from time import perf_counter


logger = logging.getLogger(__name__)
_request_id: ContextVar[str] = ContextVar("today_mail_request_id", default="-")


def set_request_id(request_id: str):
    """Set the ID for this request and return the token needed to reset it."""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()


def log_timing(step: str, started: float, *, status: str = "success", **details) -> None:
    """Write one safe, structured timing event; callers must pass metadata only."""
    event = {
        "request_id": current_request_id(),
        "step": step,
        "status": status,
        "duration_ms": round((perf_counter() - started) * 1000),
        **details,
    }
    logger.info("APP_TIMING %s", json.dumps(event, separators=(",", ":"), default=str))
