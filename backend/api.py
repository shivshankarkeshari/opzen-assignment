import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from backend import llm, storage
from backend.config import settings
from backend.db import (
    AuditEvent,
    Chunk,
    Dependency,
    Document,
    Job,
    Permission,
    Result,
    Run,
    SourceSpan,
    Version,
    engine,
    now,
    row_dict,
    transaction,
)
from backend.embeddings import enabled as embedding_enabled
from backend.intake import Metadata, document_for, submit, version_for
from backend.retrieval import SearchRequest, eligible

logger = logging.getLogger(__name__)
app = FastAPI(title="Opzen Document Workspace", version="0.1.0")


def identity(x_api_key: Annotated[str, Header()] = "") -> str:
    for name, key in settings().identities.items():
        if secrets.compare_digest(x_api_key, key):
            return f"demo:{name}"
    raise HTTPException(401, "A valid local development API key is required")


Actor = Annotated[str, Depends(identity)]


@app.exception_handler(SQLAlchemyError)
def database_error(request, exc):
    logger.error("Database request failed (%s)", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable; run bootstrap and check services"},
    )


@app.get("/health")
def health():
    checks = {}
    try:
        with engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    try:
        checks["storage"] = storage.client().bucket_exists(settings().minio_bucket)
    except Exception:
        checks["storage"] = False
    return {
        **checks,
        "identity_mode": "local_demo",
        "embedding_configured": embedding_enabled(settings().model_dump()),
        "embedding_model": settings().embedding_model,
        "embedding_dimensions": settings().embedding_dimensions,
        "groq_configured": bool(settings().groq_api_key.get_secret_value()),
        "agent_max_iterations": settings().agent_max_iterations,
        "llm_provider": settings().llm_provider,
        "llm_model": llm.model_name(),
        "llm_configured": llm.configured(),
    }


@app.post("/v1/documents", status_code=202)
@app.post("/v1/documents/{document_id}/versions", status_code=202)
def upload(
    actor: Actor,
    file: Annotated[UploadFile, File()],
    metadata: Annotated[str, Form()] = "{}",
    idempotency_key: Annotated[str, Header(min_length=1, max_length=160)] = "",
    document_id: UUID | None = None,
):
    if not idempotency_key:
        raise HTTPException(422, "Idempotency-Key header is required")
    try:
        parsed = Metadata.model_validate_json(metadata)
    except ValidationError:
        raise HTTPException(
            422, "Invalid metadata; check dates, document type, and sharing identities"
        ) from None
    data = file.file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    try:
        return submit(
            file.filename or "document",
            data,
            parsed,
            actor,
            idempotency_key,
            str(document_id) if document_id else None,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Upload storage failed")
        raise HTTPException(
            503, "Upload could not be stored; retry with the same request key"
        ) from None


@app.get("/v1/documents")
def documents(actor: Actor, search: str = "", limit: int = 50, offset: int = 0):
    with transaction() as session:
        query = (
            select(Document)
            .join(Permission)
            .where(
                Permission.subject_id == actor,
                Permission.action == "read",
                Permission.revoked_at.is_(None),
                Document.state == "active",
            )
            .order_by(Document.created_at.desc())
        )
        if search:
            query = query.where(Document.display_filename.ilike(f"%{search[:100]}%"))
        rows = session.scalars(query.limit(max(1, min(limit, 100))).offset(max(offset, 0))).all()
        applicable_ids = {
            v["document_id"]: v["version_id"]
            for start in range(0, len(rows), 20)
            for v in eligible(
                session,
                actor,
                SearchRequest(
                    query="library", document_ids=[r.document_id for r in rows[start : start + 20]]
                ),
            )
        }
        output = []
        for doc in rows:
            versions = session.scalars(
                select(Version)
                .where(Version.document_id == doc.document_id)
                .order_by(Version.revision_number.desc())
            ).all()
            latest = versions[0] if versions else None
            applicable_id = applicable_ids.get(doc.document_id)
            output.append(
                {
                    **row_dict(doc),
                    "latest_version": row_dict(latest) if latest else None,
                    "applicable_version_id": applicable_id,
                    "revision_count": len(versions),
                }
            )
        return output


@app.post("/v1/versions/{version_id}/reprocess", status_code=202)
def reprocess(
    version_id: UUID,
    actor: Actor,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=160)],
):
    with transaction() as session:
        v = version_for(session, str(version_id), actor)
        document_for(session, v.document_id, actor, "manage")
        metadata = Metadata.model_validate(v.metadata_snapshot)
        filename = (
            v.metadata_snapshot.get("filename") or v.original_object_ref["key"].rsplit("/", 1)[-1]
        )
        doc_id = v.document_id
        reuse = {
            "version_id": v.version_id,
            "original": v.original_object_ref,
            "manifest": v.artifact_manifest,
            "config": v.processing_config,
        }
    return submit(
        filename,
        storage.get(reuse["original"]),
        metadata,
        actor,
        idempotency_key,
        doc_id,
        reuse=reuse,
    )


@app.get("/v1/documents/{document_id}")
def document(document_id: UUID, actor: Actor):
    with transaction() as session:
        return row_dict(document_for(session, str(document_id), actor))


@app.get("/v1/documents/{document_id}/versions")
def versions(document_id: UUID, actor: Actor):
    with transaction() as session:
        document_for(session, str(document_id), actor)
        return [
            row_dict(v)
            for v in session.scalars(
                select(Version)
                .where(Version.document_id == str(document_id))
                .order_by(Version.revision_number.desc())
            )
        ]


@app.get("/v1/versions/{version_id}/chunks")
def chunks(version_id: UUID, actor: Actor, limit: int = 30, offset: int = 0):
    with transaction() as session:
        version_for(session, str(version_id), actor)
        query = select(Chunk).where(Chunk.version_id == str(version_id)).order_by(Chunk.ordinal)
        total = session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.version_id == str(version_id))
        )
        rows = session.scalars(query.limit(max(1, min(limit, 100))).offset(max(0, offset)))
        return {
            "total": total,
            "items": [
                {
                    **{k: v for k, v in row_dict(c).items() if k != "search_vector"},
                    "sources": [
                        row_dict(s)
                        for s in session.scalars(
                            select(SourceSpan)
                            .where(SourceSpan.chunk_id == c.chunk_id)
                            .order_by(SourceSpan.span_order)
                        )
                    ],
                }
                for c in rows
            ],
        }


@app.get("/v1/versions/{version_id}/artifacts")
def artifacts(version_id: UUID, actor: Actor):
    with transaction() as session:
        v = version_for(session, str(version_id), actor)
        return {"status": v.status, "manifest": v.artifact_manifest, "quality": v.quality_report}


@app.get("/v1/versions/{version_id}/content")
def content(version_id: UUID, actor: Actor, kind: str = "original", index: int = 0):
    with transaction() as session:
        v = version_for(session, str(version_id), actor)
        if kind == "original":
            ref, media = v.original_object_ref, v.mime_type
        elif kind == "markdown":
            ref, media = v.artifact_manifest.get("markdown"), "text/plain; charset=utf-8"
        elif kind in {"tables", "pictures", "batches"}:
            items = v.artifact_manifest.get(kind, [])
            if index < 0 or index >= len(items):
                raise HTTPException(404, "Artifact not found")
            item = items[index]
            ref, media = item.get("object_ref"), item.get("mime_type", "application/json")
        else:
            raise HTTPException(422, "Unknown artifact kind")
        if not ref:
            raise HTTPException(404, "Artifact is not available yet")
    data = storage.get(ref)
    with transaction() as session:
        version_for(session, str(version_id), actor)
    return Response(data, media_type=media, headers={"Cache-Control": "no-store"})


@app.get("/v1/source-spans/{source_id}")
def source_span(source_id: UUID, actor: Actor):
    with transaction() as session:
        source = session.get(SourceSpan, str(source_id))
        if not source:
            raise HTTPException(404, "Source not found")
        chunk = session.get(Chunk, source.chunk_id)
        version_for(session, chunk.version_id, actor)
        return {**row_dict(source), "version_id": chunk.version_id}


@app.get("/v1/runs")
def runs(actor: Actor):
    with transaction() as session:
        return [
            row_dict(r)
            for r in session.scalars(
                select(Run)
                .join(Permission, Permission.document_id == Run.document_id)
                .where(
                    Run.owner_subject == actor,
                    Permission.subject_id == actor,
                    Permission.action == "read",
                    Permission.revoked_at.is_(None),
                )
                .order_by(Run.created_at.desc())
                .limit(100)
            )
        ]


def owned_run(session, run_id: str, actor: str):
    run = session.get(Run, run_id)
    if not run or run.owner_subject != actor:
        raise HTTPException(404, "Run not found")
    if run.workflow_type == "agent":
        from backend.agent_api import run_for

        return run_for(session, run_id, actor)
    document_for(session, run.document_id, actor)
    return run


@app.get("/v1/runs/{run_id}")
def run_status(run_id: UUID, actor: Actor):
    with transaction() as session:
        return row_dict(owned_run(session, str(run_id), actor))


@app.post("/v1/runs/{run_id}/cancel")
def cancel(run_id: UUID, actor: Actor):
    with transaction() as session:
        run = owned_run(session, str(run_id), actor)
        if run.status not in {"completed", "failed", "cancelled", "partial"}:
            run.cancel_requested_at = now()
        return {"status": run.status, "cancel_requested": bool(run.cancel_requested_at)}


@app.post("/v1/runs/{run_id}/retry")
def retry(run_id: UUID, actor: Actor):
    with transaction() as session:
        run = owned_run(session, str(run_id), actor)
        job = session.scalar(select(Job).where(Job.run_id == run.run_id).with_for_update())
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "Only failed or cancelled runs can be retried")
        if run.workflow_type != "agent":
            document_for(session, run.document_id, actor, "manage")
        run.cancel_requested_at, run.finished_at = None, None
        run.status, run.progress = "queued", {"stage": "Queued to resume"}
        job.status, job.error_code, job.available_at = "queued", None, now()
        return {"run_id": run.run_id, "status": run.status}


class Grant(BaseModel):
    user: str
    allow: bool


@app.put("/v1/documents/{document_id}/permissions")
def permission(document_id: UUID, grant: Grant, actor: Actor):
    if grant.user not in settings().identities:
        raise HTTPException(422, "Unknown demo identity")
    subject = f"demo:{grant.user}"
    with transaction() as session:
        doc = document_for(session, str(document_id), actor, "manage")
        if subject == doc.owner_subject:
            raise HTTPException(422, "Owner access cannot be changed in this demo")
        p = session.scalar(
            select(Permission).where(
                Permission.document_id == doc.document_id,
                Permission.subject_id == subject,
                Permission.action == "read",
            )
        )
        if not p:
            p = Permission(
                document_id=doc.document_id, subject_id=subject, action="read", granted_by=actor
            )
            session.add(p)
        p.revoked_at = None if grant.allow else now()
        doc.acl_revision += 1
        session.add(
            AuditEvent(
                actor_subject=actor,
                action="permission_changed",
                resource_refs={"document_id": doc.document_id},
                decision="completed",
                correlation_id=doc.document_id,
                details={"subject": subject, "allow": grant.allow},
            )
        )
        affected = select(Dependency.result_id).where(Dependency.document_id == doc.document_id)
        for result in session.scalars(select(Result).where(Result.result_id.in_(affected))):
            result.freshness_state = "access_check_required"
        return {"acl_revision": doc.acl_revision}


from backend.agent_api import register  # noqa: E402

register(app, identity)
