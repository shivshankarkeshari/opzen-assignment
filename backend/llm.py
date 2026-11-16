"""Configured Groq/LM Studio adapters; typed output and final content only."""

import httpx
from groq import Groq
from pydantic import BaseModel

from backend.config import settings
from backend.telemetry import observe
from backend.telemetry import payload as trace_payload


def model_name(config=None):
    s = config or settings()
    return getattr(s, "llm_model", "") or (
        s.groq_model if getattr(s, "llm_provider", "groq") == "groq" else "lfm3-omni-2b"
    )


def configured():
    s = settings()
    return (
        bool(s.groq_api_key.get_secret_value())
        if s.llm_provider == "groq"
        else bool(s.openai_base_url)
    )


def output_tokens(s):
    return getattr(s, "llm_max_output_tokens", None) or s.groq_max_completion_tokens


def compatible_request(messages, schema, timeout):
    """LM Studio/OpenAI-compatible JSON-schema endpoint; no Groq-only parameters."""
    s = settings()
    body = {
        "model": model_name(s),
        "messages": messages,
        "temperature": 0,
        "max_tokens": output_tokens(s),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        },
    }
    if s.llm_reasoning_effort:
        body["reasoning_effort"] = s.llm_reasoning_effort
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            s.openai_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + s.openai_api_key.get_secret_value()},
            json=body,
        )
        response.raise_for_status()
        return response.json()


def structured(system: str, payload: dict, schema: type[BaseModel], run_id: str, timeout=None):
    """Strict JSON planning/validation; no prompts or completions in telemetry."""
    import json

    s = settings()
    timeout = s.llm_timeout_seconds if timeout is None else timeout
    with observe(
        "llm.structured",
        run_id,
        model=model_name(s),
        input={
            "schema": schema.__name__,
            "model": model_name(s),
            "provider": s.llm_provider,
            "request": trace_payload({"system": system, "payload": payload}),
        },
    ) as observation:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]
        if s.llm_provider == "openai_compatible":
            data = compatible_request(messages, schema, timeout)
        else:
            with Groq(
                api_key=s.groq_api_key.get_secret_value(), timeout=timeout, max_retries=1
            ) as client:
                response = client.chat.completions.create(
                    model=model_name(s),
                    messages=messages,
                    temperature=0,
                    reasoning_effort=s.groq_reasoning_effort,
                    include_reasoning=False,
                    max_completion_tokens=output_tokens(s),
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "strict": True,
                            "schema": schema.model_json_schema(),
                        },
                    },
                )
                data = response.model_dump()
        if observation and data.get("usage"):
            observation.update(
                usage_details={
                    "input": data["usage"].get("prompt_tokens", 0),
                    "output": data["usage"].get("completion_tokens", 0),
                }
            )
        choice = data["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("Structured completion did not finish")
        parsed = schema.model_validate_json(choice["message"]["content"])
        if observation:
            observation.update(
                output={
                    "schema": schema.__name__,
                    "validated": True,
                    "response": trace_payload(parsed.model_dump()),
                }
            )
        return parsed


def stream_completion(messages: list[dict], run_id: str = "connection-check"):
    s = settings()
    if getattr(s, "llm_provider", "groq") == "openai_compatible":
        import json

        with observe(
            "llm.completion",
            run_id,
            model=model_name(s),
            input=trace_payload({"messages": messages}),
        ) as observation:
            content = ""
            with httpx.stream(
                "POST",
                s.openai_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + s.openai_api_key.get_secret_value()},
                json={
                    "model": model_name(s),
                    "messages": messages,
                    "max_tokens": output_tokens(s),
                    "stream": True,
                    **(
                        {"reasoning_effort": s.llm_reasoning_effort}
                        if s.llm_reasoning_effort
                        else {}
                    ),
                },
                timeout=s.llm_timeout_seconds,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    value = line[6:]
                    if value == "[DONE]":
                        break
                    row = json.loads(value)
                    for choice in row.get("choices", []):
                        if text := choice.get("delta", {}).get("content"):
                            content += text
                            yield text
            if observation:
                observation.update(output=trace_payload({"content": content}))
        return
    if not s.groq_api_key.get_secret_value():
        raise ValueError("GROQ_API_KEY is not configured")
    with (
        observe(
            "groq.completion",
            run_id,
            model=model_name(s),
            input={"model": model_name(s), "messages": trace_payload({"messages": messages})},
        ) as observation,
        Groq(
            api_key=s.groq_api_key.get_secret_value(),
            timeout=60,
            max_retries=1,
        ) as client,
    ):
        stream = client.chat.completions.create(
            model=model_name(s),
            messages=messages,
            temperature=1,
            max_completion_tokens=output_tokens(s),
            top_p=1,
            reasoning_effort=s.groq_reasoning_effort,
            include_reasoning=False,
            stream=True,
        )
        try:
            final_content = ""
            for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage and observation:
                    observation.update(
                        usage_details={
                            "input": usage.prompt_tokens,
                            "output": usage.completion_tokens,
                        }
                    )
                if chunk.choices:
                    content = chunk.choices[0].delta.content
                    if content:
                        final_content += content
                        yield content
            if observation:
                observation.update(
                    output={"completed": True, "content": trace_payload({"content": final_content})}
                )
        finally:
            stream.close()
