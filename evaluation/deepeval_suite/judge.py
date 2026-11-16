"""Schema-aware compatible-provider judge with a finite request/time budget."""

import asyncio
import os
import time

import httpx
from deepeval.models import DeepEvalBaseLLM


class LocalJudge(DeepEvalBaseLLM):
    def __init__(self):
        self.base_url = os.environ.get(
            "DEEPEVAL_JUDGE_BASE_URL", "http://127.0.0.1:1234/v1"
        ).rstrip("/")
        self.model_id = os.environ.get(
            "DEEPEVAL_JUDGE_MODEL", os.environ.get("LLM_MODEL", "qwen/qwen3.5-9b")
        )
        self.max_requests = min(
            256, max(1, int(os.environ.get("DEEPEVAL_JUDGE_MAX_REQUESTS", "128")))
        )
        self.timeout_seconds = min(
            1800, max(30, int(os.environ.get("DEEPEVAL_JUDGE_TIMEOUT_SECONDS", "600")))
        )
        self.request_timeout_seconds = min(
            300, max(1, int(os.environ.get("DEEPEVAL_JUDGE_REQUEST_TIMEOUT_SECONDS", "120")))
        )
        self.requests = 0
        self.deadline = time.monotonic() + self.timeout_seconds
        super().__init__(model=self.model_id)

    def load_model(self):
        return self.model_id

    def get_model_name(self):
        return os.environ.get("DEEPEVAL_JUDGE_PROVIDER_LABEL", "LM Studio") + " / " + self.model_id

    def generate(self, prompt, schema=None, **kwargs):
        remaining = self.deadline - time.monotonic()
        if self.requests >= self.max_requests or remaining < self.request_timeout_seconds:
            raise RuntimeError("Local judge exceeded its finite case request/time budget")
        self.requests += 1
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 4096,
        }
        effort = os.environ.get("DEEPEVAL_JUDGE_REASONING_EFFORT", "none")
        if effort:
            payload["reasoning_effort"] = effort
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            }
        response = httpx.post(
            self.base_url + "/chat/completions",
            headers={
                "Authorization": "Bearer " + os.environ.get("DEEPEVAL_JUDGE_API_KEY", "lm-studio")
            },
            json=payload,
            timeout=min(self.request_timeout_seconds, remaining),
            trust_env=False,
        )
        if response.is_error:
            category = "provider_request_failed"
            try:
                detail = str(response.json().get("detail", ""))
            except (ValueError, AttributeError):
                detail = ""
            for marker, label in (
                ("above the requested response budget", "output_budget_exceeded"),
                ("attempted a tool operation", "tool_operation_rejected"),
                ("not supported", "model_or_schema_rejected"),
                ("usage limit", "provider_usage_limit"),
                ("rate limit", "provider_rate_limit"),
                ("deadline", "provider_deadline"),
            ):
                if marker in detail.lower():
                    category = label
                    break
            # Keep status/category, never response bodies, headers or private reasoning.
            raise RuntimeError(f"Judge HTTP {response.status_code}: {category}")
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Judge response was truncated; no score accepted")
        content = choice["message"]["content"]
        return schema.model_validate_json(content) if schema is not None else content

    async def a_generate(self, prompt, schema=None, **kwargs):
        return await asyncio.to_thread(self.generate, prompt, schema=schema, **kwargs)

    def supports_log_probs(self):
        return False
