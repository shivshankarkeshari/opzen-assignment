import json
from datetime import timedelta

import pytest
from fastapi import HTTPException

from backend.intake import Metadata, validate_file


def upload(api, data, key="test-upload", path="/v1/documents"):
    return api.post(
        path,
        headers={"Idempotency-Key": key},
        files={"file": ("policy.docx", data)},
        data={
            "metadata": json.dumps(
                {
                    "title": "Synthetic policy",
                    "approval_status": "approved",
                    "effective_from": "2026-01-01",
                }
            )
        },
    )


def test_input_validation():
    with pytest.raises(HTTPException):
        validate_file("fake.pdf", b"not a pdf")
    with pytest.raises(HTTPException):
        validate_file("fake.docx", b"PK invalid archive")
    with pytest.raises(ValueError):
        Metadata(effective_from="2026-11-15", effective_to="2026-01-01")


def test_native_structure_and_multiple_source_pages():
    from docling_core.types.doc import DocItemLabel, DoclingDocument, ProvenanceItem
    from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size

    from backend.chunking import chunk_document

    doc = DoclingDocument(name="synthetic")
    for number in [1, 2]:
        doc.add_page(page_no=number, size=Size(width=600, height=800))
    doc.add_heading("Retention", level=1)
    text = doc.add_text(label=DocItemLabel.TEXT, text="No exceptions.")
    for page in [1, 2]:
        text.prov.append(
            ProvenanceItem(
                page_no=page,
                charspan=(0, 14),
                bbox=BoundingBox(l=10, t=10, r=100, b=50, coord_origin=CoordOrigin.TOPLEFT),
            )
        )
    chunks = chunk_document(doc.export_to_dict(), "batch-2", 20, 64, "cl100k_base")
    assert any("No exceptions." in c["body_text"] for c in chunks)
    assert all(c["token_count"] <= 64 for c in chunks)
    pages = {s["page_number"] for c in chunks for s in c["sources"] if s["page_number"]}
    assert pages == {21, 22}
    assert all(s["source_item_ref"].startswith("batch-2:") for c in chunks for s in c["sources"])


@pytest.mark.integration
def test_upload_idempotency_access_and_original_integrity(api, docx_bytes):
    first = upload(api, docx_bytes)
    assert first.status_code == 202, first.text
    receipt = first.json()
    assert upload(api, docx_bytes).json() == receipt
    changed = api.post(
        "/v1/documents",
        headers={"Idempotency-Key": "test-upload"},
        files={"file": ("policy.docx", docx_bytes)},
        data={"metadata": '{"title":"Different"}'},
    )
    assert changed.status_code == 409
    assert len(api.get("/v1/documents").json()) == 1
    original = api.get(f"/v1/versions/{receipt['version_id']}/content")
    assert original.content == docx_bytes
    other = {"X-API-Key": "reviewer-local-key"}
    assert (
        api.get(f"/v1/versions/{receipt['version_id']}/content", headers=other).status_code == 404
    )
    assert api.get("/v1/documents", headers=other).json() == []
    route = f"/v1/documents/{receipt['document_id']}/permissions"
    assert api.put(route, json={"user": "reviewer", "allow": True}).is_success
    assert (
        api.get(f"/v1/versions/{receipt['version_id']}/content", headers=other).status_code == 200
    )
    assert api.put(route, json={"user": "reviewer", "allow": False}).is_success
    assert (
        api.get(f"/v1/versions/{receipt['version_id']}/content", headers=other).status_code == 404
    )


@pytest.mark.integration
def test_docling_checkpoint_recovery_does_not_duplicate_chunks(api, docx_bytes):
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from sqlalchemy import func, select

    from backend.config import settings
    from backend.db import Chunk, Job, Run, Version, now, transaction
    from backend.worker import Ingestion, claim, process

    receipt = upload(api, docx_bytes).json()
    job = claim("first-worker")
    with psycopg.connect(
        settings().checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    ) as connection:
        saver = PostgresSaver(connection)
        ingestion = Ingestion(job)
        graph = ingestion.graph(saver)
        config = {"configurable": {"thread_id": ingestion.thread_id}}
        graph.invoke({"version_id": receipt["version_id"]}, config, interrupt_after=["chunk"])
        checkpoint = graph.get_state(config)
        assert checkpoint.next == ("embed",)
        count = checkpoint.values["chunk_count"]
        assert count > 0
        with transaction() as session:
            session.get(Job, job["job_id"]).lease_until = now() - timedelta(seconds=1)
        recovered = claim("replacement-worker")
        assert recovered["fence"] > job["fence"]
        from backend.worker import LostLease

        with transaction() as session, pytest.raises(LostLease):
            ingestion.guard(session)
        process(recovered, saver)
        with transaction() as session:
            assert session.get(Run, receipt["run_id"]).status == "completed"
            assert session.get(Version, receipt["version_id"]).status == "chunked"
            assert session.scalar(select(func.count()).select_from(Chunk)) == count
            assert session.get(Version, receipt["version_id"]).published_at is None
        assert graph.get_state(config).next == ()


@pytest.mark.integration
def test_readiness_requires_real_configured_embedding_adapter(api, docx_bytes, monkeypatch):
    import httpx
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from sqlalchemy import func, select

    from backend.config import settings
    from backend.db import AuditEvent, Embedding, Version, transaction
    from backend.worker import Ingestion, claim, process

    s = settings()
    s.embedding_backend = "openai"
    s.embedding_base_url, s.embedding_model, s.embedding_dimensions = (
        "https://test.invalid/v1",
        "synthetic-test-only",
        8,
    )
    receipt = upload(api, docx_bytes).json()
    real_client = httpx.Client

    def response(request):
        data = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "embedding": [0.1] * 8} for i in range(len(data["input"]))]
            },
        )

    monkeypatch.setattr(
        "backend.embeddings.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(response), **kw),
    )
    job = claim("embedding-worker")
    with psycopg.connect(
        s.checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    ) as connection:
        saver = PostgresSaver(connection)
        process(job, saver)
        with transaction() as session:
            version = session.get(Version, receipt["version_id"])
            assert version.status == "ready"
            assert version.published_at is not None
            assert (
                session.scalar(select(func.count()).select_from(Embedding))
                == version.readiness_manifest["chunks"]
            )
        # Repeating a committed publication is safe if a worker dies before checkpointing it.
        from backend.db import Job

        with transaction() as session:
            session.get(Job, job["job_id"]).status = "leased"
        ingestion = Ingestion(job)
        state = (
            ingestion.graph(saver)
            .get_state({"configurable": {"thread_id": ingestion.thread_id}})
            .values
        )
        ingestion.publish(state)
        with transaction() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "published")
                )
                == 1
            )
        future = api.post(
            f"/v1/documents/{receipt['document_id']}/versions",
            headers={"Idempotency-Key": "future-version"},
            files={"file": ("policy.docx", docx_bytes)},
            data={
                "metadata": json.dumps(
                    {"approval_status": "approved", "effective_from": "2099-01-01"}
                )
            },
        )
        assert future.status_code == 202, future.text
        process(claim("future-worker"), saver)
        listed = api.get("/v1/documents").json()[0]
        assert listed["latest_version"]["revision_number"] == 2
        assert listed["applicable_version_id"] == receipt["version_id"]


@pytest.mark.integration
def test_reprocess_creates_revision_and_reuses_successful_extraction(api, docx_bytes, monkeypatch):
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    from backend.config import settings
    from backend.db import Version, transaction
    from backend.worker import claim, process

    first = upload(api, docx_bytes).json()
    with psycopg.connect(
        settings().checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    ) as connection:
        saver = PostgresSaver(connection)
        process(claim("initial-worker"), saver)
        response = api.post(
            f"/v1/versions/{first['version_id']}/reprocess",
            headers={"Idempotency-Key": "new-processing-revision"},
        )
        assert response.status_code == 202, response.text
        second = response.json()
        assert second["document_id"] == first["document_id"]
        assert second["version_id"] != first["version_id"]

        def no_conversion(*args, **kwargs):
            pytest.fail("Successful canonical extraction should be reused")

        monkeypatch.setattr("backend.worker.convert_batch", no_conversion)
        process(claim("reprocess-worker"), saver)
    with transaction() as session:
        old = session.get(Version, first["version_id"])
        new = session.get(Version, second["version_id"])
        assert new.status == "chunked"
        assert new.revision_kind == "reprocess"
        assert new.replaces_version_id == old.version_id
        assert new.original_object_ref == old.original_object_ref
        assert new.artifact_manifest["batches"] == old.artifact_manifest["batches"]
        assert old.revision_number == 1 and new.revision_number == 2


@pytest.mark.integration
def test_real_pdf_batches_preserve_original_page_locations(api):
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    from backend.config import settings
    from backend.worker import claim, process
    from scripts.demo_data import native_pdf

    s = settings()
    s.pdf_batch_pages, s.ocr_enabled = 1, False
    response = api.post(
        "/v1/documents",
        headers={"Idempotency-Key": "batched-pdf"},
        files={
            "file": (
                "synthetic.pdf",
                native_pdf(
                    [
                        "SYNTHETIC: Customer records are retained seven years.",
                        "SYNTHETIC: Staff access requires manager approval.",
                    ]
                ),
            )
        },
        data={"metadata": "{}"},
    )
    assert response.status_code == 202, response.text
    receipt = response.json()
    with psycopg.connect(
        s.checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    ) as connection:
        process(claim("pdf-worker"), PostgresSaver(connection))
    run = api.get(f"/v1/runs/{receipt['run_id']}").json()
    assert run["status"] == "completed", run
    artifacts = api.get(f"/v1/versions/{receipt['version_id']}/artifacts").json()
    assert len(artifacts["manifest"]["batches"]) == 2
    chunks = api.get(f"/v1/versions/{receipt['version_id']}/chunks").json()["items"]
    assert {source["page_number"] for chunk in chunks for source in chunk["sources"]} == {1, 2}
    assert {
        source["source_locator"]["batch"] for chunk in chunks for source in chunk["sources"]
    } == {"batch-0", "batch-1"}


@pytest.mark.integration
def test_failed_conversion_is_visible_and_never_published(api):
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    from backend.config import settings
    from backend.worker import claim, process

    response = api.post(
        "/v1/documents",
        headers={"Idempotency-Key": "malformed-pdf"},
        files={"file": ("broken.pdf", b"%PDF-1.4\nnot a valid document")},
        data={"metadata": "{}"},
    )
    receipt = response.json()
    with psycopg.connect(
        settings().checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    ) as connection:
        process(claim("failure-worker"), PostgresSaver(connection))
    assert api.get(f"/v1/runs/{receipt['run_id']}").json()["status"] == "failed"
    version = api.get(f"/v1/documents/{receipt['document_id']}/versions").json()[0]
    assert version["published_at"] is None
    assert version["status"] == "failed"
