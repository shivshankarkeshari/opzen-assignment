"""Local Hugging Face embeddings with explicit tokenizer and input-limit checks."""

from functools import lru_cache

import httpx

from backend.config import settings


def profile() -> dict:
    s = settings()
    return {
        key: getattr(s, key)
        for key in (
            "embedding_backend",
            "embedding_model",
            "embedding_revision",
            "embedding_dimensions",
            "embedding_base_url",
            "tokenizer_encoding",
        )
    }


def signature(config: dict) -> str:
    """Preserve ingestion's original signature for already indexed vectors."""
    import json

    from backend.storage import digest

    return digest(
        json.dumps(
            {
                "backend": config.get("embedding_backend", "openai"),
                "model": config["embedding_model"],
                "revision": config.get("embedding_revision", ""),
                "dimensions": config["embedding_dimensions"],
                "endpoint": config["embedding_base_url"],
                "tokenizer": config["tokenizer_encoding"],
            },
            sort_keys=True,
        ).encode()
    )


def enabled(config: dict) -> bool:
    backend = config.get("embedding_backend", "openai")
    return bool(config.get("embedding_model")) and (
        backend == "huggingface" or (backend == "openai" and bool(config.get("embedding_base_url")))
    )


@lru_cache(maxsize=2)
def tokenizer(model_name: str, revision: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        model_name, revision=revision or None, trust_remote_code=False
    )


@lru_cache(maxsize=1)
def model(model_name: str, revision: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        model_name, revision=revision or None, device="cpu", trust_remote_code=False
    )


def encode(texts: list[str], config: dict, *, query=False) -> list[list[float]]:
    encoder = model(config["embedding_model"], config["embedding_revision"])
    if query:
        texts = ["Represent this sentence for searching relevant passages: " + t for t in texts]
    counts = [len(encoder.tokenizer.encode(text, add_special_tokens=True)) for text in texts]
    if any(count > encoder.max_seq_length for count in counts):
        raise ValueError("Embedding input exceeds the model limit; refusing silent truncation")
    if encoder.get_sentence_embedding_dimension() != config["embedding_dimensions"]:
        raise ValueError("Configured embedding dimensions do not match the local model")
    return encoder.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
        convert_to_numpy=True,
    ).tolist()


def embed_batch(texts: list[str], config: dict) -> list[list[float]]:
    if config.get("embedding_backend", "openai") == "huggingface":
        return encode(texts, config)
    with httpx.Client(timeout=90) as client:
        response = client.post(
            config["embedding_base_url"].rstrip("/") + "/embeddings",
            headers={"Authorization": f"Bearer {settings().embedding_api_key}"},
            json={
                "model": config["embedding_model"],
                "input": texts,
                "dimensions": config["embedding_dimensions"],
            },
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
    if len(rows) != len(texts) or [r["index"] for r in rows] != list(range(len(texts))):
        raise ValueError("Invalid embedding response count or indices")
    return [row["embedding"] for row in rows]
