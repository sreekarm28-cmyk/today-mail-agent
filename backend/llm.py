"""Small client for OpenRouter's OpenAI-compatible chat API."""

import json
import logging
import os
import re
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
OPENROUTER_EMBEDDING_URL = os.getenv(
    "OPENROUTER_EMBEDDING_URL", "https://openrouter.ai/api/v1/embeddings"
)
OPENROUTER_EMBEDDING_MODEL = os.getenv(
    "OPENROUTER_EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b:free"
)
EMBEDDING_DIMENSIONS = 2048


def embed_texts(texts: list[str], *, input_type: str) -> list[list[float]]:
    """Create query or passage embeddings through OpenRouter."""
    if input_type not in {"query", "passage"}:
        raise ValueError("Embedding input_type must be 'query' or 'passage'.")
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("Embedding input must contain non-empty text.")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENROUTER_API_KEY on the server first.")

    payload = {
        "model": OPENROUTER_EMBEDDING_MODEL,
        "input": texts,
        "input_type": input_type,
        "encoding_format": "float",
    }
    request = Request(
        OPENROUTER_EMBEDDING_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Today's Mail Agent",
        },
    )
    started = perf_counter()
    try:
        try:
            with urlopen(request, timeout=90) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            # Avoid logging the provider response body because it may echo input.
            logger.error("OpenRouter embeddings returned HTTP %s.", exc.code)
            if exc.code == 401:
                raise RuntimeError("OpenRouter rejected the API key.") from exc
            raise RuntimeError(f"OpenRouter embeddings request failed (HTTP {exc.code}).") from exc
        except TimeoutError as exc:
            raise RuntimeError("OpenRouter embeddings did not respond within 90 seconds.") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach OpenRouter embeddings: {exc.reason}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("OpenRouter returned an invalid embeddings response.") from exc

        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise RuntimeError("OpenRouter returned the wrong number of embeddings.")
        ordered = sorted(data, key=lambda item: item.get("index", -1) if isinstance(item, dict) else -1)
        vectors = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if (
                not isinstance(vector, list)
                or len(vector) != EMBEDDING_DIMENSIONS
                or any(not isinstance(value, (int, float)) for value in vector)
            ):
                raise RuntimeError(
                    f"OpenRouter returned an invalid embedding; expected {EMBEDDING_DIMENSIONS} numbers."
                )
            vectors.append([float(value) for value in vector])
        usage = result.get("usage") or {}
        log_timing(
            "openrouter.embeddings",
            started,
            model=OPENROUTER_EMBEDDING_MODEL,
            input_type=input_type,
            input_count=len(texts),
            input_tokens=usage.get("total_tokens"),
            dimensions=EMBEDDING_DIMENSIONS,
        )
        return vectors
    except Exception as exc:
        log_timing(
            "openrouter.embeddings",
            started,
            status="error",
            model=OPENROUTER_EMBEDDING_MODEL,
            input_type=input_type,
            input_count=len(texts),
            error_type=type(exc).__name__,
        )
        raise


def _safe_response_preview(content: object, *, limit: int = 240) -> str:
    """Return a short, redacted diagnostic excerpt without reasoning blocks."""
    if not isinstance(content, str):
        return "<non-text response>"

    preview = re.sub(r"<think\b[^>]*>.*?(?:</think\s*>|$)", "[reasoning omitted]", content, flags=re.IGNORECASE | re.DOTALL)
    preview = re.sub(r"https?://\S+|www\.\S+", "[URL]", preview, flags=re.IGNORECASE)
    preview = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", preview)
    preview = re.sub(r"(?<!\w)\+?\d[\d() .-]{7,}\d(?!\w)", "[number]", preview)
    preview = re.sub(r"\b\d{6,}\b", "[number]", preview)
    preview = " ".join(preview.split())
    if len(preview) > limit:
        preview = preview[:limit] + "…"
    return preview


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
        # The free Nemotron endpoint supports the OpenRouter reasoning control.
        # Disable extra thinking tokens so the model has room to return its tool call.
        "reasoning": {"enabled": False},
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
            completion_details = usage.get("completion_tokens_details") or {}
            content = message.get("content")
            diagnostics = {
                "finish_reason": choice.get("finish_reason") or "unknown",
                "output_tokens": usage.get("completion_tokens"),
                "input_tokens": usage.get("prompt_tokens"),
                "reasoning_tokens": completion_details.get("reasoning_tokens"),
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
            tool_calls = message.get("tool_calls") or []
            tool_names = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                name = function.get("name") if isinstance(function, dict) else None
                tool_names.append(str(name or "unknown")[:80])
            expected_tool_name = None
            if isinstance(tool_choice, dict):
                expected_function = tool_choice.get("function")
                if isinstance(expected_function, dict):
                    expected_tool_name = expected_function.get("name")
            content_text = content if isinstance(content, str) else ""
            needs_response_diagnostic = (
                len(tool_calls) != 1
                or (expected_tool_name is not None and tool_names != [expected_tool_name])
                or diagnostics["finish_reason"] == "length"
            )
            if usage.get("prompt_tokens") is not None:
                metadata["input_tokens"] = usage["prompt_tokens"]
            if usage.get("completion_tokens") is not None:
                metadata["output_tokens"] = usage["completion_tokens"]
            if diagnostics["reasoning_tokens"] is not None:
                metadata["reasoning_tokens"] = diagnostics["reasoning_tokens"]
            if usage.get("cost") is not None:
                metadata["cost_usd"] = usage["cost"]
            record_metadata(span, **metadata)
            log_timing(
                "openrouter.chat",
                started,
                model=OPENROUTER_MODEL,
                input_tokens=diagnostics["input_tokens"],
                output_tokens=diagnostics["output_tokens"],
                reasoning_tokens=diagnostics["reasoning_tokens"],
                tool_count=len(tools),
                tool_call_count=len(tool_calls),
                finish_reason=diagnostics["finish_reason"],
                content_chars=len(content_text),
            )
            if needs_response_diagnostic:
                logger.warning(
                    "MODEL_RESPONSE_DIAGNOSTIC %s",
                    json.dumps(
                        {
                            "request_id": current_request_id(),
                            "model": OPENROUTER_MODEL,
                            "finish_reason": diagnostics["finish_reason"],
                            "tool_count": len(tools),
                            "tool_call_count": len(tool_calls),
                            "tool_names": tool_names,
                            "expected_tool_name": expected_tool_name,
                            "content_chars": len(content_text),
                            "reasoning_marker_present": bool(
                                re.search(r"<think\b", content_text, flags=re.IGNORECASE)
                            ),
                            "preview": _safe_response_preview(content),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
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
