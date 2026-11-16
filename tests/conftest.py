import os

import pytest


@pytest.fixture
def services():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to use isolated PostgreSQL/MinIO test services")
    import psycopg
    from sqlalchemy.engine import make_url

    from backend import storage
    from backend.bootstrap import main
    from backend.config import settings
    from backend.db import Base, engine

    original = settings()
    test_name = "opzen_assignment_test"
    with psycopg.connect(original.checkpoint_url, autocommit=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", [test_name]
        ).fetchone():
            connection.execute(
                psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(test_name))
            )
    url = (
        make_url(original.database_url)
        .set(database=test_name)
        .render_as_string(hide_password=False)
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        patch.setenv("MINIO_BUCKET", "opzen-assignment-tests")
        patch.setenv("EMBEDDING_BACKEND", "none")
        patch.setenv("LANGFUSE_PUBLIC_KEY", "")
        patch.setenv("LANGFUSE_SECRET_KEY", "")
        settings.cache_clear()
        engine.cache_clear()
        storage.client.cache_clear()
        main()
        with engine().begin() as connection:
            assert engine().url.database == test_name
            names = ", ".join(f'app."{table.name}"' for table in Base.metadata.sorted_tables)
            connection.exec_driver_sql(f"TRUNCATE {names} CASCADE")
        yield
        engine().dispose()
        settings.cache_clear()
        engine.cache_clear()
        storage.client.cache_clear()


@pytest.fixture
def api(services):
    from fastapi.testclient import TestClient

    from backend.api import app

    with TestClient(app, headers={"X-API-Key": "analyst-local-key"}) as client:
        yield client


@pytest.fixture
def docx_bytes():
    import io

    from docx import Document

    doc = Document()
    doc.add_heading("Synthetic retention policy", 0)
    doc.add_heading("Retention", 1)
    doc.add_paragraph("Customer records must be retained for seven years.")
    doc.add_paragraph("No exceptions.")
    table = doc.add_table(rows=2, cols=2)
    for cell, text in zip(
        [c for r in table.rows for c in r.cells], ["Record", "Years", "Customer", "7"], strict=True
    ):
        cell.text = text
    data = io.BytesIO()
    doc.save(data)
    return data.getvalue()
