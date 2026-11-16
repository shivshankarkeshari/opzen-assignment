from contextlib import nullcontext

import httpx
import pytest
from pydantic import BaseModel, ConfigDict


@pytest.mark.parametrize("model,effort", [("lfm3-omni-2b", ""), ("qwen/qwen3.5-9b", "none")])
def test_local_provider_sends_actual_model_schema_and_no_groq_parameters(
    monkeypatch, model, effort
):
    from backend import llm
    from backend.config import Settings

    config = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_model=model,
        llm_reasoning_effort=effort,
        llm_timeout_seconds=123,
        openai_base_url="http://test.invalid:1234/v1",
        openai_api_key="lm-studio",
    )
    monkeypatch.setattr(llm, "settings", lambda: config)
    monkeypatch.setattr(llm, "observe", lambda *a, **kw: nullcontext())
    requests = []

    def handler(request):
        import json

        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "http://test.invalid:1234/v1/chat/completions"
        assert body["model"] == model
        assert body["response_format"]["json_schema"]["strict"] is True
        assert not {"include_reasoning", "max_completion_tokens"} & set(body)
        if effort:
            assert body["reasoning_effort"] == effort
        else:
            assert "reasoning_effort" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"status":"OK"}', "reasoning_content": "private"},
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )

    real_client = httpx.Client

    def local_client(**kwargs):
        assert kwargs["timeout"] == 123
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm.httpx, "Client", local_client)

    class Check(BaseModel):
        model_config = ConfigDict(extra="forbid")
        status: str

    result = llm.structured("Return OK", {"question": "synthetic"}, Check, "test-run")
    assert result.status == "OK" and len(requests) == 1


def test_provider_model_override_and_legacy_default():
    from backend.config import Settings
    from backend.llm import model_name

    assert model_name(Settings(_env_file=None)) == "openai/gpt-oss-120b"
    assert model_name(Settings(_env_file=None, llm_provider="openai_compatible")) == "lfm3-omni-2b"
    assert model_name(Settings(_env_file=None, llm_model="custom-model")) == "custom-model"
