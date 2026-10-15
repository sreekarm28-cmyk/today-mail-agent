"""Small client for OpenRouter's OpenAI-compatible chat API."""

import json
import logging
import os
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .diagnostics import current_request_id, log_timing
from .observability import get_langfuse_client, observation, record_metadata


logger = logging.getLogger(__name__)


OPENROUTER_URL = os.getenv(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free"
)


def chat(
    messages: list[dict],
    tools: list[dict],
    *,
    tool_choice="auto",
    max_tokens=1024,
    include_diagnostics=False,
) -> dict | tuple[dict, dict]:
    """Send one non-streaming request and return the assistant message."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set OPENROUTER_API_KEY in this terminal before running the app."
        )

    client = get_langfuse_client()
    body_data = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        body_data["tools"] = tools
        body_data["tool_choice"] = tool_choice
        body_data["parallel_tool_calls"] = False

    request = Request(
        OPENROUTER_URL,
        data=json.dumps(body_data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Today's Mail Agent",
        },
    )

    started = perf_counter()
    with observation(
        client, as_type="generation", name="openrouter-chat", model=OPENROUTER_MODEL
    ) as span:
        try:
            try:
                with urlopen(request, timeout=120) as response:
                    result = json.loads(response.read())
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                logger.error("OpenRouter returned HTTP %s: %s", exc.code, detail)
                if exc.code == 401:
                    raise RuntimeError(
                        "OpenRouter rejected the API key. Check OPENROUTER_API_KEY."
                    ) from exc
                if exc.code == 402:
                    raise RuntimeError(
                        "OpenRouter has insufficient credits for this request."
                    ) from exc
                raise RuntimeError(
                    f"OpenRouter request failed (HTTP {exc.code}). Please try again."
                ) from exc
            except TimeoutError as exc:
                raise RuntimeError("OpenRouter did not respond within 2 minutes.") from exc
            except URLError as exc:
                raise RuntimeError(f"Could not reach OpenRouter: {exc.reason}") from exc

            choices = result.get("choices") or []
            if not choices or not isinstance(choices[0].get("message"), dict):
                raise RuntimeError("OpenRouter returned an unexpected response.")

            choice = choices[0]
            message = choice["message"]
            usage = result.get("usage") or {}
            content = message.get("content")
            diagnostics = {
                "finish_reason": choice.get("finish_reason") or "unknown",
                "output_tokens": usage.get("completion_tokens"),
                "input_tokens": usage.get("prompt_tokens"),
                "content_type": type(content).__name__ if content is not None else "null",
                "content_present": bool(content),
                "refusal_present": bool(message.get("refusal")),
            }
            metadata = {
                "status": "success",
                "duration_ms": round((perf_counter() - started) * 1000),
                "request_id": current_request_id(),
                "tool_count": len(tools),
                "tool_call_count": len(message.get("tool_calls") or []),
                "finish_reason": diagnostics["finish_reason"],
                "content_present": diagnostics["content_present"],
                "refusal_present": diagnostics["refusal_present"],
            }
            if usage.get("prompt_tokens") is not None:
                metadata["input_tokens"] = usage["prompt_tokens"]
            if usage.get("completion_tokens") is not None:
                metadata["output_tokens"] = usage["completion_tokens"]
            if usage.get("cost") is not None:
                metadata["cost_usd"] = usage["cost"]
            record_metadata(span, **metadata)
            log_timing(
                "openrouter.chat",
                started,
                model=OPENROUTER_MODEL,
                input_tokens=diagnostics["input_tokens"],
                output_tokens=diagnostics["output_tokens"],
                tool_call_count=metadata["tool_call_count"],
            )
            if include_diagnostics:
                return message, diagnostics
            return message
        except Exception as exc:
            log_timing(
                "openrouter.chat",
                started,
                status="error",
                model=OPENROUTER_MODEL,
                error_type=type(exc).__name__,
            )
            record_metadata(
                span,
                status="error",
                error_type=type(exc).__name__,
                duration_ms=round((perf_counter() - started) * 1000),
                request_id=current_request_id(),
            )
            raise
