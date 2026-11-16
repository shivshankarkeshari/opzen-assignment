from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import lru_cache
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from backend.config import settings


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    metadata = MetaData(schema="app")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Document(Base):
    __tablename__ = "documents"
    document_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    owner_subject: Mapped[str]
    display_filename: Mapped[str]
    source_kind: Mapped[str] = mapped_column(default="upload")
    source_key: Mapped[str] = mapped_column(default=new_id, unique=True)
    state: Mapped[str] = mapped_column(default="active")
    acl_revision: Mapped[int] = mapped_column(default=1)


class Version(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "revision_number"),)
    version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("app.documents.document_id"), index=True)
    revision_number: Mapped[int]
    revision_kind: Mapped[str] = mapped_column(default="content")
    replaces_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("app.document_versions.version_id")
    )
    checksum: Mapped[str]
    mime_type: Mapped[str]
    size_bytes: Mapped[int]
    title: Mapped[str]
    document_type: Mapped[str]
    department_code: Mapped[str]
    approval_status: Mapped[str]
    effective_from: Mapped[date | None]
    effective_to: Mapped[date | None]
    metadata_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    original_object_ref: Mapped[dict] = mapped_column(JSONB, default=dict)
    artifact_manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    processing_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    quality_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    readiness_manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(default="uploading")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[str]


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal"),
        Index("ix_chunks_search", "search_vector", postgresql_using="gin"),
    )
    chunk_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("app.document_versions.version_id"), index=True
    )
    ordinal: Mapped[int]
    chunk_kind: Mapped[str] = mapped_column(default="text")
    section_ref: Mapped[str | None]
    heading_path: Mapped[list] = mapped_column(JSONB, default=list)
    body_text: Mapped[str]
    context_text: Mapped[str]
    body_hash: Mapped[str]
    context_hash: Mapped[str]
    token_count: Mapped[int]
    quality_state: Mapped[str] = mapped_column(default="verified")
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)


class SourceSpan(Base):
    __tablename__ = "chunk_sources"
    source_span_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_id
    )
    chunk_id: Mapped[str] = mapped_column(ForeignKey("app.chunks.chunk_id"), index=True)
    span_order: Mapped[int]
    source_item_ref: Mapped[str]
    page_number: Mapped[int | None]
    source_locator: Mapped[dict] = mapped_column(JSONB)
    span_role: Mapped[str] = mapped_column(default="body")
    mapping_status: Mapped[str] = mapped_column(default="verified")


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("chunk_id", "config_hash"),)
    embedding_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("app.chunks.chunk_id"), index=True)
    model_name: Mapped[str]
    config_hash: Mapped[str]
    input_hash: Mapped[str]
    input_text: Mapped[str]
    input_token_count: Mapped[int]
    dimensions: Mapped[int]
    vector: Mapped[list] = mapped_column(Vector())


class Permission(Base):
    __tablename__ = "document_permissions"
    __table_args__ = (UniqueConstraint("document_id", "subject_id", "action"),)
    permission_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_id
    )
    document_id: Mapped[str] = mapped_column(ForeignKey("app.documents.document_id"), index=True)
    subject_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    granted_by: Mapped[str]
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "workflow_runs"
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("app.documents.document_id"), index=True
    )
    owner_subject: Mapped[str] = mapped_column(index=True)
    workflow_type: Mapped[str] = mapped_column(default="ingestion")
    status: Mapped[str] = mapped_column(default="uploading")
    input_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    thread_id: Mapped[str] = mapped_column(default=new_id, unique=True)
    graph_version: Mapped[str] = mapped_column(default="ingestion-v1")
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Result(Base):
    __tablename__ = "results"
    result_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("app.workflow_runs.run_id"), index=True)
    result_kind: Mapped[str]
    body: Mapped[dict] = mapped_column(JSONB)
    freshness_state: Mapped[str] = mapped_column(default="current")


class Dependency(Base):
    __tablename__ = "result_dependencies"
    dependency_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_id
    )
    result_id: Mapped[str] = mapped_column(ForeignKey("app.results.result_id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("app.documents.document_id"), index=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("app.document_versions.version_id"), index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(ForeignKey("app.chunks.chunk_id"))
    claim_key: Mapped[str]
    dependency_role: Mapped[str]


class Feedback(Base):
    __tablename__ = "feedback"
    feedback_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("app.workflow_runs.run_id"))
    submitted_by: Mapped[str]
    issue_type: Mapped[str]
    comment: Mapped[str | None]
    review_state: Mapped[str] = mapped_column(default="new")


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("app.workflow_runs.run_id"), index=True)
    job_type: Mapped[str] = mapped_column(default="ingestion")
    operation_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(default="uploading", index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    lease_owner: Mapped[str | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fence_token: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None]


class AuditEvent(Base):
    __tablename__ = "audit_events"
    audit_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    actor_subject: Mapped[str]
    action: Mapped[str]
    resource_refs: Mapped[dict] = mapped_column(JSONB)
    decision: Mapped[str]
    correlation_id: Mapped[str]
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True)


@contextmanager
def transaction():
    with Session(engine(), expire_on_commit=False) as session, session.begin():
        yield session


def row_dict(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
