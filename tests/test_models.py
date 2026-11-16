from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import SecretStr


def test_embedding_limit_is_checked_before_encoder_can_truncate(monkeypatch):
    from backend import embeddings

    encoder = SimpleNamespace(
        tokenizer=SimpleNamespace(encode=lambda *a, **kw: [1] * 513), max_seq_length=512
    )
    monkeypatch.setattr(embeddings, "model", lambda *args: encoder)
    with pytest.raises(ValueError, match="refusing silent truncation"):
        embeddings.encode(["synthetic"], {"embedding_model": "test", "embedding_revision": "test"})


def test_groq_returns_final_content_without_reasoning(monkeypatch):
    from backend import llm

    calls = {}

    class Stream:
        def __iter__(self):
            for content, reasoning in [(None, "private reasoning"), ("OK", None)]:
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content=content, reasoning=reasoning))
                    ]
                )

        def close(self):
            calls["closed"] = True

    def create(**kwargs):
        calls.update(kwargs)
        return Stream()

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    config = SimpleNamespace(
        groq_api_key=SecretStr("test-key"),
        groq_model="openai/gpt-oss-120b",
        groq_max_completion_tokens=2048,
        groq_reasoning_effort="medium",
    )
    monkeypatch.setattr(llm, "settings", lambda: config)
    monkeypatch.setattr(llm, "Groq", Client)
    monkeypatch.setattr(llm, "observe", lambda *args, **kwargs: nullcontext())
    assert "".join(llm.stream_completion([{"role": "user", "content": "synthetic"}])) == "OK"
    assert calls["include_reasoning"] is False
    assert calls["stream"] is True
    assert calls["closed"]


def test_agent_prompt_keeps_evidence_but_not_repeated_geometry():
    from backend.agent import prompt_payload

    item = {
        "text": "Retain seven years.",
        "version_id": "v1",
        "title": "Policy",
        "section_id": "s1",
        "coverage": "complete",
        "sources": [{"page_number": 2, "source_locator": {"bbox": [1, 2, 3, 4]}}],
        "document": {"title": "Policy", "effective_from": "2026-01-01"},
    }
    original = {"evidence": {"evidence": {"e1": item}, "warnings": []}}
    compact = prompt_payload(original)
    assert compact["evidence"]["evidence"]["e1"]["text"] == item["text"]
    assert compact["evidence"]["documents"]["v1"]["effective_from"] == "2026-01-01"
    assert "sources" not in compact["evidence"]["evidence"]["e1"]
    assert original["evidence"]["evidence"]["e1"]["sources"] == item["sources"]
