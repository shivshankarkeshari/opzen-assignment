import os

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import text

from backend.config import settings
from backend.db import Base, engine
from backend.storage import client


def main():
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    with engine().begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS checkpoints"))
        Base.metadata.create_all(connection)
    with psycopg.connect(
        settings().checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    ) as connection:
        PostgresSaver(connection).setup()
    for bucket in [settings().minio_bucket, "langfuse-events", "langfuse-media"]:
        if not client().bucket_exists(bucket):
            client().make_bucket(bucket)
    print("Application tables, LangGraph checkpoints, and MinIO bucket are initialized.")


if __name__ == "__main__":
    main()
