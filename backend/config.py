from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://opzen:opzen_local@localhost:55432/opzen"
    minio_endpoint: str = "localhost:59000"
    minio_access_key: str = "opzen"
    minio_secret_key: str = "opzen_local_storage"
    minio_bucket: str = "opzen-documents"
    minio_secure: bool = False
    api_url: str = "http://127.0.0.1:8000"
    demo_identities: str = "analyst:analyst-local-key,reviewer:reviewer-local-key"
    ui_demo_user: str = "analyst"
    max_upload_mb: int = Field(25, ge=1, le=200)
    max_pages: int = Field(200, ge=1)
    pdf_batch_pages: int = Field(20, ge=1)
    chunk_tokens: int = Field(450, ge=64)
    tokenizer_encoding: str = "cl100k_base"
    docling_artifacts_path: str = ""
    ocr_enabled: bool = True
    conversion_timeout: int = Field(180, ge=10)
    lease_seconds: int = Field(600, ge=60)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_dimensions: int = Field(768, ge=1)
    embedding_backend: str = "huggingface"
    embedding_revision: str = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
    groq_api_key: SecretStr = SecretStr("")
    llm_provider: Literal["groq", "openai_compatible"] = "groq"
    llm_model: str = ""
    llm_reasoning_effort: Literal["", "none", "low", "medium", "high"] = ""
    llm_timeout_seconds: int = Field(45, ge=1, le=300)
    llm_max_output_tokens: int | None = Field(None, ge=1, le=8192)
    openai_base_url: str = "http://127.0.0.1:1234/v1"
    openai_api_key: SecretStr = SecretStr("lm-studio")
    groq_model: str = "openai/gpt-oss-120b"
    groq_reasoning_effort: str = "medium"
    groq_max_completion_tokens: int = Field(2048, ge=1, le=8192)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_capture_content: bool = False
    langgraph_strict_msgpack: bool = True
    agent_max_iterations: int = Field(5, ge=1, le=5)
    agent_timeout_seconds: int = Field(180, ge=30, le=1800)
    agent_context_chars: int = Field(36000, ge=4000, le=80000)

    @property
    def identities(self) -> dict[str, str]:
        return dict(pair.strip().split(":", 1) for pair in self.demo_identities.split(","))

    @property
    def checkpoint_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def settings() -> Settings:
    return Settings()
