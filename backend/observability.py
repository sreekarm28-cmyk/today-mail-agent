"""Optional, privacy-conscious Langfuse tracing helpers."""

from contextlib import nullcontext
import logging
import os

logger = logging.getLogger(__name__)
_missing_keys_notice_logged = False


def get_langfuse_client():
    """Return a Langfuse client only when credentials are configured."""
    global _missing_keys_notice_logged
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not public_key and not secret_key:
        if not _missing_keys_notice_logged:
            logger.warning(
                "Langfuse tracing is disabled: LANGFUSE_PUBLIC_KEY and "
                "LANGFUSE_SECRET_KEY are not configured."
            )
            _missing_keys_notice_logged = True
        return None
    if not public_key or not secret_key:
        raise RuntimeError("Set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY, or leave both unset.")

    try:
        from langfuse import get_client
    except ImportError as exc:
        raise RuntimeError("Install the project requirements to use Langfuse: py -m pip install -r requirements.txt") from exc
    return get_client()


def flush_client(client) -> None:
    """Flush queued traces, logging failures without exposing private data."""
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:
        logger.warning("Langfuse trace flush failed (%s).", type(exc).__name__)


def observation(client, *, as_type: str, name: str, **attributes):
    """Create an observation, or a no-op context if tracing is not configured."""
    if client is None:
        return nullcontext(None)
    return client.start_as_current_observation(as_type=as_type, name=name, **attributes)


def record_metadata(observation_handle, **metadata) -> None:
    """Attach only explicitly supplied, non-content metadata to an observation."""
    if observation_handle is not None:
        observation_handle.update(metadata=metadata)
