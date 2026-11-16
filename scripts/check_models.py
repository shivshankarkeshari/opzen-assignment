"""Explicit smoke check with synthetic text; never sends document contents to Groq."""

from backend.config import settings
from backend.embeddings import encode
from backend.llm import model_name, stream_completion
from backend.telemetry import client


def main():
    s = settings()
    vectors = encode(["Synthetic policy: retain customer records for seven years."], s.model_dump())
    print(f"Embeddings: {s.embedding_model}, {len(vectors[0])} dimensions")
    answer = "".join(stream_completion([{"role": "user", "content": "Reply with only OK."}]))
    if not answer.strip():
        raise RuntimeError("LLM returned no final content")
    print(f"LLM: {s.llm_provider}/{model_name()}, final-content stream received")
    if s.langfuse_public_key and s.langfuse_secret_key:
        if not client().auth_check():
            raise RuntimeError("Langfuse authentication failed")
        client().flush()
        print("Langfuse: authenticated; observations flushed")


if __name__ == "__main__":
    main()
