"""Explicit local-only reset of documents and their derived application state."""

import argparse

from minio.deleteobjects import DeleteObject
from sqlalchemy import func, select, text

from backend.config import settings
from backend.db import Base, Document, engine
from backend.storage import client


def reset():
    s = settings()
    assert engine().url.database == "opzen" and engine().url.host in ("localhost", "127.0.0.1")
    assert s.minio_endpoint in ("localhost:59000", "127.0.0.1:59000")
    assert s.minio_bucket == "opzen-documents"
    with engine().begin() as connection:
        count = connection.scalar(select(func.count()).select_from(Document))
        tables = ", ".join(f'app."{table.name}"' for table in Base.metadata.sorted_tables)
        connection.exec_driver_sql(f"TRUNCATE {tables} CASCADE")
        checkpoints = (
            connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='checkpoints' AND tablename != 'checkpoint_migrations'"
                )
            )
            .scalars()
            .all()
        )
        if checkpoints:
            names = ", ".join(f'checkpoints."{name}"' for name in checkpoints)
            connection.exec_driver_sql(f"TRUNCATE {names} CASCADE")
    objects = list(client().list_objects(s.minio_bucket, recursive=True, include_version=True))
    errors = list(
        client().remove_objects(
            s.minio_bucket,
            (DeleteObject(obj.object_name, version_id=obj.version_id) for obj in objects),
        )
    )
    if errors:
        raise RuntimeError(f"Object cleanup failed for {len(errors)} objects")
    print(
        f"Cleared {count} documents and {len(objects)} original/derived objects; application runs and checkpoints reset."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", required=True)
    parser.parse_args()
    reset()
