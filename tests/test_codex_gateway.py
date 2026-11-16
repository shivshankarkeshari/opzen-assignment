from fastapi.testclient import TestClient

from scripts import codex_gateway as gateway


def client(monkeypatch):
    monkeypatch.setattr(
        gateway,
        "config",
        lambda: gateway.GatewayConfig(
            _env_file=None, model="gpt-6.1-sol", gateway_api_key="test-key"
        ),
    )
    return TestClient(gateway.app)


def request_body():
    return {
        "model": "gpt-6.1-sol",
        "messages": [{"role": "user", "content": "Return OK"}],
        "max_tokens": 500,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                    "additionalProperties": False,
                }
            },
        },
    }


def test_gateway_requires_credential_before_starting_cli(monkeypatch):
    api = client(monkeypatch)
    monkeypatch.setattr(
        gateway,
        "structured_call",
        lambda *args: (_ for _ in ()).throw(AssertionError("CLI must not run")),
    )
    assert api.post("/v1/chat/completions", json=request_body()).status_code == 401


def test_gateway_rejects_unselected_model_and_streaming(monkeypatch):
    api = client(monkeypatch)
    body = request_body()
    body["model"] = "other-model"
    assert (
        api.post(
            "/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json=body
        ).status_code
        == 400
    )
    body.update(model="gpt-6.1-sol", stream=True)
    assert (
        api.post(
            "/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json=body
        ).status_code
        == 400
    )


def test_gateway_translates_structured_result_and_usage(monkeypatch):
    api = client(monkeypatch)
    monkeypatch.setattr(
        gateway,
        "structured_call",
        lambda *args: ({"status": "OK"}, {"input_tokens": 20, "output_tokens": 8}, 1.2),
    )
    response = api.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json=request_body()
    )
    assert response.status_code == 200
    result = response.json()
    assert result["choices"][0]["message"]["content"] == '{"status": "OK"}'
    assert result["usage"]["completion_tokens"] == 8
    assert result["gateway"]["upstream_token_cap"] is False


def test_strict_schema_preserves_property_names_and_nullability():
    schema = {
        "type": "object",
        "properties": {
            "default": {"type": "string"},
            "reason": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
    }
    result = gateway.strict_schema(schema)
    assert result["required"] == ["default", "reason"]
    assert result["additionalProperties"] is False
    assert "default" in result["properties"]
    assert "default" not in result["properties"]["reason"]
    assert result["properties"]["reason"]["anyOf"][1]["type"] == "null"
    assert "required" not in schema
