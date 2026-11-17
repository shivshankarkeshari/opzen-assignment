"""Small leased-job worker with durable LangGraph ingestion steps."""

import logging
import math
import os
import socket
import tempfile
import threading
import time
from datetime import timedelta
from importlib.metadata import version as package_version
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import func, literal_column, or_, select, update

from backend import storage
from backend.chunking import chunk_document
from backend.config import settings
from backend.converter import batches, convert_batch
from backend.db import (
    AuditEvent,
    Chunk,
    Document,
    Embedding,
    Job,
    Result,
    Run,
    SourceSpan,
    Version,
    new_id,
    now,
    transaction,
)
from backend.embeddings import embed_batch, signature
from backend.embeddings import enabled as embedding_enabled
from backend.intake import can
from backend.sections import build_map
from backend.telemetry import observe

logger = logging.getLogger(__name__)


class LostLease(Exception):
    pass


class Cancelled(Exception):
    pass


class ReviewRequired(Exception):
    pass


class State(TypedDict, total=False):
    version_id: str
    manifest: dict
    chunk_count: int
    indexed: bool
    outcome: str


def claim(worker_id: str) -> dict | None:
    with transaction() as session:
        job = session.scalar(
            select(Job)
            .where(
                Job.job_type.in_(["ingestion", "agent"]),
                or_(
                    (Job.status.in_(["queued", "retrying"])) & (Job.available_at <= now()),
                    (Job.status == "leased") & (Job.lease_until < now()),
                ),
            )
            .order_by(Job.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.status, job.lease_owner = "leased", worker_id
        job.attempt_count += 1
        job.fence_token += 1
        job.lease_until = now() + timedelta(seconds=settings().lease_seconds)
        return {
            "job_id": job.job_id,
            "fence": job.fence_token,
            "run_id": job.run_id,
            "version_id": job.payload.get("version_id"),
            "job_type": job.job_type,
        }


class Ingestion:
    def __init__(self, job: dict):
        self.job = job
        with transaction() as session:
            version = session.get(Version, job["version_id"])
            run = session.get(Run, job["run_id"])
            self.config = dict(version.processing_config)
            self.thread_id = run.thread_id
            self.original = dict(version.original_object_ref)
            self.document_id = version.document_id
            self.extension = session.get(Job, job["job_id"]).payload["extension"]

    def guard(self, session):
        job = session.scalar(select(Job).where(Job.job_id == self.job["job_id"]).with_for_update())
        if job.status != "leased" or job.fence_token != self.job["fence"]:
            raise LostLease()
        run = session.get(Run, self.job["run_id"])
        doc = session.get(Document, self.document_id)
        if (
            run.cancel_requested_at
            or doc.state != "active"
            or not can(session, doc.document_id, run.owner_subject)
        ):
            raise Cancelled()
        return job, run, session.get(Version, self.job["version_id"])

    def progress(self, stage: str, status: str, **counts):
        with transaction() as session:
            _, run, version = self.guard(session)
            run.status, run.progress = "running", {"stage": stage, **counts}
            version.status = status

    def extract(self, state: State):
        self.progress("Extracting source structure", "extracting")
        if package_version("docling") != self.config["docling_version"]:
            raise ValueError("Docling version changed; create a new processing revision")
        with transaction() as session:
            _, _, version = self.guard(session)
            manifest = dict(version.artifact_manifest) or {
                "batches": [],
                "tables": [],
                "pictures": [],
                "warnings": [],
            }
        prefix = f"documents/{self.document_id}/{state['version_id']}/attempt-{self.job['fence']}"
        with tempfile.TemporaryDirectory(prefix="opzen-") as temp:
            path = Path(temp) / f"original{self.extension}"
            path.write_bytes(storage.get(self.original))
            for i, (batch, offset, pages) in enumerate(batches(path, self.config)):
                if i < len(manifest["batches"]):
                    storage.get(manifest["batches"][i]["object_ref"])
                    continue
                self.progress(f"Extracting batch {i + 1}", "extracting", processed_pages=offset)
                output = Path(temp) / f"output-{i}"
                with observe("docling.extract", self.job["run_id"]):
                    info = convert_batch(batch, output, self.config)
                ref = storage.put(
                    f"{prefix}/batch-{i}/canonical.json",
                    (output / "canonical.json").read_bytes(),
                    "application/json",
                )
                md_ref = storage.put(
                    f"{prefix}/batch-{i}/preview.md",
                    (output / "preview.md").read_bytes(),
                    "text/plain",
                )
                entry = {
                    "object_ref": ref,
                    "markdown": md_ref,
                    "namespace": f"batch-{i}",
                    "page_offset": offset,
                    "page_count": pages,
                    "status": info["status"],
                }
                manifest["batches"] = [*manifest["batches"], entry]
                for kind in ("tables", "pictures"):
                    items = list(manifest[kind])
                    for item in info[kind]:
                        media = "image/png" if kind == "pictures" else "application/json"
                        asset_ref = storage.put(
                            f"{prefix}/batch-{i}/{item['file']}",
                            (output / item["file"]).read_bytes(),
                            media,
                        )
                        items.append(
                            {
                                **item,
                                "object_ref": asset_ref,
                                "mime_type": media,
                                "namespace": f"batch-{i}",
                                "page_offset": offset,
                            }
                        )
                    manifest[kind] = items
                manifest["warnings"] = [*manifest["warnings"], *info["warnings"]]
                with transaction() as session:
                    _, _, version = self.guard(session)
                    version.artifact_manifest = dict(manifest)
            markdown = "\n\n---\n\n".join(
                storage.get(b["markdown"]).decode() for b in manifest["batches"]
            )
            manifest["markdown"] = storage.put(
                f"{prefix}/preview.md", markdown.encode(), "text/plain"
            )
            if not manifest.get("section_map"):
                manifest["section_map"] = storage.put_json(
                    f"{prefix}/sections.json", build_map(manifest["batches"])
                )
        with transaction() as session:
            _, _, version = self.guard(session)
            version.artifact_manifest = manifest
            version.quality_report = {"warnings": manifest["warnings"], "schema_version": 1}
        if (
            not manifest["batches"]
            or any(b["status"] != "success" for b in manifest["batches"])
            or manifest["warnings"]
        ):
            raise ReviewRequired(
                "Extraction is incomplete or has export warnings; inspect the retained artifacts"
            )
        return {"manifest": manifest}

    def chunk(self, state: State):
        self.progress("Creating source-aware chunks", "chunking")
        with transaction() as session:
            _, _, version = self.guard(session)
            expected = version.readiness_manifest.get("chunks")
            count = session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.version_id == version.version_id)
            )
            if expected and expected == count:
                return {"chunk_count": count}
        chunks = []
        section_map = storage.get_json(state["manifest"]["section_map"])
        for batch in state["manifest"]["batches"]:
            try:
                chunks.extend(
                    chunk_document(
                        storage.get_json(batch["object_ref"]),
                        batch["namespace"],
                        batch["page_offset"],
                        self.config["chunk_tokens"],
                        self.config["tokenizer_encoding"],
                        model_name=self.config["embedding_model"]
                        if self.config.get("embedding_backend") == "huggingface"
                        else None,
                        model_revision=self.config.get("embedding_revision", ""),
                        min_tokens=self.config.get("chunk_min_tokens", 100),
                    )
                )
            except ValueError as exc:
                raise ReviewRequired(str(exc)) from exc
        with transaction() as session:
            _, _, version = self.guard(session)
            counts = [chunk["token_count"] for chunk in chunks]
            version.quality_report = {
                **version.quality_report,
                "chunking": {
                    "strategy": self.config.get("chunking_strategy", "docling_source_aware_v2"),
                    "minimum_body_token_target": self.config.get("chunk_min_tokens", 100),
                    "min_context_tokens": min(counts),
                    "max_context_tokens": max(counts),
                    "chunks_below_context_target": sum(
                        n < self.config.get("chunk_min_tokens", 100) for n in counts
                    ),
                },
            }
            for i, data in enumerate(chunks):
                sources = data.pop("sources")
                data["section_ref"] = (
                    section_map["items"]
                    .get(sources[0]["source_item_ref"], {})
                    .get("section_id", data["section_ref"])
                )
                chunk = Chunk(
                    chunk_id=new_id(),
                    version_id=version.version_id,
                    ordinal=i,
                    **data,
                    search_vector=func.to_tsvector(
                        literal_column("'english'::regconfig"), data["context_text"]
                    ),
                )
                session.add(chunk)
                session.flush()
                for n, source in enumerate(sources):
                    session.add(SourceSpan(chunk_id=chunk.chunk_id, span_order=n, **source))
            version.readiness_manifest = {"chunks": len(chunks), "source_maps_validated": True}
        return {"chunk_count": len(chunks)}

    def embed(self, state: State):
        if not embedding_enabled(self.config):
            return {"indexed": False}
        self.progress("Embedding validated chunks", "indexing", chunks=state["chunk_count"])
        model = self.config["embedding_model"]
        config_signature = signature(self.config)
        with transaction() as session:
            pending = [
                {"id": c.chunk_id, "text": c.context_text, "tokens": c.token_count}
                for c in session.scalars(
                    select(Chunk)
                    .where(Chunk.version_id == state["version_id"])
                    .order_by(Chunk.ordinal)
                )
                if not session.scalar(
                    select(Embedding.embedding_id).where(
                        Embedding.chunk_id == c.chunk_id, Embedding.config_hash == config_signature
                    )
                )
            ]
        for start in range(0, len(pending), 32):
            self.progress("Embedding validated chunks", "indexing", remaining=len(pending) - start)
            group = pending[start : start + 32]
            with observe("embedding.batch", self.job["run_id"]):
                vectors = embed_batch([c["text"] for c in group], self.config)
            if len(vectors) != len(group):
                raise ValueError("Invalid embedding response count")
            with transaction() as session:
                self.guard(session)
                for chunk, vector in zip(group, vectors, strict=True):
                    if len(vector) != self.config["embedding_dimensions"] or not all(
                        math.isfinite(v) for v in vector
                    ):
                        raise ValueError("Invalid embedding vector")
                    session.add(
                        Embedding(
                            chunk_id=chunk["id"],
                            model_name=model,
                            config_hash=config_signature,
                            input_hash=storage.digest(chunk["text"].encode()),
                            input_text=chunk["text"],
                            input_token_count=chunk["tokens"],
                            dimensions=len(vector),
                            vector=vector,
                        )
                    )
        return {"indexed": True}

    def publish(self, state: State):
        with transaction() as session:
            _, run, version = self.guard(session)
            if version.readiness_manifest.get("finalized"):
                return {"outcome": "ready" if version.readiness_manifest["indexed"] else "chunked"}
            session.scalar(
                select(Document).where(Document.document_id == self.document_id).with_for_update()
            )
            # Each immutable revision publishes independently. Read-time revision/date
            # selection means a slow older job cannot overwrite a newer policy, and a
            # future-effective successor does not block today's still-applicable version.
            version.status = "ready" if state["indexed"] else "chunked"
            if state["indexed"]:
                count = session.scalar(
                    select(func.count())
                    .select_from(Embedding)
                    .join(Chunk)
                    .where(Chunk.version_id == version.version_id)
                )
                if count != state["chunk_count"]:
                    raise ValueError("Embedding readiness count mismatch")
                version.published_at = now()
                session.add(
                    Job(
                        job_type="invalidate_results",
                        operation_key=f"invalidate:{version.version_id}",
                        request_hash=version.checksum,
                        status="queued",
                        payload={"document_id": self.document_id},
                    )
                )
            version.readiness_manifest = {
                **version.readiness_manifest,
                "indexed": state["indexed"],
                "finalized": True,
            }
            run.progress = {
                "stage": "Ready"
                if state["indexed"]
                else "Chunked · embedding model not configured",
                "chunks": state["chunk_count"],
            }
            session.add(
                AuditEvent(
                    actor_subject=run.owner_subject,
                    action="published" if state["indexed"] else "extracted",
                    resource_refs={"version_id": version.version_id},
                    decision="completed",
                    correlation_id=run.run_id,
                )
            )
        return {"outcome": "ready" if state["indexed"] else "chunked"}

    def graph(self, saver):
        builder = StateGraph(State)
        nodes = [
            ("extract", self.extract),
            ("chunk", self.chunk),
            ("embed", self.embed),
            ("publish", self.publish),
        ]
        for name, node in nodes:
            builder.add_node(name, node)
        for left, right in zip(
            [START, *[n for n, _ in nodes]], [*[n for n, _ in nodes], END], strict=True
        ):
            builder.add_edge(left, right)
        return builder.compile(checkpointer=saver)


def process(job: dict, saver):
    if job.get("job_type") == "agent":
        from backend.agent import process as process_agent

        return process_agent(job, saver)
    ingestion = Ingestion(job)
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(15):
            try:
                with transaction() as session:
                    session.execute(
                        update(Job)
                        .where(
                            Job.job_id == job["job_id"],
                            Job.fence_token == job["fence"],
                            Job.status == "leased",
                        )
                        .values(lease_until=now() + timedelta(seconds=settings().lease_seconds))
                    )
            except Exception:
                logger.warning("Could not renew job lease")

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    outcome, message = "completed", None
    try:
        graph = ingestion.graph(saver)
        config = {"configurable": {"thread_id": ingestion.thread_id}}
        snapshot = graph.get_state(config)
        with observe("ingestion.run", job["run_id"]):
            graph.invoke(None if snapshot.values else {"version_id": job["version_id"]}, config)
    except LostLease:
        return
    except Cancelled:
        outcome, message = "cancelled", "Cancelled or source access changed"
    except ReviewRequired as exc:
        outcome, message = "partial", str(exc)
    except Exception:
        logger.exception("Ingestion failed for run %s", job["run_id"])
        outcome, message = (
            "failed",
            "Processing failed. Check worker logs and model configuration, then retry.",
        )
    finally:
        stop.set()
        thread.join(timeout=2)
    with transaction() as session:
        current = session.scalar(select(Job).where(Job.job_id == job["job_id"]).with_for_update())
        if current.fence_token != job["fence"] or current.status != "leased":
            return
        run = session.get(Run, job["run_id"])
        current.status = "completed" if outcome in {"completed", "partial"} else outcome
        current.error_code, current.lease_until = message, None
        run.status, run.finished_at = outcome, now()
        if message:
            run.progress = {"stage": message}
            version = session.get(Version, job["version_id"])
            if version.readiness_manifest.get("finalized"):
                # Publication may commit before the final framework checkpoint is acknowledged.
                run.status, current.status, current.error_code = "completed", "completed", None
                version.status = "ready" if version.readiness_manifest["indexed"] else "chunked"
                run.progress = {
                    "stage": version.status.capitalize(),
                    "chunks": version.readiness_manifest["chunks"],
                }
            else:
                version.status = "review_required" if outcome == "partial" else outcome


def invalidate():
    with transaction() as session:
        job = session.scalar(
            select(Job)
            .where(Job.job_type == "invalidate_results", Job.status == "queued")
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job:
            # Conservative for the prototype; includes newly relevant sources with no old dependency.
            session.execute(update(Result).values(freshness_state="stale"))
            job.status = "completed"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    worker_id = f"{socket.gethostname()}:{new_id()}"
    with ConnectionPool(
        settings().checkpoint_url,
        min_size=1,
        max_size=3,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "options": "-c search_path=checkpoints,public",
        },
    ) as pool:
        saver = PostgresSaver(pool)
        while True:
            try:
                invalidate()
                job = claim(worker_id)
                if job:
                    process(job, saver)
                else:
                    time.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception:
                logger.exception("Worker dispatch error")
                time.sleep(3)


if __name__ == "__main__":
    main()
