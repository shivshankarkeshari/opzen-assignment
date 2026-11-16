"""Shared authorized revision selection and exact hybrid retrieval."""

import math
import threading
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, text

from backend import embeddings
from backend.db import (
    Chunk,
    Document,
    Embedding,
    Permission,
    SourceSpan,
    Version,
    row_dict,
    transaction,
)

inference_lock = threading.BoundedSemaphore(1)


def today() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    scope: Literal["current", "effective_on", "selected_versions"] = "current"
    effective_on: date | None = None
    document_ids: list[UUID] = Field(default_factory=list, max_length=20)
    version_ids: list[UUID] = Field(default_factory=list, max_length=10)
    document_types: list[str] = Field(default_factory=list, max_length=4)
    departments: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(8, ge=1, le=20)

    @model_validator(mode="after")
    def valid_scope(self):
        self.query = self.query.strip()
        if not self.query or (self.scope == "effective_on") != bool(self.effective_on):
            raise ValueError("Provide a query and an effective date only for effective_on scope")
        if (self.scope == "selected_versions") != bool(self.version_ids):
            raise ValueError("Provide exact revisions only for selected_versions scope")
        return self


def eligible(session, actor: str, request: SearchRequest) -> list[dict]:
    query = (
        select(Version, Document.acl_revision)
        .join(Document)
        .where(
            Document.state == "active",
            Version.status == "ready",
            Version.published_at.is_not(None),
            select(Permission.permission_id)
            .where(
                Permission.document_id == Document.document_id,
                Permission.subject_id == actor,
                Permission.action == "read",
                Permission.revoked_at.is_(None),
            )
            .exists(),
        )
    )
    if request.document_ids:
        query = query.where(Version.document_id.in_([str(x) for x in request.document_ids]))
    if request.scope == "selected_versions":
        query = query.where(Version.version_id.in_([str(x) for x in request.version_ids]))
    else:
        effective = request.effective_on or today()
        query = query.where(
            Version.approval_status == "approved",
            Version.effective_from <= effective,
            (Version.effective_to.is_(None)) | (Version.effective_to > effective),
        )
    ranked = (
        select(
            Version.version_id,
            func.row_number()
            .over(partition_by=Version.document_id, order_by=Version.revision_number.desc())
            .label("position"),
        )
        .where(Version.version_id.in_(query.with_only_columns(Version.version_id)))
        .subquery()
    )
    if request.scope != "selected_versions":
        query = query.where(
            Version.version_id.in_(select(ranked.c.version_id).where(ranked.c.position == 1))
        )
    if request.document_types:
        query = query.where(Version.document_type.in_(request.document_types))
    if request.departments:
        query = query.where(Version.department_code.in_(request.departments))
    rows = session.execute(query.order_by(Version.version_id).limit(101)).all()
    if len(rows) > 100:
        raise HTTPException(422, "Narrow the search to at most 100 authorized revisions")
    result = [{**row_dict(v), "acl_revision": acl} for v, acl in rows]
    if request.scope == "selected_versions" and {r["version_id"] for r in result} != {
        str(x) for x in request.version_ids
    }:
        raise HTTPException(404, "Selected revision unavailable")
    if request.scope == "selected_versions":
        order = {str(version_id): i for i, version_id in enumerate(request.version_ids)}
        result.sort(key=lambda row: order[row["version_id"]])
    return result


def authorize_snapshot(session, actor: str, versions: list[dict]):
    ids = [v["version_id"] for v in versions]
    rows = session.execute(
        select(Version.version_id, Document.acl_revision)
        .join(Document)
        .where(
            Version.version_id.in_(ids),
            Version.status == "ready",
            Document.state == "active",
            select(Permission.permission_id)
            .where(
                Permission.document_id == Document.document_id,
                Permission.subject_id == actor,
                Permission.action == "read",
                Permission.revoked_at.is_(None),
            )
            .exists(),
        )
    ).all()
    if {r[0] for r in rows} != set(ids):
        raise HTTPException(404, "Source scope unavailable; rerun with current access")


def metadata(versions: list[dict]) -> list[dict]:
    keys = (
        "document_id",
        "version_id",
        "title",
        "revision_number",
        "document_type",
        "department_code",
        "effective_from",
        "effective_to",
        "approval_status",
    )
    return [
        {key: str(v[key]) if isinstance(v[key], date) else v[key] for key in keys} for v in versions
    ]


def fuse(dense: list, lexical: list, limit=60) -> list[dict]:
    found = {}
    for branch, rows in (("semantic", dense), ("keyword", lexical)):
        for rank, row in enumerate(rows, 1):
            key, score = row
            hit = found.setdefault(key, {"chunk_id": key, "rrf": 0.0})
            hit["rrf"] += 1 / (60 + rank)
            hit[branch] = float(score)
    return sorted(found.values(), key=lambda r: (-r["rrf"], r["chunk_id"]))[:limit]


def search(actor: str, request: SearchRequest, versions: list[dict] | None = None) -> dict:
    with transaction() as session:
        versions = eligible(session, actor, request) if versions is None else versions
        authorize_snapshot(session, actor, versions)
    profile = embeddings.profile()
    if profile["embedding_backend"] != "huggingface":
        raise HTTPException(503, "Search requires the configured local Hugging Face profile")
    compatible = [
        v
        for v in versions
        if embeddings.signature(v["processing_config"]) == embeddings.signature(profile)
    ]
    warnings = (
        ["Some selected revisions need reprocessing for the active model"]
        if len(compatible) < len(versions)
        else []
    )
    if not compatible:
        return {"items": [], "warnings": warnings, "retrieval_mode": "hybrid"}
    if not inference_lock.acquire(timeout=5):
        raise HTTPException(503, "Query encoder busy; try again shortly")
    try:
        try:
            vector = embeddings.encode([request.query], profile, query=True)[0]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    finally:
        inference_lock.release()
    if (
        len(vector) != profile["embedding_dimensions"]
        or not all(math.isfinite(x) for x in vector)
        or not any(vector)
    ):
        raise HTTPException(503, "Invalid query embedding")
    ids = [v["version_id"] for v in compatible]
    # One snapshot for both branches; filtering precedes all distance evaluation.
    with transaction() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        authorize_snapshot(session, actor, versions)
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        corpus = (
            select(Embedding.chunk_id, Embedding.vector, Chunk.search_vector)
            .join(Chunk)
            .where(
                Chunk.version_id.in_(ids),
                Chunk.quality_state == "verified",
                Embedding.config_hash == embeddings.signature(profile),
                Embedding.dimensions == len(vector),
                func.vector_dims(Embedding.vector) == len(vector),
            )
            .cte("eligible_chunks")
            .prefix_with("MATERIALIZED")
        )
        distance = corpus.c.vector.cosine_distance(vector)
        dense = session.execute(
            select(corpus.c.chunk_id, distance).order_by(distance, corpus.c.chunk_id).limit(40)
        ).all()
        query = func.websearch_to_tsquery("english", request.query)
        rank = func.ts_rank_cd(corpus.c.search_vector, query)
        lexical = session.execute(
            select(corpus.c.chunk_id, rank)
            .where(corpus.c.search_vector.op("@@")(query))
            .order_by(rank.desc(), corpus.c.chunk_id)
            .limit(40)
        ).all()
        fused = fuse(dense, lexical)[: request.limit]
        items = []
        version_map = {v["version_id"]: v for v in compatible}
        for hit in fused:
            chunk = session.get(Chunk, hit["chunk_id"])
            sources = [
                row_dict(s)
                for s in session.scalars(
                    select(SourceSpan)
                    .where(SourceSpan.chunk_id == chunk.chunk_id)
                    .order_by(SourceSpan.span_order)
                )
            ]
            if not sources or any(s["mapping_status"] != "verified" for s in sources):
                continue
            items.append(
                {
                    **row_dict(chunk),
                    "scores": hit,
                    "sources": sources,
                    "document": metadata([version_map[chunk.version_id]])[0],
                }
            )
        for item in items:
            item.pop("search_vector", None)
    with transaction() as session:
        authorize_snapshot(session, actor, versions)
        if request.scope != "selected_versions":
            fresh = eligible(session, actor, request)
            if {v["version_id"] for v in fresh} != {v["version_id"] for v in versions}:
                raise HTTPException(409, "Applicable revisions changed; search again")
    return {
        "items": items,
        "warnings": warnings,
        "retrieval_mode": "hybrid",
        "resolved_effective_on": request.effective_on or today(),
    }
