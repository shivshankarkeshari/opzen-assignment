from types import SimpleNamespace

import httpx
import pytest


def test_trace_content_is_opt_in_and_redacts_credentials(monkeypatch):
    from backend import telemetry

    config = SimpleNamespace(langfuse_capture_content=False)
    monkeypatch.setattr(telemetry, "settings", lambda: config)
    value = {"question": "private document content", "api_key": "test-secret"}
    summary = telemetry.payload(value)
    assert summary["content_capture"] is False
    assert "private document content" not in str(summary)
    config.langfuse_capture_content = True
    captured = telemetry.payload(value)
    assert captured["question"] == "private document content"
    assert captured["api_key"] == "[redacted]"
    assert telemetry.payload({"reasoning": "private reasoning"})["reasoning"] == "[redacted]"


def test_observation_populates_input_and_default_output(monkeypatch):
    from backend import telemetry

    values = {}

    class Span:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def update(self, **kwargs):
            values.update(kwargs)

    class Client:
        def start_as_current_observation(self, **kwargs):
            values.update(kwargs)
            return Span()

    monkeypatch.setattr(
        telemetry,
        "settings",
        lambda: SimpleNamespace(langfuse_public_key="public", langfuse_secret_key="secret"),
    )
    monkeypatch.setattr(telemetry, "client", lambda: Client())
    with telemetry.observe("test.stage", "test-run"):
        pass
    assert values["input"] == {"stage": "test.stage", "run_id": "test-run"}
    assert values["output"] == {"status": "completed"}
    with telemetry.observe("test.stage", "test-run") as observation:
        observation.update(output={"returned": 4})
    assert values["output"] == {"returned": 4}


def test_http_failure_records_status_without_response_body_or_headers(monkeypatch):
    from backend import telemetry

    values = {}

    class Span:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def update(self, **kwargs):
            values.update(kwargs)

    class Client:
        def start_as_current_observation(self, **kwargs):
            return Span()

    monkeypatch.setattr(
        telemetry,
        "settings",
        lambda: SimpleNamespace(langfuse_public_key="public", langfuse_secret_key="secret"),
    )
    monkeypatch.setattr(telemetry, "client", lambda: Client())
    request = httpx.Request(
        "POST",
        "http://gateway/v1/chat/completions",
        headers={"Authorization": "Bearer private-key"},
    )
    response = httpx.Response(504, request=request, json={"detail": "private body"})
    with pytest.raises(httpx.HTTPStatusError), telemetry.observe("llm", "test"):
        response.raise_for_status()
    assert values["output"] == {
        "status": "failed",
        "error_type": "HTTPStatusError",
        "http_status": 504,
    }
    assert "private" not in str(values)
