import json
import logging
import re
from contextlib import ExitStack, contextmanager
from functools import lru_cache

import httpx

from backend.config import settings


def payload(value):
    """Content is opt-in; summaries remain useful without protected text."""
    encoded = json.dumps(value, default=str)
    if not settings().langfuse_capture_content:
        return {
            "content_capture": False,
            "fields": list(value) if isinstance(value, dict) else [],
            "serialized_characters": len(encoded),
        }

    def redact(item):
        if isinstance(item, dict):
            return {
                k: "[redacted]"
                if any(
                    word in k.lower()
                    for word in ("api_key", "password", "secret", "authorization", "reasoning")
                )
                else redact(v)
                for k, v in item.items()
            }
        if isinstance(item, list):
            return [redact(v) for v in item]
        if isinstance(item, str):
            return re.sub(r"\b(?:gsk_|sk-|Bearer\s+)[A-Za-z0-9_-]{12,}", "[redacted]", item)
        return item

    cleaned = redact(value)
    encoded = json.dumps(cleaned, default=str)
    return cleaned if len(encoded) <= 24000 else {"preview": encoded[:24000], "truncated": True}


class Observation:
    def __init__(self, span):
        self.span, self.has_output = span, False

    def update(self, **values):
        self.has_output |= "output" in values
        try:
            self.span.update(**values)
        except Exception:
            logging.getLogger(__name__).warning("Telemetry update failed")


@lru_cache
def client():
    from langfuse import Langfuse

    s = settings()
    return Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key,
        base_url=s.langfuse_base_url,
    )


@contextmanager
def observe(name: str, run_id: str, model: str | None = None, input: dict | None = None):
    # Deliberately export only stage names and opaque correlation IDs, never document bodies.
    stack = ExitStack()
    observation = None
    if settings().langfuse_public_key and settings().langfuse_secret_key:
        try:
            span = stack.enter_context(
                client().start_as_current_observation(
                    name=name,
                    metadata={"run_id": run_id},
                    input=input if input is not None else {"stage": name, "run_id": run_id},
                    **({"as_type": "generation", "model": model} if model else {}),
                )
            )
            observation = Observation(span)
        except Exception:
            logging.getLogger(__name__).warning("Telemetry unavailable")
    try:
        yield observation
    except BaseException as exc:
        if observation:
            details = {
                "status": "interrupted" if type(exc).__name__ == "GraphInterrupt" else "failed",
                "error_type": type(exc).__name__,
            }
            if isinstance(exc, httpx.HTTPStatusError):
                details["http_status"] = exc.response.status_code
            observation.update(output=details)
        raise
    finally:
        if observation and not observation.has_output:
            observation.update(output={"status": "completed"})
        try:
            stack.close()
        except Exception:
            logging.getLogger(__name__).warning("Telemetry export failed")
