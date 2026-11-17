import io
import json
import zipfile
from datetime import date
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend import storage
from backend.config import settings
from backend.db import AuditEvent, Document, Job, Permission, Run, Version, new_id, transaction


class Metadata(BaseModel):
    title: str = Field(default="", max_length=200)
    document_type: Literal["policy", "procedure", "contract", "guideline"] = "policy"
    department_code: str = Field(default="General", min_length=1, max_length=80)
    approval_status: Literal["draft", "approved"] = "draft"
    effective_from: date | None = None
    effective_to: date | None = None
    recorded_author: str = Field(default="", max_length=200)
    share_with: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("Effective end must be after effective start")
        if any(name not in settings().identities for name in self.share_with):
            raise ValueError("Unknown demo identity")
        return self


def can(session, document_id: str, subject: str, action="read") -> bool:
    return (
        session.scalar(
            select(Permission.permission_id).where(
                Permission.document_id == document_id,
                Permission.subject_id == subject,
                Permission.action == action,
                Permission.revoked_at.is_(None),
            )
        )
        is not None
    )


def document_for(session, document_id: str, subject: str, action="read") -> Document:
    document = session.get(Document, document_id)
    if not document or document.state != "active" or not can(session, document_id, subject, action):
        with transaction() as audit:
            audit.add(
                AuditEvent(
                    actor_subject=subject,
                    action="document_access",
                    resource_refs={"document_id": document_id},
                    decision="denied",
                    correlation_id=new_id(),
                    details={"action": action},
                )
            )
        raise HTTPException(404, "Document not found")
    return document


def version_for(session, version_id: str, subject: str) -> Version:
    version = session.get(Version, version_id)
    if not version:
        raise HTTPException(404, "Version not found")
    document_for(session, version.document_id, subject)
    return version


def validate_file(filename: str, data: bytes) -> str:
    if not data:
        raise HTTPException(422, "The uploaded file is empty")
    if len(data) > settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "File exceeds the configured upload limit")
    extension = Path(filename).suffix.lower()
    if extension == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("Not a Word document")
                if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValueError("Expanded document exceeds the safety limit")
        except (ValueError, zipfile.BadZipFile):
            raise HTTPException(422, "Invalid or oversized DOCX archive") from None
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if extension == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if extension in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise HTTPException(
        422, "Supported files: PDF, DOCX, PNG, and JPEG; content must match the extension"
    )


def receipt(session, job: Job) -> dict:
    run = session.get(Run, job.run_id)
    return {
        "document_id": run.document_id,
        "version_id": job.payload["version_id"],
        "run_id": run.run_id,
        "job_id": job.job_id,
        "status": run.status,
    }


def submit(
    filename: str,
    data: bytes,
    metadata: Metadata,
    subject: str,
    key: str,
    document_id: str | None = None,
    reuse: dict | None = None,
) -> dict:
    mime_type = validate_file(filename, data)
    checksum = storage.digest(data)
    body = metadata.model_dump(mode="json")
    request_hash = storage.digest(
        json.dumps(
            {
                "metadata": body,
                "checksum": checksum,
                "document_id": document_id,
                "filename": filename,
                "reprocess_from": reuse["version_id"] if reuse else None,
            },
            sort_keys=True,
        ).encode()
    )
    operation_key = f"upload:{subject}:{storage.digest(key.encode())}"

    def reserve():
        with transaction() as session:
            existing = session.scalar(select(Job).where(Job.operation_key == operation_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise HTTPException(409, "Idempotency key was already used for different input")
                document_for(session, existing.payload["document_id"], subject, "manage")
                return existing.job_id
            doc_id = document_id or new_id()
            if document_id:
                document_for(session, document_id, subject, "manage")
                session.scalar(
                    select(Document).where(Document.document_id == doc_id).with_for_update()
                )
            else:
                session.add(
                    Document(document_id=doc_id, owner_subject=subject, display_filename=filename)
                )
                session.flush()
                for name in {subject, *(f"demo:{name}" for name in metadata.share_with)}:
                    actions = ["read", "manage"] if name == subject else ["read"]
                    for action in actions:
                        session.add(
                            Permission(
                                document_id=doc_id,
                                subject_id=name,
                                action=action,
                                granted_by=subject,
                            )
                        )
            number = (
                session.scalar(
                    select(func.max(Version.revision_number)).where(Version.document_id == doc_id)
                )
                or 0
            ) + 1
            version_id, run_id, job_id = new_id(), new_id(), new_id()
            names = [
                "chunk_tokens",
                "chunk_min_tokens",
                "tokenizer_encoding",
                "ocr_enabled",
                "pdf_batch_pages",
                "max_pages",
                "conversion_timeout",
                "docling_artifacts_path",
                "embedding_base_url",
                "embedding_backend",
                "embedding_revision",
                "embedding_model",
                "embedding_dimensions",
            ]
            config = {name: getattr(settings(), name) for name in names}
            config.update(
                docling_version=package_version("docling"),
                schema_version=1,
                chunking_strategy="docling_source_aware_v2",
            )
            version = Version(
                version_id=version_id,
                document_id=doc_id,
                revision_number=number,
                checksum=checksum,
                mime_type=mime_type,
                size_bytes=len(data),
                title=metadata.title.strip() or Path(filename).stem,
                document_type=metadata.document_type,
                department_code=metadata.department_code,
                approval_status=metadata.approval_status,
                effective_from=metadata.effective_from,
                effective_to=metadata.effective_to,
                metadata_snapshot={**body, "filename": filename},
                processing_config=config,
                recorded_by=subject,
                revision_kind="reprocess" if reuse else "content",
                replaces_version_id=reuse["version_id"] if reuse else None,
            )
            if (
                reuse
                and reuse["manifest"].get("batches")
                and not reuse["manifest"].get("warnings")
                and all(batch.get("status") == "success" for batch in reuse["manifest"]["batches"])
                and all(
                    config.get(k) == reuse["config"].get(k)
                    for k in ["docling_version", "ocr_enabled", "pdf_batch_pages"]
                )
            ):
                version.artifact_manifest = reuse["manifest"]
            session.add(version)
            session.add(
                Run(
                    run_id=run_id,
                    document_id=doc_id,
                    owner_subject=subject,
                    input_json={"version_id": version_id},
                    config_snapshot=config,
                )
            )
            session.flush()
            session.add(
                Job(
                    job_id=job_id,
                    run_id=run_id,
                    operation_key=operation_key,
                    request_hash=request_hash,
                    payload={
                        "version_id": version_id,
                        "document_id": doc_id,
                        "extension": Path(filename).suffix.lower(),
                    },
                )
            )
            session.add(
                AuditEvent(
                    actor_subject=subject,
                    action="upload_reserved",
                    resource_refs={"document_id": doc_id, "version_id": version_id},
                    decision="allowed",
                    correlation_id=run_id,
                )
            )
            return job_id

    try:
        job_id = reserve()
    except IntegrityError:
        # A concurrent identical request may have reserved the unique operation key first.
        job_id = reserve()
    with transaction() as session:
        job = session.get(Job, job_id)
        if job.status != "uploading":
            return receipt(session, job)
        version = session.get(Version, job.payload["version_id"])
        object_key = f"documents/{version.document_id}/{version.version_id}/original{job.payload['extension']}"
    ref = reuse["original"] if reuse else storage.put(object_key, data, mime_type)
    with transaction() as session:
        job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
        if job.status == "uploading":
            version = session.get(Version, job.payload["version_id"])
            document_for(session, version.document_id, subject, "manage")
            version.original_object_ref = ref
            version.status = "queued"
            job.status = "queued"
            run = session.get(Run, job.run_id)
            run.status, run.progress = "queued", {"stage": "Waiting for worker"}
        return receipt(session, job)
